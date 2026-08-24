# Sentinel infrastructure provisioning.
# The project already exists and billing is already linked, so this script does
# not create or bill a project. It is safe to re-run: every create step tolerates
# an already-exists error.

$ErrorActionPreference = "Continue"

$PROJECT_ID   = "sentinel-506512"
$REGION       = "us-central1"
$BILLING_ACCT = "014F55-71D73D-B2C5A3"

# Gemini 3.5 is only served from the global endpoint. Every other service in this
# project is regional in us-central1. Mixing these up produces a 404 that reads
# like the model does not exist.
$GEMINI_LOCATION = "global"

$PROJECT_NUMBER = gcloud projects describe $PROJECT_ID --format="value(projectNumber)"
Write-Host "Project $PROJECT_ID is number $PROJECT_NUMBER"

Write-Host "Step 1: enable APIs. Service Usage rejects batches larger than 20."
$apisCore = @(
  "cloudresourcemanager.googleapis.com", "serviceusage.googleapis.com",
  "run.googleapis.com", "cloudbuild.googleapis.com", "artifactregistry.googleapis.com",
  "firestore.googleapis.com", "cloudtasks.googleapis.com", "cloudscheduler.googleapis.com",
  "pubsub.googleapis.com", "aiplatform.googleapis.com", "modelarmor.googleapis.com",
  "dlp.googleapis.com", "cloudkms.googleapis.com", "secretmanager.googleapis.com",
  "cloudtrace.googleapis.com", "logging.googleapis.com", "monitoring.googleapis.com",
  "iamcredentials.googleapis.com", "iam.googleapis.com", "storage.googleapis.com"
)
$apisAux = @(
  "billingbudgets.googleapis.com", "gmail.googleapis.com", "sheets.googleapis.com",
  "drive.googleapis.com", "calendar-json.googleapis.com", "chat.googleapis.com"
)
gcloud services enable $apisCore --project=$PROJECT_ID
gcloud services enable $apisAux  --project=$PROJECT_ID

Write-Host "Step 2: budget alert"
# The amount must be denominated in the billing account's own currency. This
# account is INR, so 40USD is rejected as an invalid argument with no hint as to
# which argument. 3500 INR is roughly the intended 40 USD ceiling.
gcloud billing budgets create `
  --billing-account=$BILLING_ACCT `
  --display-name="Sentinel 3500 INR" `
  --budget-amount=3500INR `
  --threshold-rule=percent=0.5 `
  --threshold-rule=percent=0.9 `
  --threshold-rule=percent=1.0 `
  --filter-projects="projects/$PROJECT_NUMBER"

Write-Host "Step 3: Firestore in native mode"
gcloud firestore databases create --location=$REGION --type=firestore-native --project=$PROJECT_ID

Write-Host "Step 4: Artifact Registry for Cloud Run images"
gcloud artifacts repositories create sentinel --repository-format=docker --location=$REGION `
  --description="Sentinel service images" --project=$PROJECT_ID

Write-Host "Step 5: Pub/Sub topics for de-identified events"
gcloud pubsub topics create raw-events --project=$PROJECT_ID
gcloud pubsub topics create raw-events-dlq --project=$PROJECT_ID

Write-Host "Step 6: Cloud Tasks queue for per-obligation timers"
gcloud tasks queues create sentinel-obligations --location=$REGION --project=$PROJECT_ID
gcloud tasks queues update sentinel-obligations --location=$REGION --project=$PROJECT_ID `
  --max-attempts=5 --min-backoff=10s --max-backoff=300s `
  --max-dispatches-per-second=10 --max-concurrent-dispatches=5

Write-Host "Step 7: GCS bucket for inbound documents"
gcloud storage buckets create "gs://$PROJECT_ID-inbound" --location=$REGION `
  --uniform-bucket-level-access --project=$PROJECT_ID

Write-Host "Step 8: KMS key ring and key encryption key"
gcloud kms keyrings create sentinel --location=$REGION --project=$PROJECT_ID
gcloud kms keys create phi-token-kek --keyring=sentinel --location=$REGION `
  --purpose=encryption --project=$PROJECT_ID

Write-Host "Step 9: generate the deterministic tokenisation key, persist only the wrapped form"
# The plaintext AES key exists only in memory and in a temp file that is deleted
# immediately. Only the KMS-wrapped form is ever persisted.
gcloud secrets describe phi-wrapped-key --project=$PROJECT_ID 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  [System.IO.File]::WriteAllBytes("$env:TEMP\sentinel-key.bin", $bytes)

  gcloud kms encrypt --location=$REGION --keyring=sentinel --key=phi-token-kek `
    --plaintext-file="$env:TEMP\sentinel-key.bin" `
    --ciphertext-file="$env:TEMP\sentinel-key.wrapped" --project=$PROJECT_ID

  $wrapped = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes("$env:TEMP\sentinel-key.wrapped"))
  [System.IO.File]::WriteAllText("$env:TEMP\sentinel-key.b64", $wrapped)

  gcloud secrets create phi-wrapped-key --replication-policy=automatic --project=$PROJECT_ID
  gcloud secrets versions add phi-wrapped-key --data-file="$env:TEMP\sentinel-key.b64" --project=$PROJECT_ID

  Remove-Item "$env:TEMP\sentinel-key.bin" -Force -ErrorAction SilentlyContinue
  Remove-Item "$env:TEMP\sentinel-key.wrapped" -Force -ErrorAction SilentlyContinue
  Remove-Item "$env:TEMP\sentinel-key.b64" -Force -ErrorAction SilentlyContinue
  Write-Host "Wrapped key stored as phi-wrapped-key. Plaintext deleted."
} else {
  Write-Host "Secret phi-wrapped-key already exists, leaving it alone."
}

Write-Host "Step 10: let the Sensitive Data Protection service agent unwrap the key"
# The service agent does not exist until it is explicitly generated. The gcloud
# equivalent lives in the beta component, which cannot be installed when the SDK
# sits in Program Files without administrator rights, so call the REST method.
$tok = gcloud auth print-access-token
curl.exe -s -X POST -H "Authorization: Bearer $tok" -H "Content-Type: application/json" -d "{}" `
  "https://serviceusage.googleapis.com/v1beta1/projects/$PROJECT_NUMBER/services/dlp.googleapis.com:generateServiceIdentity" | Out-Null

gcloud kms keys add-iam-policy-binding phi-token-kek --keyring=sentinel --location=$REGION --condition=None --quiet `
  --member="serviceAccount:service-$PROJECT_NUMBER@dlp-api.iam.gserviceaccount.com" `
  --role="roles/cloudkms.cryptoKeyDecrypter" --project=$PROJECT_ID

Write-Host "Step 11: service accounts, one per trust domain"
$accounts = [ordered]@{
  "sentinel-ingest" = "Trust boundary: de-identification and screening"
  "sentinel-engine" = "Ledger, interpreter, coordinator, timers"
  "sentinel-clin"   = "ClinicalFollowUpAgent"
  "sentinel-rev"    = "RevenueCycleAgent"
  "sentinel-path"   = "CarePathwayAgent"
  "sentinel-reid"   = "Re-identification, human callers only"
  "sentinel-web"    = "Continuity Board frontend"
  "sentinel-tasks"  = "Cloud Tasks and Scheduler caller identity"
}
# Service account creation is quota-limited per minute per project and eight in a
# row trips it. A failed create is silent later on, surfacing only as an
# add-iam-policy-binding error saying the account does not exist, so retry here.
foreach ($name in $accounts.Keys) {
  $ok = $false
  for ($i = 0; $i -lt 3 -and -not $ok; $i++) {
    gcloud iam service-accounts create $name --display-name=$accounts[$name] --project=$PROJECT_ID 2>&1 | Out-Null
    gcloud iam service-accounts describe "$name@$PROJECT_ID.iam.gserviceaccount.com" --project=$PROJECT_ID 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $ok = $true } else { Start-Sleep -Seconds 20 }
  }
  if ($ok) { Write-Host "Service account $name ready" } else { Write-Host "FAILED to create $name" }
}

# add-iam-policy-binding reads and rewrites the whole project policy, so calls made
# back to back lose to each other with a concurrent-modification error. Retry rather
# than leaving a role silently ungranted.
function Grant($sa, $roles) {
  foreach ($r in $roles) {
    $ok = $false
    for ($i = 0; $i -lt 4 -and -not $ok; $i++) {
      gcloud projects add-iam-policy-binding $PROJECT_ID `
        --member="serviceAccount:$sa@$PROJECT_ID.iam.gserviceaccount.com" `
        --role=$r --condition=None --quiet | Out-Null
      if ($LASTEXITCODE -eq 0) { $ok = $true } else { Start-Sleep -Seconds 3 }
    }
    if (-not $ok) { Write-Host "FAILED to grant $r to $sa" }
  }
  Write-Host "Granted $($roles.Count) roles to $sa"
}

# Ingest sees raw text and is the only identity allowed to call de-identification.
Grant "sentinel-ingest" @(
  "roles/datastore.user", "roles/pubsub.publisher", "roles/dlp.user",
  "roles/modelarmor.user", "roles/storage.objectViewer",
  "roles/cloudtrace.agent", "roles/logging.logWriter"
)

# Engine owns the ledger and the timers. It calls Gemini. It performs no external action.
Grant "sentinel-engine" @(
  "roles/datastore.user", "roles/aiplatform.user", "roles/modelarmor.user",
  "roles/cloudtasks.enqueuer", "roles/iam.serviceAccountUser",
  "roles/cloudtrace.agent", "roles/logging.logWriter", "roles/monitoring.metricWriter"
)

# The three department agents. Identical project-level roles by design; the real
# separation is the tool allowlist in code, the policy engine, and per-secret IAM
# granted separately by grant-secrets.ps1.
foreach ($agent in @("sentinel-clin", "sentinel-rev", "sentinel-path")) {
  Grant $agent @(
    "roles/datastore.user", "roles/aiplatform.user", "roles/modelarmor.user",
    "roles/cloudtasks.enqueuer", "roles/cloudtrace.agent", "roles/logging.logWriter"
  )
}

# Re-identification is the only identity that may unwrap a token back to a name.
Grant "sentinel-reid" @(
  "roles/dlp.user", "roles/datastore.viewer",
  "roles/cloudtrace.agent", "roles/logging.logWriter"
)

Grant "sentinel-web"   @("roles/datastore.viewer", "roles/logging.logWriter")
Grant "sentinel-tasks" @("roles/cloudtasks.enqueuer", "roles/logging.logWriter")

Write-Host "Step 12: Model Armor template"
gcloud config set api_endpoint_overrides/modelarmor "https://modelarmor.$REGION.rep.googleapis.com/"
gcloud model-armor templates create sentinel-boundary --location=$REGION --project=$PROJECT_ID `
  --pi-and-jailbreak-filter-settings-enforcement=enabled `
  --pi-and-jailbreak-filter-settings-confidence-level=LOW_AND_ABOVE `
  --malicious-uri-filter-settings-enforcement=enabled `
  --basic-config-filter-enforcement=enabled
gcloud config unset api_endpoint_overrides/modelarmor

Write-Host ""
Write-Host "Provisioning complete. These values belong in .env:"
Write-Host "  GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
Write-Host "  GOOGLE_CLOUD_PROJECT_NUMBER=$PROJECT_NUMBER"
Write-Host "  GOOGLE_CLOUD_REGION=$REGION"
Write-Host "  GOOGLE_CLOUD_LOCATION=$GEMINI_LOCATION"
Write-Host "  GEMINI_MODEL=gemini-3.5-flash"
Write-Host "  TASKS_QUEUE=sentinel-obligations"
Write-Host "  PUBSUB_TOPIC=raw-events"
Write-Host "  MODEL_ARMOR_TEMPLATE=sentinel-boundary"
Write-Host "  KMS_KEY=projects/$PROJECT_ID/locations/$REGION/keyRings/sentinel/cryptoKeys/phi-token-kek"
Write-Host "  WRAPPED_KEY_SECRET=phi-wrapped-key"
Write-Host "  INBOUND_BUCKET=$PROJECT_ID-inbound"
