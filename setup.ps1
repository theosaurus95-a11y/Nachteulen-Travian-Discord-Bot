param(
    [string]$Token,
    [string]$Prefix = "!",
    [string]$WatchChannelIds = "1493215975288471607"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$venvPath = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$envFile = Join-Path $projectRoot ".env"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

Write-Step "Checking Python"
try {
    $pythonVersion = & python --version
    Write-Host $pythonVersion
}
catch {
    throw "Python was not found in PATH. Install Python and make sure `python` works in PowerShell."
}

if (-not (Test-Path $venvPath)) {
    Write-Step "Creating virtual environment"
    & python -m venv .venv
}
else {
    Write-Step "Virtual environment already exists"
}

Write-Step "Installing dependencies"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

if (-not $Token) {
    Write-Step "Discord token"
    $secureToken = Read-Host "Paste your Discord bot token" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    try {
        $Token = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

if (-not $Token) {
    throw "A Discord bot token is required."
}

Write-Step "Writing .env"
$envLines = @(
    "DISCORD_TOKEN=$Token"
    "COMMAND_PREFIX=$Prefix"
    "WATCH_CHANNEL_IDS=$WatchChannelIds"
)
Set-Content -Path $envFile -Value $envLines -Encoding UTF8

Write-Step "Setup complete"
Write-Host "You can now start the bot with:" -ForegroundColor Green
Write-Host ".\start-bot.ps1"
