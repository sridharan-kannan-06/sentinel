# Deploy one Sentinel service to Cloud Run.
#
#   powershell -ExecutionPolicy Bypass -File infra/deploy.ps1 -Service engine
#   powershell -ExecutionPolicy Bypass -File infra/deploy.ps1 -Service ingest
#
# Each service builds from its own directory as its own container and runs as its
# own service account, which is what makes the identity separation real rather
# than declarative.

param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("engine", "ingest")]
  [string]$Service
)

$ErrorActionPreference = "Stop"

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$PROJECT_ID = "sentinel-506512"
$REGION     = "us-central1"

$NAME = "sentinel-$Service"
$SA   = "sentinel-$Service@$PROJECT_ID.iam.gserviceaccount.com"

$envFile = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envFile)) { throw "No .env at $envFile" }

$cfg = @{}
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { $cfg[$matches[1]] = $matches[2].Trim() }
}

$gitSha = (git -C $RepoRoot rev-parse --short HEAD)
if (-not $gitSha) { $gitSha = "unknown" }

$common = @(
  "GOOGLE_CLOUD_PROJECT=$($cfg['GOOGLE_CLOUD_PROJECT'])",
  "GOOGLE_CLOUD_PROJECT_NUMBER=$($cfg['GOOGLE_CLOUD_PROJECT_NUMBER'])",
  "GOOGLE_CLOUD_REGION=$($cfg['GOOGLE_CLOUD_REGION'])",
  "GIT_SHA=$gitSha"
)

if ($Service -eq "engine") {
  $pairs = $common + @(
    # Gemini 3.5 answers only on the global endpoint. GOOGLE_GENAI_USE_VERTEXAI
    # is what tells the ADK client to use Vertex rather than the public API.
    "GOOGLE_CLOUD_LOCATION=$($cfg['GOOGLE_CLOUD_LOCATION'])",
    "GOOGLE_GENAI_USE_VERTEXAI=TRUE",
    "GEMINI_MODEL=$($cfg['GEMINI_MODEL'])",
    "TASKS_QUEUE=$($cfg['TASKS_QUEUE'])",
    "TASKS_SERVICE_ACCOUNT=$($cfg['TASKS_SERVICE_ACCOUNT'])",
    "EVIDENCE_GATE=$($cfg['EVIDENCE_GATE'])",
    "NOTIFIER=$($cfg['NOTIFIER'])",
    "NOTIFY_FROM=$($cfg['NOTIFY_FROM'])",
    "NOTIFY_TO=$($cfg['NOTIFY_TO'])",
    "NOTIFY_SHEET_ID=$($cfg['NOTIFY_SHEET_ID'])",
    "GMAIL_OAUTH_SECRET=$($cfg['GMAIL_OAUTH_SECRET'])"
  )
  $memory = "1Gi"
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
  --source "$RepoRoot\services\$Service" `
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

if ($Service -eq "engine") {
  Write-Host "Patching ENGINE_BASE_URL so the service can enqueue its own timers."
  gcloud run services update $NAME --project=$PROJECT_ID --region=$REGION `
    --update-env-vars="ENGINE_BASE_URL=$url" --quiet | Out-Null

  $envText = Get-Content $envFile -Raw
  if ($envText -match '(?m)^ENGINE_BASE_URL=.*$') {
    $envText = $envText -replace '(?m)^ENGINE_BASE_URL=.*$', "ENGINE_BASE_URL=$url"
  } else {
    $envText = $envText.TrimEnd() + "`nENGINE_BASE_URL=$url`n"
  }
  [System.IO.File]::WriteAllText($envFile, $envText)
} else {
  $envText = Get-Content $envFile -Raw
  if ($envText -match '(?m)^INGEST_BASE_URL=.*$') {
    $envText = $envText -replace '(?m)^INGEST_BASE_URL=.*$', "INGEST_BASE_URL=$url"
  } else {
    $envText = $envText.TrimEnd() + "`nINGEST_BASE_URL=$url`n"
  }
  [System.IO.File]::WriteAllText($envFile, $envText)
}

# The health endpoint is /health and deliberately not /healthz. Google's edge
# intercepts /healthz on run.app hostnames and answers it with its own 404 page
# before the request reaches the container, which looks exactly like a service
# that failed to deploy. Every other path routes normally.
Write-Host "Checking health."
$healthy = $false
for ($i = 1; $i -le 20 -and -not $healthy; $i++) {
  $code = curl.exe -s -o "$env:TEMP\sentinel-hz.json" -w "%{http_code}" --max-time 20 "$url/health"
  if ($code -eq "200") {
    $healthy = $true
    Write-Host "Healthy after $i attempt(s):"
    Get-Content "$env:TEMP\sentinel-hz.json"
  } else {
    Write-Host "  attempt $i returned $code"
    Start-Sleep -Seconds 15
  }
}
Write-Host ""
if (-not $healthy) {
  Write-Host "No 200 from this machine. If the code is 000 your local resolver is"
  Write-Host "refusing *.run.app; check from inside Google Cloud before assuming"
  Write-Host "the service is broken."
}
