# Deploy one Sentinel service to Cloud Run.
#
#   infra/deploy.ps1 -Service engine
#   infra/deploy.ps1 -Service ingest
#   infra/deploy.ps1 -Service clin     ClinicalFollowUpAgent
#   infra/deploy.ps1 -Service rev      RevenueCycleAgent
#   infra/deploy.ps1 -Service path     CarePathwayAgent
#
# The three agents are one image deployed three times. What differs is the
# service account it runs as and the AGENT_ROLE it declares, and the container
# refuses to start if those two disagree.
#
# Each build is staged into a temporary directory so that the canonical
# policy.yaml and the shared modules can be copied in alongside the service's
# own files. Docker cannot reach above its build context, and duplicating the
# authority model into three directories would let it drift.

param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("engine", "ingest", "clin", "rev", "path")]
  [string]$Service
)

$ErrorActionPreference = "Stop"

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$PROJECT_ID = "sentinel-506512"
$REGION     = "us-central1"

$AGENT_ROLES = @{
  "clin" = "clinical_followup"
  "rev"  = "revenue_cycle"
  "path" = "care_pathway"
}
$IsAgent   = $AGENT_ROLES.ContainsKey($Service)
$SourceDir = if ($IsAgent) { "agents" } else { $Service }
$NAME      = "sentinel-$Service"
$SA        = "sentinel-$Service@$PROJECT_ID.iam.gserviceaccount.com"

$envFile = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envFile)) { throw "No .env at $envFile" }

$cfg = @{}
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { $cfg[$matches[1]] = $matches[2].Trim() }
}

$gitSha = (git -C $RepoRoot rev-parse --short HEAD)
if (-not $gitSha) { $gitSha = "unknown" }

Write-Host "Staging the build directory for $NAME"
$stage = Join-Path $env:TEMP "sentinel-build-$Service"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

Get-ChildItem "$RepoRoot\services\$SourceDir" -File |
  Where-Object { $_.Name -notlike "test_*" } |
  ForEach-Object { Copy-Item $_.FullName $stage -Force }

# Every configuration file travels with the services that enforce it: policy.yaml
# for authority, evidence.yaml for what counts as proof, escalation.yaml for who
# hears about it. Copying only policy.yaml leaves the Evidence Gate with no rules,
# which it correctly reports by refusing to close anything at all.
Get-ChildItem "$RepoRoot\policy" -Filter *.yaml -File |
  ForEach-Object { Copy-Item $_.FullName $stage -Force }

if ($IsAgent) {
  # The engine holds the canonical copies. Staging them here rather than keeping
  # duplicates in the repository means the agents cannot enforce a different
  # policy than the coordinator applied.
  foreach ($module in @("policy.py", "logs.py", "notify.py")) {
    Copy-Item "$RepoRoot\services\engine\$module" $stage -Force
  }
}

$common = @(
  "GOOGLE_CLOUD_PROJECT=$($cfg['GOOGLE_CLOUD_PROJECT'])",
  "GOOGLE_CLOUD_PROJECT_NUMBER=$($cfg['GOOGLE_CLOUD_PROJECT_NUMBER'])",
  "GOOGLE_CLOUD_REGION=$($cfg['GOOGLE_CLOUD_REGION'])",
  "GIT_SHA=$gitSha"
)
$notifyVars = @(
  "NOTIFIER=$($cfg['NOTIFIER'])",
  "NOTIFY_FROM=$($cfg['NOTIFY_FROM'])",
  "NOTIFY_TO=$($cfg['NOTIFY_TO'])",
  "NOTIFY_SHEET_ID=$($cfg['NOTIFY_SHEET_ID'])",
  "GMAIL_OAUTH_SECRET=$($cfg['GMAIL_OAUTH_SECRET'])"
)

if ($Service -eq "engine") {
  $pairs = $common + $notifyVars + @(
    # Gemini 3.5 answers only on the global endpoint, and
    # GOOGLE_GENAI_USE_VERTEXAI is what points the ADK client at Vertex.
    "GOOGLE_CLOUD_LOCATION=$($cfg['GOOGLE_CLOUD_LOCATION'])",
    "GOOGLE_GENAI_USE_VERTEXAI=TRUE",
    "GEMINI_MODEL=$($cfg['GEMINI_MODEL'])",
    "TASKS_QUEUE=$($cfg['TASKS_QUEUE'])",
    "TASKS_SERVICE_ACCOUNT=$($cfg['TASKS_SERVICE_ACCOUNT'])",
    "EVIDENCE_GATE=$($cfg['EVIDENCE_GATE'])",
    "AGENT_CLIN_URL=$($cfg['AGENT_CLIN_URL'])",
    "AGENT_REV_URL=$($cfg['AGENT_REV_URL'])",
    "AGENT_PATH_URL=$($cfg['AGENT_PATH_URL'])"
  )
  $memory = "1Gi"
} elseif ($IsAgent) {
  $pairs = $common + $notifyVars + @(
    "AGENT_ROLE=$($AGENT_ROLES[$Service])",
    "ENGINE_BASE_URL=$($cfg['ENGINE_BASE_URL'])"
  )
  $memory = "512Mi"
} else {
  $pairs = $common + @(
    "PUBSUB_TOPIC=$($cfg['PUBSUB_TOPIC'])",
    "MODEL_ARMOR_TEMPLATE=$($cfg['MODEL_ARMOR_TEMPLATE'])",
    "KMS_KEY=$($cfg['KMS_KEY'])",
    "WRAPPED_KEY_SECRET=$($cfg['WRAPPED_KEY_SECRET'])"
  )
  $memory = "512Mi"
}
$envArg = $pairs -join ","

Write-Host "Deploying $NAME as $SA. A source deploy runs Cloud Build and takes"
Write-Host "three to five minutes. Do not interrupt it."

gcloud run deploy $NAME `
  --source $stage `
  --project=$PROJECT_ID `
  --region=$REGION `
  --service-account=$SA `
  --min-instances=0 `
  --max-instances=2 `
  --memory=$memory `
  --cpu=1 `
  --timeout=300 `
  --allow-unauthenticated `
  --set-env-vars=$envArg `
  --quiet

if ($LASTEXITCODE -ne 0) { throw "deploy failed" }

# status.url reports a legacy hostname that is not always the one that routes.
# The urls annotation lists every hostname the service answers on.
$annotation = gcloud run services describe $NAME --project=$PROJECT_ID --region=$REGION `
  --format="value(metadata.annotations['run.googleapis.com/urls'])"
$urls = @()
if ($annotation) { $urls = ($annotation | ConvertFrom-Json) }
$url = $urls | Where-Object { $_ -like "*.$REGION.run.app" } | Select-Object -First 1
if (-not $url) { $url = $urls | Select-Object -First 1 }
Write-Host "Service URL is $url"

function SetEnvValue($key, $value) {
  $text = Get-Content $envFile -Raw
  if ($text -match "(?m)^$key=.*$") {
    $text = $text -replace "(?m)^$key=.*$", "$key=$value"
  } else {
    $text = $text.TrimEnd() + "`n$key=$value`n"
  }
  [System.IO.File]::WriteAllText($envFile, $text)
}

switch ($Service) {
  "engine" {
    Write-Host "Patching ENGINE_BASE_URL so the service can enqueue its own timers."
    gcloud run services update $NAME --project=$PROJECT_ID --region=$REGION `
      --update-env-vars="ENGINE_BASE_URL=$url" --quiet | Out-Null
    SetEnvValue "ENGINE_BASE_URL" $url
  }
  "ingest" { SetEnvValue "INGEST_BASE_URL" $url }
  "clin"   { SetEnvValue "AGENT_CLIN_URL" $url }
  "rev"    { SetEnvValue "AGENT_REV_URL" $url }
  "path"   { SetEnvValue "AGENT_PATH_URL" $url }
}

# The health endpoint is /health and deliberately not /healthz. Google's edge
# intercepts /healthz on run.app hostnames and answers it with its own 404 page
# before the request reaches the container, which looks exactly like a service
# that failed to deploy.
Write-Host "Checking health."
$healthy = $false
for ($i = 1; $i -le 8 -and -not $healthy; $i++) {
  $code = curl.exe -s -o "$env:TEMP\sentinel-hz.json" -w "%{http_code}" --max-time 20 "$url/health"
  if ($code -eq "200") {
    $healthy = $true
    Write-Host "Healthy after $i attempt(s):"
    Get-Content "$env:TEMP\sentinel-hz.json"
  } else {
    Write-Host "  attempt $i returned $code"
    Start-Sleep -Seconds 10
  }
}
Write-Host ""
if (-not $healthy) {
  Write-Host "No 200 from this machine. A code of 000 means the local resolver is"
  Write-Host "refusing *.run.app, not that the service is broken. Verify from inside"
  Write-Host "Google Cloud before concluding anything."
}
