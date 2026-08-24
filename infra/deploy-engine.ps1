# Deploy services/engine to Cloud Run.
#
# The service needs its own URL in ENGINE_BASE_URL so it can address itself when
# enqueuing Cloud Tasks, and that URL does not exist until the first deploy has
# happened. Rather than pay for a second container build, the first deploy goes
# out without it and a services update patches the variable in afterwards.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PROJECT_ID = "sentinel-506512"
$REGION     = "us-central1"
$SERVICE    = "sentinel-engine"
$SA         = "sentinel-engine@$PROJECT_ID.iam.gserviceaccount.com"

$envFile = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envFile)) {
  throw "No .env at $envFile. Copy .env.example and fill it in."
}

$cfg = @{}
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { $cfg[$matches[1]] = $matches[2].Trim() }
}

$gitSha = (git -C $RepoRoot rev-parse --short HEAD)
if (-not $gitSha) { $gitSha = "unknown" }

$pairs = @(
  "GOOGLE_CLOUD_PROJECT=$($cfg['GOOGLE_CLOUD_PROJECT'])",
  "GOOGLE_CLOUD_PROJECT_NUMBER=$($cfg['GOOGLE_CLOUD_PROJECT_NUMBER'])",
  "GOOGLE_CLOUD_REGION=$($cfg['GOOGLE_CLOUD_REGION'])",
  "GOOGLE_CLOUD_LOCATION=$($cfg['GOOGLE_CLOUD_LOCATION'])",
  "GEMINI_MODEL=$($cfg['GEMINI_MODEL'])",
  "TASKS_QUEUE=$($cfg['TASKS_QUEUE'])",
  "TASKS_SERVICE_ACCOUNT=$($cfg['TASKS_SERVICE_ACCOUNT'])",
  "EVIDENCE_GATE=$($cfg['EVIDENCE_GATE'])",
  "GIT_SHA=$gitSha"
)
$envArg = $pairs -join ","

Write-Host "Deploying $SERVICE from source. A source deploy runs Cloud Build and"
Write-Host "routinely takes three to five minutes. Do not interrupt it."

gcloud run deploy $SERVICE `
  --source "$RepoRoot\services\engine" `
  --project=$PROJECT_ID `
  --region=$REGION `
  --service-account=$SA `
  --min-instances=0 `
  --max-instances=2 `
  --memory=512Mi `
  --cpu=1 `
  --timeout=120 `
  --allow-unauthenticated `
  --set-env-vars=$envArg `
  --quiet

if ($LASTEXITCODE -ne 0) { throw "deploy failed" }

# status.url reports the legacy SERVICE-HASH-REGIONCODE.a.run.app form, which is
# not always the hostname that actually routes. The urls annotation lists every
# hostname the service answers on; prefer the current
# SERVICE-PROJECTNUMBER.REGION.run.app form and fall back only if it is absent.
$annotation = gcloud run services describe $SERVICE --project=$PROJECT_ID --region=$REGION `
  --format="value(metadata.annotations['run.googleapis.com/urls'])"
$urls = @()
if ($annotation) { $urls = ($annotation | ConvertFrom-Json) }

$url = $urls | Where-Object { $_ -like "*.$REGION.run.app" } | Select-Object -First 1
if (-not $url) { $url = $urls | Select-Object -First 1 }
if (-not $url) {
  $url = gcloud run services describe $SERVICE --project=$PROJECT_ID --region=$REGION --format="value(status.url)"
}
Write-Host "Service URL is $url"
Write-Host "All hostnames: $($urls -join ', ')"

Write-Host "Patching ENGINE_BASE_URL so the service can enqueue its own timers."
gcloud run services update $SERVICE `
  --project=$PROJECT_ID --region=$REGION `
  --update-env-vars="ENGINE_BASE_URL=$url" --quiet | Out-Null

# Persist the URL so start-canary.ps1 and the Continuity Board do not have to
# rediscover it.
$envText = Get-Content $envFile -Raw
if ($envText -match '(?m)^ENGINE_BASE_URL=.*$') {
  $envText = $envText -replace '(?m)^ENGINE_BASE_URL=.*$', "ENGINE_BASE_URL=$url"
} else {
  $envText = $envText.TrimEnd() + "`nENGINE_BASE_URL=$url`n"
}
[System.IO.File]::WriteAllText($envFile, $envText)

Write-Host ""
Write-Host "Deployed. Waiting for the URL to begin routing."
# A newly created service answers 404 at the frontend for a short while after the
# revision reports Ready, so a single health check straight after deploy is not
# evidence of anything. Poll.
$healthy = $false
for ($i = 1; $i -le 20 -and -not $healthy; $i++) {
  $code = curl.exe -s -o "$env:TEMP\sentinel-hz.json" -w "%{http_code}" --max-time 20 "$url/healthz"
  if ($code -eq "200") {
    $healthy = $true
    Write-Host "Healthy after $i attempt(s):"
    Get-Content "$env:TEMP\sentinel-hz.json"
  } else {
    Write-Host "  attempt $i returned $code"
    Start-Sleep -Seconds 15
  }
}
if (-not $healthy) {
  Write-Host ""
  Write-Host "Service did not answer 200 within about five minutes. The revision may"
  Write-Host "still be fine: check 'gcloud run revisions list' and the request logs."
  exit 1
}
Write-Host ""
