$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envFile = Join-Path $projectRoot ".env"

if (-not (Test-Path $venvPython)) {
    throw "Virtual environment not found. Run .\setup.ps1 first."
}

if (-not (Test-Path $envFile)) {
    throw ".env was not found. Run .\setup.ps1 first."
}

Write-Host "Starting Discord bot..." -ForegroundColor Green
& $venvPython bot.py
