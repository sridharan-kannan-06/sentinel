# Log based metrics and the operations dashboard.
#
# These are derived from the structured logs the services already emit rather
# than from a separate metrics path. One source of truth means a number on the
# dashboard can always be traced back to the log line that produced it, which is
# the whole point of an audit trail.
#
# Every metric here counts something a person would actually act on. There is no
# request rate and no latency percentile, because nobody watching a continuity
# system is asking how fast it is.

$ErrorActionPreference = "Continue"

$PROJECT_ID = "sentinel-506512"

# The filters contain spaces, quotes and equals signs. Passing them as inline
# arguments lets PowerShell split them before gcloud ever sees them, which fails
# with "unrecognized arguments" naming a fragment of the filter. A config file
# sidesteps the whole quoting problem.
function MakeMetric($name, $description, $filter) {
  $config = Join-Path $env:TEMP "sentinel-metric-$name.yaml"
  $yaml = @(
    "description: $description",
    "filter: |-",
    "  $filter"
  ) -join "`n"
  [System.IO.File]::WriteAllText($config, $yaml)

  gcloud logging metrics describe $name --project=$PROJECT_ID 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) {
    gcloud logging metrics update $name --project=$PROJECT_ID `
      --config-from-file=$config --quiet | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "updated  $name" } else { Write-Host "FAILED   $name" }
  } else {
    gcloud logging metrics create $name --project=$PROJECT_ID `
      --config-from-file=$config --quiet | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "created  $name" } else { Write-Host "FAILED   $name" }
  }
  Remove-Item $config -Force -ErrorAction SilentlyContinue
}

MakeMetric "sentinel_obligations_created" `
  "Obligations admitted to the ledger" `
  'resource.type="cloud_run_revision" AND jsonPayload.message="obligation created"'

MakeMetric "sentinel_checkpoints_fired" `
  "SLA checkpoints that woke and acted" `
  'resource.type="cloud_run_revision" AND jsonPayload.message="wake handled"'

MakeMetric "sentinel_policy_denials" `
  "Actions refused by the policy engine" `
  'resource.type="cloud_run_revision" AND (jsonPayload.message="agent refused an action its own policy does not permit" OR jsonPayload.message="policy denied a routed action")'

MakeMetric "sentinel_evidence_rejections" `
  "Closure attempts refused by the Evidence Gate" `
  'resource.type="cloud_run_revision" AND jsonPayload.message="evidence rejected"'

MakeMetric "sentinel_obligations_closed" `
  "Obligations closed on qualifying evidence" `
  'resource.type="cloud_run_revision" AND jsonPayload.message="obligation closed on evidence"'

MakeMetric "sentinel_escalations" `
  "Escalation rungs notified" `
  'resource.type="cloud_run_revision" AND jsonPayload.message="escalation rung notified"'

MakeMetric "sentinel_sweep_repairs" `
  "Checkpoints the reconciliation sweep re-armed after a lost timer" `
  'resource.type="cloud_run_revision" AND jsonPayload.message="sweep repaired a missed checkpoint"'

MakeMetric "sentinel_boundary_blocks" `
  "Inbound payloads Model Armor refused at the trust boundary" `
  'resource.type="cloud_run_revision" AND jsonPayload.message="event blocked at the trust boundary and not forwarded"'

MakeMetric "sentinel_reidentifications" `
  "Every time a token was resolved back to a name" `
  'resource.type="cloud_run_revision" AND jsonPayload.message="re-identification requested"'

Write-Host ""
Write-Host "Creating the dashboard"
$dashboard = Join-Path $PSScriptRoot "monitoring-dashboard.json"
$existing = gcloud monitoring dashboards list --project=$PROJECT_ID `
  --filter="displayName='Sentinel Continuity'" --format="value(name)" 2>$null
if ($existing) {
  gcloud monitoring dashboards update $existing --config-from-file=$dashboard --project=$PROJECT_ID --quiet
  Write-Host "updated the existing dashboard"
} else {
  gcloud monitoring dashboards create --config-from-file=$dashboard --project=$PROJECT_ID --quiet
  Write-Host "created the dashboard"
}

Write-Host ""
Write-Host "A log based metric only counts entries written after it exists, so"
Write-Host "these start from zero and fill in as the system runs."
