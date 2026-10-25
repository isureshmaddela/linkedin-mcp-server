# Starts the Aira Career Copilot locally (loopback only).
# Usage: .\scripts\start_aira.ps1 [-Port 8765] [-OpenBrowser]
param(
    [int]$Port = 8765,
    [switch]$OpenBrowser,
    # The Aira account (hosted build) that can sign people in here, e.g. http://127.0.0.1:8780. The shared secret
    # (AIRA_ACCOUNT_SECRET) belongs in .env, not on the command line.
    [string]$AccountUrl = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv is required. Install it from https://docs.astral.sh/uv/ and run 'uv sync --group dev'."
}

if (-not (Test-Path ".venv")) {
    Write-Host "Installing dependencies..." -ForegroundColor Yellow
    uv sync --group dev
}

$ollama = $null
try { $ollama = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 } catch { }
if ($null -eq $ollama) {
    Write-Host "Ollama is not reachable at http://127.0.0.1:11434. Fit reviews and drafts will be unavailable until it runs ('ollama serve')." -ForegroundColor Yellow
}

$env:AUTO_APPLY_PORT = "$Port"
if ($AccountUrl) { $env:AIRA_ACCOUNT_URL = $AccountUrl }
Write-Host "Starting Aira Career Copilot at http://127.0.0.1:$Port (Ctrl+C to stop)" -ForegroundColor Green
if ($OpenBrowser) { Start-Process "http://127.0.0.1:$Port" }
uv run python auto_apply_assistant.py
