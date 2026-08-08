# ══════════════════════════════════════════════════════════════════════════
# DEMO-ONLY VPS bootstrap (branch demo/mt5).
# GitHub keeps ONLY the Dashboard. Paper trading is STOPPED.
# Run PowerShell AS ADMIN inside C:\Nabil-gold. Idempotent: re-runnable.
# ══════════════════════════════════════════════════════════════════════════
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

# 0. Timezone = UTC (stable clock for logs/heartbeats)
try { Set-TimeZone -Id "UTC" } catch { Write-Host "Set timezone to UTC manually." -ForegroundColor Yellow }

# 1. Git (code updates on this machine: git pull)
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    $wg = Get-Command winget -ErrorAction SilentlyContinue
    if ($wg) {
        winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
    } else {
        Write-Host "NO WINGET: install Git from https://git-scm.com/download/win then re-run." -ForegroundColor Yellow
        Start-Process "https://git-scm.com/download/win"
    }
}

# 2. Python 3.11+
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    $wg = Get-Command winget -ErrorAction SilentlyContinue
    if ($wg) {
        winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    } else {
        Write-Host "NO WINGET (Windows Server): install Python 3.11 from https://www.python.org/downloads/ WITH 'Add python.exe to PATH', then re-run." -ForegroundColor Yellow
        Start-Process "https://www.python.org/downloads/release/python-3119/"
        exit 1
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { Write-Host "OPEN A NEW PowerShell window and re-run."; exit 1 }
}

# 3. Dependencies (MetaTrader5 is Windows-only)
python -m pip install --upgrade pip
python -m pip install MetaTrader5
python -m pip install -r requirements.txt

# 4. .env from template (EDIT VALUES BEFORE ANYTHING RUNS!)
if (-not (Test-Path .env)) { Copy-Item deploy\.env.example .env; Write-Host "EDIT .env NOW — fill every key." -ForegroundColor Yellow }

# 5. Logs dir
New-Item -ItemType Directory -Force -Path logs | Out-Null

# 6. Scheduled tasks — DEMO ONLY (wrappers in deploy\tasks set env + logging)
$t = Join-Path (Get-Location).Path "deploy\tasks"
schtasks /Create /TN "SS_DemoAnalysis" /SC MINUTE /MO 5  /TR "cmd /c $t\demo_analysis.bat" /F
schtasks /Create /TN "SS_DemoWatchdog" /SC MINUTE /MO 1  /TR "cmd /c $t\demo_watchdog.bat" /F
schtasks /Create /TN "SS_DemoLoop"     /SC ONLOGON       /TR "cmd /c $t\demo_loop.bat"     /F
schtasks /Create /TN "SS_TickManager"  /SC ONLOGON       /TR "cmd /c $t\tick_manager.bat"  /F
schtasks /Create /TN "SS_MT5Terminal"  /SC ONLOGON       /TR "`"C:\Program Files\MetaTrader 5\terminal64.exe`"" /F

# ── OPTIONAL (disabled by default): move the subscription bot here too.
#    If you enable these two lines, DISABLE "Subscription Bot Cron" on GitHub.
# python -m pip install -r subscription_bot\requirements.txt
# schtasks /Create /TN "SS_SubscriptionBot" /SC DAILY /ST 00:00 /TR "cmd /c $t\subscription_bot.bat" /F

# 7. Smoke test BEFORE the first scheduled run
python scripts\demo_smoke_test.py

Write-Host ""
Write-Host "Setup complete. NEXT:" -ForegroundColor Green
Write-Host "  1. Edit .env, log into the MT5 DEMO terminal once (keep it open)."
Write-Host "  2. Enable AUTO-LOGON (netplwiz) so ONLOGON tasks survive reboot."
Write-Host "  3. Test manually: deploy\tasks\demo_smoke.bat"
Write-Host "  4. Then cut GitHub: deploy\GITHUB_SHUTDOWN_AR.md"
