# Wire the de-identified event topic to the engine's interpreter.
#
# Push rather than pull, so the engine stays scale-to-zero and only wakes when an
# event actually arrives. The subscription authenticates as sentinel-tasks with
# an OIDC token, which means the engine can be closed to unauthenticated callers
# later without rewiring anything.

$ErrorActionPreference = "Continue"

$PROJECT_ID     = "sentinel-506512"
$PROJECT_NUMBER = "60712078658"
$REGION         = "us-central1"
$TASKS_SA       = "sentinel-tasks@$PROJECT_ID.iam.gserviceaccount.com"
$PUBSUB_AGENT   = "service-$PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com"

$envFile = Join-Path (Split-Path -Parent $PSScriptRoot) ".env"
$cfg = @{}
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { $cfg[$matches[1]] = $matches[2].Trim() }
}
$engine = $cfg['ENGINE_BASE_URL']
if (-not $engine) { throw "ENGINE_BASE_URL is empty in .env. Deploy the engine first." }

Write-Host "Step 1: create the Pub/Sub service identity"
# The agent does not exist until it is generated, and without it the token
# creator binding below fails with a message about a missing account.
$tok = gcloud auth print-access-token
curl.exe -s -X POST -H "Authorization: Bearer $tok" -H "Content-Type: application/json" -d "{}" `
  "https://serviceusage.googleapis.com/v1beta1/projects/$PROJECT_NUMBER/services/pubsub.googleapis.com:generateServiceIdentity" | Out-Null

Write-Host "Step 2: let Pub/Sub mint OIDC tokens as sentinel-tasks"
gcloud iam service-accounts add-iam-policy-binding $TASKS_SA `
  --project=$PROJECT_ID `
  --member="serviceAccount:$PUBSUB_AGENT" `
  --role="roles/iam.serviceAccountTokenCreator" `
  --condition=None --quiet | Out-Null

Write-Host "Step 3: let sentinel-tasks invoke the engine"
gcloud run services add-iam-policy-binding sentinel-engine `
  --project=$PROJECT_ID --region=$REGION `
  --member="serviceAccount:$TASKS_SA" `
  --role="roles/run.invoker" --condition=None --quiet | Out-Null

Write-Host "Step 4: dead letter permissions"
gcloud pubsub topics add-iam-policy-binding raw-events-dlq `
  --project=$PROJECT_ID `
  --member="serviceAccount:$PUBSUB_AGENT" `
  --role="roles/pubsub.publisher" --quiet | Out-Null

Write-Host "Step 5: push subscription to the interpreter"
# The ack deadline is generous because interpreting an event involves a model
# call. A short deadline would redeliver the event while the first attempt was
# still working, and produce duplicate obligations.
gcloud pubsub subscriptions create raw-events-to-engine `
  --project=$PROJECT_ID `
  --topic=raw-events `
  --push-endpoint="$engine/interpret" `
  --push-auth-service-account=$TASKS_SA `
  --push-auth-token-audience=$engine `
  --ack-deadline=180 `
  --dead-letter-topic=raw-events-dlq `
  --max-delivery-attempts=5

gcloud pubsub subscriptions update raw-events-to-engine `
  --project=$PROJECT_ID `
  --push-endpoint="$engine/interpret" `
  --push-auth-service-account=$TASKS_SA `
  --push-auth-token-audience=$engine `
  --ack-deadline=180 | Out-Null

gcloud pubsub subscriptions add-iam-policy-binding raw-events-to-engine `
  --project=$PROJECT_ID `
  --member="serviceAccount:$PUBSUB_AGENT" `
  --role="roles/pubsub.subscriber" --quiet | Out-Null

Write-Host ""
Write-Host "Subscription raw-events-to-engine pushes to $engine/interpret"
