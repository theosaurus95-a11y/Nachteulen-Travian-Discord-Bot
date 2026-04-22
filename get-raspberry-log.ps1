param(
    [string]$InfoFile = "raspberry-pi-info.txt",
    [string]$RemoteProjectDir = "/home/christoph/discord-bot",
    [string]$RemoteLogPath = "logs/bot.log",
    [string]$LocalOutputDir = "raspberry-logs",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\travian_bot_rpi",
    [switch]$IncludeRotated
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

$sshArgs = @()
if (Test-Path $KeyPath) {
    $sshArgs = @("-i", $KeyPath)
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$localDir = Join-Path $projectRoot $LocalOutputDir
New-Item -ItemType Directory -Path $localDir -Force | Out-Null

$remoteFullPath = "$RemoteProjectDir/$RemoteLogPath"
$localLogPath = Join-Path $localDir "bot-$timestamp.log"

Write-Host "Downloading current bot log from $($remote):$remoteFullPath" -ForegroundColor Cyan
scp @sshArgs "${remote}:$remoteFullPath" $localLogPath

if ($IncludeRotated) {
    Write-Host "Downloading rotated bot logs..." -ForegroundColor Cyan
    scp @sshArgs "${remote}:$remoteFullPath.*" $localDir
}

Write-Host ""
Write-Host "Log downloaded to: $localLogPath" -ForegroundColor Green
Write-Host "Showing last 80 lines:" -ForegroundColor Cyan
Get-Content $localLogPath -Tail 80
