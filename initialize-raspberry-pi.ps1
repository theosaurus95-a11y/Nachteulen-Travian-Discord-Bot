param(
    [string]$InfoFile = "raspberry-pi-info.txt",
    [string]$RemoteProjectDir = "/home/christoph/discord-bot",
    [string]$ServiceName = "travian-discord-bot",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\travian_bot_rpi"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

function Get-PiInfo {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        throw "Info file not found: $Path"
    }

    $info = @{}
    foreach ($line in Get-Content $Path -Encoding UTF8) {
        if ($line -match "^\s*([^:]+):\s*(.+?)\s*$") {
            $info[$matches[1].Trim()] = $matches[2].Trim()
        }
    }

    foreach ($key in @("Hostname", "Benutzername")) {
        if (-not $info.ContainsKey($key)) {
            throw "Missing '$key' in $Path"
        }
    }

    return $info
}

function Write-Utf8NoBom {
    param(
        [string]$Path,
        [string]$Value
    )

    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Value, $encoding)
}

$piInfo = Get-PiInfo $InfoFile
$remoteHost = $piInfo["Hostname"]
$remoteUser = $piInfo["Benutzername"]
$remote = "$remoteUser@$remoteHost"
$sshArgs = @()
if (Test-Path $KeyPath) {
    $sshArgs = @("-i", $KeyPath)
}

Write-Host "Initializing Raspberry Pi target $remote" -ForegroundColor Cyan
Write-Host "You may be asked for the Raspberry Pi password from $InfoFile." -ForegroundColor Yellow
Write-Host "Tip: run this script from an already open PowerShell window so messages stay visible." -ForegroundColor Yellow

$remoteScript = @"
set -euo pipefail

PROJECT_DIR='$RemoteProjectDir'
SERVICE_NAME='$ServiceName'
SERVICE_FILE="/etc/systemd/system/`$SERVICE_NAME.service"

echo "Installing system packages..."
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip

echo "Creating project directory: `$PROJECT_DIR"
mkdir -p "`$PROJECT_DIR"

echo "Creating Python virtual environment..."
python3 -m venv "`$PROJECT_DIR/.venv"
"`$PROJECT_DIR/.venv/bin/python" -m pip install --upgrade pip

echo "Installing/refreshing systemd service: `$SERVICE_NAME"
sudo tee "`$SERVICE_FILE" >/dev/null <<SERVICE
[Unit]
Description=Travian Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$remoteUser
WorkingDirectory=`$PROJECT_DIR
ExecStart=`$PROJECT_DIR/.venv/bin/python `$PROJECT_DIR/bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable "`$SERVICE_NAME.service"

echo "Initialization complete. Run deploy-to-raspberry-pi.ps1 to copy files and start the bot."
"@

$localTempScript = Join-Path $env:TEMP "travian-bot-rpi-init.sh"
$remoteTempScript = "/tmp/travian-bot-rpi-init.sh"

try {
    Write-Utf8NoBom -Path $localTempScript -Value $remoteScript

    Write-Host ""
    Write-Host "Copying initialization script to Raspberry Pi..." -ForegroundColor Cyan
    scp @sshArgs $localTempScript "${remote}:$remoteTempScript"

    Write-Host ""
    Write-Host "Running initialization script on Raspberry Pi..." -ForegroundColor Cyan
    Write-Host "If sudo asks for a password, enter the Raspberry Pi password." -ForegroundColor Yellow
    ssh @sshArgs -t $remote "chmod +x '$remoteTempScript' && '$remoteTempScript'; result=`$?; rm -f '$remoteTempScript'; exit `$result"
}
finally {
    if (Test-Path $localTempScript) {
        Remove-Item -LiteralPath $localTempScript -Force
    }
}

Write-Host ""
Write-Host "Raspberry Pi initialization complete." -ForegroundColor Green
Write-Host "Next step: .\deploy-to-raspberry-pi.ps1"
