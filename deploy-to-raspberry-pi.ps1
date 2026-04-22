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

function Copy-ToPi {
    param(
        [string]$LocalPath,
        [string]$RemoteTarget,
        [string[]]$SshArgs
    )

    if (-not (Test-Path $LocalPath)) {
        throw "Required deployment file not found: $LocalPath"
    }

    scp @SshArgs $LocalPath $RemoteTarget
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
$remoteTarget = "${remote}:$RemoteProjectDir/"
$sshArgs = @()
if (Test-Path $KeyPath) {
    $sshArgs = @("-i", $KeyPath)
}

$filesToDeploy = @(
    "bot.py",
    "bot_runtime.py",
    "travian_discord_integration.py",
    "travian_kingdoms_api.py",
    "requirements.txt",
    ".env",
    ".env.example",
    "travian-map-data.json"
)

Write-Host "Deploying Discord bot to $($remote):$RemoteProjectDir" -ForegroundColor Cyan
Write-Host "You may be asked for the Raspberry Pi password from $InfoFile." -ForegroundColor Yellow
Write-Host "Tip: run this script from an already open PowerShell window so messages stay visible." -ForegroundColor Yellow

ssh @sshArgs $remote "mkdir -p '$RemoteProjectDir'"

foreach ($file in $filesToDeploy) {
    Write-Host "Copying $file"
    Copy-ToPi -LocalPath (Join-Path $projectRoot $file) -RemoteTarget $remoteTarget -SshArgs $sshArgs
}

$remoteScript = @"
set -euo pipefail

PROJECT_DIR='$RemoteProjectDir'
SERVICE_NAME='$ServiceName'

if [ ! -x "`$PROJECT_DIR/.venv/bin/python" ]; then
  echo "Virtual environment missing. Creating it now..."
  python3 -m venv "`$PROJECT_DIR/.venv"
fi

echo "Installing Python dependencies..."
"`$PROJECT_DIR/.venv/bin/python" -m pip install --upgrade pip
"`$PROJECT_DIR/.venv/bin/python" -m pip install -r "`$PROJECT_DIR/requirements.txt"

echo "Validating Python files..."
cd "`$PROJECT_DIR"
"`$PROJECT_DIR/.venv/bin/python" -m py_compile bot.py bot_runtime.py travian_discord_integration.py travian_kingdoms_api.py

echo "Restarting service..."
sudo systemctl restart "`$SERVICE_NAME.service"
sudo systemctl --no-pager --full status "`$SERVICE_NAME.service"
"@

$localTempScript = Join-Path $env:TEMP "travian-bot-rpi-deploy.sh"
$remoteTempScript = "/tmp/travian-bot-rpi-deploy.sh"

try {
    Write-Utf8NoBom -Path $localTempScript -Value $remoteScript

    Write-Host ""
    Write-Host "Copying deployment script to Raspberry Pi..." -ForegroundColor Cyan
    scp @sshArgs $localTempScript "${remote}:$remoteTempScript"

    Write-Host ""
    Write-Host "Running deployment script on Raspberry Pi..." -ForegroundColor Cyan
    Write-Host "If sudo asks for a password, enter the Raspberry Pi password." -ForegroundColor Yellow
    ssh @sshArgs -t $remote "chmod +x '$remoteTempScript' && '$remoteTempScript'; result=`$?; rm -f '$remoteTempScript'; exit `$result"
}
finally {
    if (Test-Path $localTempScript) {
        Remove-Item -LiteralPath $localTempScript -Force
    }
}

Write-Host ""
Write-Host "Deployment complete. The bot service was restarted on the Raspberry Pi." -ForegroundColor Green
