# Configure the public read-only dashboard entirely on the Windows VPS.
# Run as Administrator after extracting the local-dashboard round.
# ASCII-only for Windows PowerShell 5.1.

param([switch]$NoRestart)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

$envPath = Join-Path $root ".env"
if (-not (Test-Path $envPath)) { throw "Missing $envPath" }
$lines = @(Get-Content $envPath)
$newLines = @()
foreach ($line in $lines) {
    if ($line -match '^\s*DASHBOARD_API_(HOST|PORT|TOKEN)\s*=') { continue }
    if ($line -match '^\s*DASHBOARD_PUBLIC_READONLY\s*=') { continue }
    $newLines += $line
}
$newLines += "DASHBOARD_API_HOST=0.0.0.0"
$newLines += "DASHBOARD_API_PORT=8787"
$newLines += "DASHBOARD_PUBLIC_READONLY=true"
[System.IO.File]::WriteAllLines(
    $envPath, $newLines, (New-Object System.Text.UTF8Encoding($false))
)

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
python -m py_compile scripts\run_dashboard_server.py services\dashboard_local_api.py
if ($LASTEXITCODE -ne 0) { throw "Dashboard Python syntax validation failed" }

$ruleName = "SmartSignal Public Dashboard 8787"
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort 8787 -Profile Any | Out-Null

$taskBat = Join-Path $root "deploy\tasks\dashboard_api.bat"
if (-not (Test-Path $taskBat)) { throw "Missing $taskBat" }
& schtasks.exe /Create /TN "SS_DashboardAPI" /SC ONLOGON `
    /TR "cmd /c $taskBat" /F | Out-Host
if ($LASTEXITCODE -ne 0) { throw "Could not register SS_DashboardAPI" }

$publicIp = "VPS_PUBLIC_IP"
try { $publicIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 15).Trim() } catch {}
$url = "http://${publicIp}:8787/"
$out = Join-Path $root "dashboard_public_url.txt"
@(
    "SmartSignal public read-only dashboard:",
    $url,
    "",
    "Health: http://${publicIp}:8787/health",
    "Data source: C:\Nabil-gold\storage\trades.json"
) | Set-Content -Path $out -Encoding UTF8

Write-Host "Public local dashboard configured." -ForegroundColor Green
Write-Host "URL: $url" -ForegroundColor Cyan
Write-Host "Saved to: $out"
Write-Host "The server supports GET only; no trade/order mutation endpoint exists."

if ($NoRestart) {
    Write-Host "NoRestart selected. Required next command: Restart-Computer -Force" -ForegroundColor Yellow
    exit 0
}
Write-Host "Rebooting now so SS_DashboardAPI starts in the normal ONLOGON session." -ForegroundColor Yellow
Restart-Computer -Force
