# Open the web UI in Microsoft Edge once the target URL is ready.
# Called by start_*.bat in a minimized window.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File open_ui.ps1 -Url http://127.0.0.1:8070/ui/
param(
    [Parameter(Mandatory = $true)][string]$Url,
    [int]$TimeoutSec = 90,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Find-Edge {
    # 1) App Paths registry entry (most reliable)
    foreach ($key in @(
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe',
            'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe')) {
        try {
            $item = Get-ItemProperty -Path $key -ErrorAction Stop
            $candidate = $item.'(default)'
            if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
        } catch { }
    }
    # 2) common install locations (do not rely on ProgramFiles(x86) env var,
    #    it is missing in some shells)
    foreach ($dir in @(
            'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            'C:\Program Files\Microsoft\Edge\Application\msedge.exe')) {
        if (Test-Path -LiteralPath $dir) { return $dir }
    }
    $envLocal = [Environment]::GetEnvironmentVariable('LOCALAPPDATA')
    if ($envLocal) {
        $candidate = Join-Path $envLocal 'Microsoft\Edge\Application\msedge.exe'
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    # 3) PATH
    $cmd = Get-Command msedge.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

# Poll until the URL answers with 2xx/3xx (or timeout).
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) { $ready = $true; break }
    } catch {
        Start-Sleep -Milliseconds 800
    }
}

$edge = Find-Edge
if ($DryRun) {
    Write-Output ("ready={0} edge={1} url={2}" -f $ready, $edge, $Url)
    exit 0
}

if ($edge) {
    Start-Process -FilePath $edge -ArgumentList @('--new-window', $Url)
} else {
    # Fall back to the default browser so something always opens.
    Start-Process $Url
}
