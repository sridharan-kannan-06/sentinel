# Run one service on this machine against the real Google Cloud backends.
#
#   powershell -ExecutionPolicy Bypass -File infra/run-local.ps1 -Service web
#   powershell -ExecutionPolicy Bypass -File infra/run-local.ps1 -Service engine
#
# There is no emulator path. The engine talks to Firestore, Cloud Tasks and
# Vertex AI, and the boundary talks to Sensitive Data Protection and Model Armor,
# so a local run still needs a provisioned project and application default
# credentials. What runs locally is the code, not the infrastructure.
#
# This script exists because the services read their configuration from the
# environment, and uvicorn does not read .env. Starting one by hand without the
# environment produces a service whose /health endpoint answers perfectly well
# and whose every Firestore call fails with RESOURCE_PROJECT_INVALID.

param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("engine", "ingest", "reid", "web")]
  [string]$Service,

  [int]$Port = 0
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$envFile  = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envFile)) {
  throw "No .env at $envFile. Copy .env.example and fill it in, or run infra/setup-gcp.ps1 first."
}

$cfg = @{}
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') {
    $key = $matches[1]; $value = $matches[2].Trim()
    $cfg[$key] = $value
    Set-Item -Path "env:$key" -Value $value
  }
}

# Cloud Run answers on two hostnames per service and some networks resolve only
# the older one. Anything running here that calls a deployed service has to use
# whichever this machine can actually reach.
if ($cfg['ENGINE_LOCAL_URL']) { $env:ENGINE_BASE_URL = $cfg['ENGINE_LOCAL_URL'] }
if ($cfg['REID_LOCAL_URL'])   { $env:REID_BASE_URL   = $cfg['REID_LOCAL_URL'] }

$adc = Join-Path $env:APPDATA "gcloud\application_default_credentials.json"
if (-not (Test-Path $adc)) {
  Write-Host "No application default credentials found."
  Write-Host "Run this once, then try again:"
  Write-Host "    gcloud auth application-default login"
  exit 1
}

if ($Service -eq "web") {
  if ($Port -eq 0) { $Port = 3000 }
  Write-Host "Continuity Board on http://localhost:$Port"
  Write-Host "Reading from the deployed engine at $env:ENGINE_BASE_URL"
  Write-Host ""
  Set-Location (Join-Path $RepoRoot "web")
  if (-not (Test-Path "node_modules")) {
    Write-Host "Installing dependencies, this happens once."
    npm.cmd install --no-audit --no-fund
  }
  npm.cmd run dev -- --port $Port
  exit $LASTEXITCODE
}

if ($Port -eq 0) { $Port = 8080 }

$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  Write-Host "No virtual environment found. Create it once with:"
  Write-Host "    python -m venv .venv"
  Write-Host "    .venv\Scripts\python.exe -m pip install -r requirements-dev.txt"
  exit 1
}

# The engine's interpreter and coordinator reach Gemini through Vertex rather
# than the public API, and Gemini 3.5 answers only on the global endpoint.
if ($Service -eq "engine") {
  $env:GOOGLE_GENAI_USE_VERTEXAI = "TRUE"
}

Write-Host "sentinel-$Service on http://localhost:$Port"
Write-Host "Project $($env:GOOGLE_CLOUD_PROJECT), reading the real Firestore ledger."
Write-Host "Health check: curl.exe -s http://localhost:$Port/health"
Write-Host ""

Set-Location (Join-Path $RepoRoot "services\$Service")
& $python -m uvicorn main:app --reload --port $Port
