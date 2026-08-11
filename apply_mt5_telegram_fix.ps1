# Apply the 2026-08-11 Telegram/MT5 consistency round on the demo VPS.
# Run as Administrator AFTER extracting the round over C:\Nabil-gold.
# This script backs up the local trade book, validates Python syntax, repairs
# the MT5 ONLOGON task, removes stale runtime ownership files, then reboots.

param([switch]$NoRestart)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup = Join-Path $root "storage\backup_consistency_$stamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
if (Test-Path "storage\trades.json") {
    Copy-Item "storage\trades.json" (Join-Path $backup "trades.json") -Force
}
Write-Host "Trade-book backup: $backup" -ForegroundColor Green

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
python -m py_compile `
    scripts\run_analysis.py `
    scripts\run_tick_manager.py `
    scripts\run_demo_loop.py `
    scripts\demo_watchdog.py `
    services\database.py `
    services\mt5_executor.py `
    utils\helpers.py `
    utils\single_instance.py
if ($LASTEXITCODE -ne 0) { throw "Python syntax validation failed; NOT rebooting." }

$mt5Wrapper = Join-Path $root "deploy\tasks\mt5_terminal.bat"
if (-not (Test-Path $mt5Wrapper)) { throw "Missing $mt5Wrapper" }
$taskCommand = "cmd /c $mt5Wrapper"
& schtasks.exe /Create /TN "SS_MT5Terminal" /SC ONLOGON /TR $taskCommand /F | Out-Host
if ($LASTEXITCODE -ne 0) { throw "Could not repair SS_MT5Terminal." }

# Runtime files must be recreated by the new processes after reboot. They are
# not configuration and must never be carried between snapshots/deployments.
foreach ($file in @(
    "tick_manager.pid", "demo_loop.pid",
    "tick_heartbeat.json", "heartbeat.json",
    "tick_heartbeat.json.tmp"
)) {
    Remove-Item $file -Force -ErrorAction SilentlyContinue
}

Write-Host "Validation OK. Runtime files cleaned; MT5 task repaired." -ForegroundColor Green
if ($NoRestart) {
    Write-Host "NoRestart selected. REQUIRED next command: Restart-Computer -Force" -ForegroundColor Yellow
    exit 0
}

Write-Host "Rebooting now. ONLOGON tasks will restore the demo stack." -ForegroundColor Yellow
Restart-Computer -Force
