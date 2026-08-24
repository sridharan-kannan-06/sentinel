# Per-secret IAM, granted one secret at a time rather than project wide.
#
# Project level roles would let every service read every secret, which would make
# the separation between the agents a matter of code style rather than of
# identity. These bindings are what make it real: only the ingest boundary can
# unwrap the tokenisation key, and only the revenue agent can read the outbound
# mail credential.

$ErrorActionPreference = "Continue"

$PROJECT_ID = "sentinel-506512"

function GrantSecret($secret, $account) {
  $exists = gcloud secrets describe $secret --project=$PROJECT_ID 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Secret $secret does not exist yet, skipping $account"
    return
  }
  gcloud secrets add-iam-policy-binding $secret `
    --project=$PROJECT_ID `
    --member="serviceAccount:$account@$PROJECT_ID.iam.gserviceaccount.com" `
    --role="roles/secretmanager.secretAccessor" `
    --condition=None --quiet | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Write-Host "Granted $account read on $secret"
  } else {
    Write-Host "FAILED to grant $account read on $secret"
  }
}

# The tokenisation key. Ingest wraps identifiers on the way in; reid is the only
# identity permitted to reverse one, and only for an authenticated human.
GrantSecret "phi-wrapped-key" "sentinel-ingest"
GrantSecret "phi-wrapped-key" "sentinel-reid"

# Outbound mail. The engine sends SLA notifications; the revenue agent sends
# external correspondence to insurers. No clinical identity may read this.
GrantSecret "gmail-oauth" "sentinel-engine"
GrantSecret "gmail-oauth" "sentinel-rev"
