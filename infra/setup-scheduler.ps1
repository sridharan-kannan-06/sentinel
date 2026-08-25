# The reconciliation sweep.
#
# Cloud Tasks gives each obligation its own timer, which is what makes per-entity
# wake-ups possible at all. Tasks can still be lost, so this cron job asks the
# engine every fifteen minutes whether any obligation has a checkpoint in the
# past that never fired, and re-arms it.
#
# The two mechanisms are deliberately different in kind. The timer knows about
# one obligation and nothing else; the sweep knows about all of them and trusts
# none of the timers. Losing a task therefore costs one sweep interval rather
# than the obligation.

$ErrorActionPreference = "Continue"

$PROJECT_ID = "sentinel-506512"
$REGION     = "us-central1"
$TASKS_SA   = "sentinel-tasks@$PROJECT_ID.iam.gserviceaccount.com"
$JOB        = "sentinel-reconcile"

$envFile = Join-Path (Split-Path -Parent $PSScriptRoot) ".env"
$cfg = @{}
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { $cfg[$matches[1]] = $matches[2].Trim() }
}
$engine = $cfg['ENGINE_BASE_URL']
if (-not $engine) { throw "ENGINE_BASE_URL is empty in .env. Deploy the engine first." }

$existing = gcloud scheduler jobs describe $JOB --location=$REGION --project=$PROJECT_ID 2>$null
if ($LASTEXITCODE -eq 0) {
  Write-Host "Updating the existing $JOB job"
  gcloud scheduler jobs update http $JOB `
    --location=$REGION --project=$PROJECT_ID `
    --schedule="*/15 * * * *" `
    --uri="$engine/reconcile" `
    --http-method=POST `
    --oidc-service-account-email=$TASKS_SA `
    --oidc-token-audience=$engine `
    --attempt-deadline=120s
} else {
  Write-Host "Creating the $JOB job"
  gcloud scheduler jobs create http $JOB `
    --location=$REGION --project=$PROJECT_ID `
    --schedule="*/15 * * * *" `
    --uri="$engine/reconcile" `
    --http-method=POST `
    --oidc-service-account-email=$TASKS_SA `
    --oidc-token-audience=$engine `
    --attempt-deadline=120s `
    --description="Re-arms obligation checkpoints whose Cloud Task was lost"
}

Write-Host ""
Write-Host "Sweep runs every fifteen minutes against $engine/reconcile"
Write-Host "Force one now with:"
Write-Host "  gcloud scheduler jobs run $JOB --location=$REGION --project=$PROJECT_ID"
