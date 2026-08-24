# Start the canary.
#
# This opens one real insurance pre-authorisation obligation on the deployed
# service with SLA checkpoints days into the future, and lets it run until
# submission. The point is that no clock is simulated anywhere: Cloud Tasks fires
# on real dates and the elapsed time in the ledger is real elapsed time.
#
# Run this once. Running it again opens a second canary rather than restarting
# the first.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $RepoRoot ".env"

$cfg = @{}
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { $cfg[$matches[1]] = $matches[2].Trim() }
}

$base = $cfg['ENGINE_BASE_URL']
if (-not $base) { throw "ENGINE_BASE_URL is empty in .env. Run infra/deploy-engine.ps1 first." }

# Checkpoints land on 26, 28 and 30 August. Times are UTC and deliberately in the
# working day in IST, because a nudge that fires at three in the morning is not a
# nudge anyone would act on.
$nudge    = [datetime]::new(2026, 8, 26, 5, 30, 0, [System.DateTimeKind]::Utc)
$breach   = [datetime]::new(2026, 8, 28, 5, 30, 0, [System.DateTimeKind]::Utc)
$escalate = [datetime]::new(2026, 8, 30, 5, 30, 0, [System.DateTimeKind]::Utc)

function IsoUtc($dt) { return $dt.ToString("yyyy-MM-ddTHH:mm:ssZ") }

# The subject token is hardcoded for this phase. From Phase 1 onward tokens come
# out of Sensitive Data Protection at the trust boundary and no caller chooses one.
$payload = @{
  type               = "insurance_preauthorisation"
  subject_token      = "PT-a94f"
  owner_role         = "revenue_cycle"
  owner_id           = "STAFF-billing-01"
  created_from_event = "canary:manual-start"
  deadline           = IsoUtc $breach
  required_evidence  = "Insurer pre-authorisation reference number issued for the subject"
  risk_tier          = "T2"
  blocked_by         = @()
  checkpoints        = @(
    @{ kind = "nudge";    at = IsoUtc $nudge },
    @{ kind = "breach";   at = IsoUtc $breach },
    @{ kind = "escalate"; at = IsoUtc $escalate }
  )
} | ConvertTo-Json -Depth 6

$tmp = Join-Path $env:TEMP "sentinel-canary.json"
[System.IO.File]::WriteAllText($tmp, $payload)

Write-Host "Opening the canary obligation against $base"
$responseFile = Join-Path $env:TEMP "sentinel-canary-response.json"
$code = curl.exe -s -o $responseFile -w "%{http_code}" --max-time 60 `
  -X POST -H "Content-Type: application/json" --data-binary "@$tmp" "$base/obligations"

if ($code -ne "201") {
  Write-Host "Create failed with HTTP $code"
  Get-Content $responseFile -ErrorAction SilentlyContinue
  exit 1
}

$created = Get-Content $responseFile -Raw | ConvertFrom-Json
$id = $created.obligation.id
Write-Host ""
Write-Host "Canary obligation id: $id"
Write-Host "Status:               $($created.obligation.status)"
Write-Host "Deadline:             $($created.obligation.deadline)"
Write-Host "Timers enqueued:      $($created.tasks.Count)"
foreach ($t in $created.tasks) { Write-Host "  $t" }

# Record the id so the demo and the Continuity Board can point at it later
# without anyone having to remember it.
$canaryFile = Join-Path $RepoRoot "docs\CANARY.md"
$started = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$lines = @(
  "# Canary",
  "",
  "One real obligation, opened once and left running until submission. Nothing",
  "about its timing is simulated.",
  "",
  "- Obligation id: ``$id``",
  "- Opened at: $started UTC",
  "- Checkpoints: $(IsoUtc $nudge), $(IsoUtc $breach), $(IsoUtc $escalate)",
  "- Service: $base",
  "",
  "Inspect it with:",
  "",
  '```powershell',
  "curl.exe -s $base/obligations/$id",
  '```'
)
[System.IO.File]::WriteAllLines($canaryFile, $lines)
Write-Host ""
Write-Host "Recorded in docs/CANARY.md"
