# Starts the Aira hosted (multi-tenant) build locally: web tier, worker pool and scheduler in one process.
# Usage: .\scripts\start_aira_cloud.ps1 [-Port 8780] [-DataDir .\.aira-cloud] [-SeedDemo] [-OpenBrowser]
#
# Development defaults: the stand-in billing provider (no money moves) and mail kept in the database. For a real
# deployment use docs/multitenant-operations.md and deploy/.
param(
    [int]$Port = 8780,
    [string]$DataDir = "",
    [switch]$SeedDemo,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv is required. Install it from https://docs.astral.sh/uv/ and run 'uv sync --group dev'."
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Error "Port $Port is already in use (process id $($listener[0].OwningProcess)). Stop that process, or start this on another port: .\scripts\start_aira_cloud.ps1 -Port 8790"
}

$needsInstall = -not (Test-Path ".venv")
if (-not $needsInstall) {
    uv run python -c "import psycopg, signxml, websockets" 2>$null
    $needsInstall = $LASTEXITCODE -ne 0
}
if ($needsInstall) {
    Write-Host "Installing dependencies (first run only)..." -ForegroundColor Yellow
    uv sync --group dev --extra cloud
}

# Optional local settings (for example the account-link secret), one KEY=VALUE per line; never echoed.
$envFile = Join-Path $root ".env.cloud"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim(), "Process") }
    }
}

if ([string]::IsNullOrWhiteSpace($DataDir)) { $DataDir = Join-Path $root ".aira-cloud" }
$env:AIRA_CLOUD_DATA = $DataDir
$env:AIRA_CLOUD_PORT = "$Port"
$env:AIRA_CLOUD_BASE_URL = "http://127.0.0.1:$Port"

$ollama = $null
try { $ollama = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 } catch { }
if ($null -eq $ollama) {
    Write-Host "Ollama is not reachable at http://127.0.0.1:11434. Fit reviews and drafts will be unavailable until it runs ('ollama serve'), or set AIRA_CLOUD_MODEL_PROVIDER." -ForegroundColor Yellow
}

if ($SeedDemo) {
    if (Test-Path (Join-Path $DataDir "control.sqlite3")) {
        Write-Host "Demo data already exists in $DataDir; not seeding again." -ForegroundColor Yellow
    } else {
        Write-Host "Creating demo organizations (password: demo-password-123)..." -ForegroundColor Yellow
        uv run python -m aira_cloud seed-demo
    }
}

Write-Host "Starting Aira (hosted build) at http://127.0.0.1:$Port with data in $DataDir (Ctrl+C to stop)" -ForegroundColor Green
Write-Host "Create a platform operator with:  uv run python -m aira_cloud operator --email you@example.com" -ForegroundColor DarkGray
if ($SeedDemo) {
    Write-Host "Demo sign-in (password demo-password-123): coach.ana@northwind.test, cam.cole@northwind.test (no MFA), owner@northwind.test (asks you to set up an authenticator app first), ops@aira.test at /#/platform" -ForegroundColor Cyan
}
# Open the browser only once the server answers, so the first page is never a connection error.
$url = "http://127.0.0.1:$Port"
$openWhenReady = $OpenBrowser.IsPresent
Start-Job -ArgumentList $url, $openWhenReady -ScriptBlock {
    param($address, $open)
    for ($i = 0; $i -lt 90; $i++) {
        try {
            if ((Invoke-WebRequest -Uri "$address/" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) {
                Write-Host "Aira is ready: $address"
                if ($open) { Start-Process $address }
                return
            }
        } catch { Start-Sleep -Seconds 1 }
    }
} | Out-Null
uv run python -m aira_cloud serve --worker --port $Port
