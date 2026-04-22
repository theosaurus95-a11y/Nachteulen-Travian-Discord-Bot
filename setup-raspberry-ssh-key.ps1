param(
    [string]$InfoFile = "raspberry-pi-info.txt",
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

$piInfo = Get-PiInfo $InfoFile
$remoteHost = $piInfo["Hostname"]
$remoteUser = $piInfo["Benutzername"]
$remote = "$remoteUser@$remoteHost"
$publicKeyPath = "$KeyPath.pub"

Write-Host "Setting up SSH key login for $remote" -ForegroundColor Cyan
Write-Host "Key path: $KeyPath"

$sshDir = Split-Path -Parent $KeyPath
if (-not (Test-Path $sshDir)) {
    New-Item -ItemType Directory -Path $sshDir | Out-Null
}

if (-not (Test-Path $KeyPath)) {
    Write-Host "Creating SSH key..." -ForegroundColor Cyan
    $keygenCommand = 'ssh-keygen -t ed25519 -f "{0}" -N "" -C "travian-discord-bot-rpi"' -f $KeyPath
    cmd /c $keygenCommand
}
else {
    Write-Host "SSH key already exists, reusing it." -ForegroundColor Yellow
}

if (-not (Test-Path $publicKeyPath)) {
    if (Test-Path $KeyPath) {
        Write-Host "Public key missing. Recreating it from the private key..." -ForegroundColor Yellow
        $publicKey = & ssh-keygen -y -f $KeyPath
        if (-not $publicKey) {
            throw "Could not recreate public key from: $KeyPath"
        }
        Set-Content -Path $publicKeyPath -Value $publicKey -Encoding ascii
    }
    else {
        throw "Public key was not found: $publicKeyPath"
    }
}

$publicKey = (Get-Content $publicKeyPath -Raw).Trim()
if (-not $publicKey) {
    throw "Public key file is empty: $publicKeyPath"
}

Write-Host ""
Write-Host "Copying public key to Raspberry Pi..." -ForegroundColor Cyan
Write-Host "You should be asked for the Raspberry Pi password one last time." -ForegroundColor Yellow

$escapedPublicKey = $publicKey.Replace("'", "'\''")
$remoteCommand = "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && grep -qxF '$escapedPublicKey' ~/.ssh/authorized_keys || echo '$escapedPublicKey' >> ~/.ssh/authorized_keys"
ssh $remote $remoteCommand

Write-Host ""
Write-Host "Testing passwordless SSH login..." -ForegroundColor Cyan
ssh -i $KeyPath -o BatchMode=yes $remote "echo SSH key login works."

Write-Host ""
Write-Host "SSH key setup complete." -ForegroundColor Green
Write-Host "The Raspberry scripts will use this key automatically after a small update."
