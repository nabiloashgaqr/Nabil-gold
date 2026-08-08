# ══════════════════════════════════════════════════════════════════════════
# FULL-VPS bootstrap: EVERYTHING moves here; GitHub keeps ONLY the Dashboard.
# One repo checkout (branch demo/mt5) runs TWO streams:
#   • paper stream  (EXECUTION_MODE=paper,     TRADES_TABLE=trades)      = truth
#   • demo stream   (EXECUTION_MODE=mt5_demo,  TRADES_TABLE=trades_demo) = MT5 shadow
# plus the subscription bot. Run PowerShell AS ADMIN. Idempotent: re-runnable.
# ══════════════════════════════════════════════════════════════════════════
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

# 0. Timezone = UTC (identical clock behavior to the old GitHub runners)
try { Set-TimeZone -Id "UTC" } catch { Write-Host "Could not set timezone UTC (set it manually)." -ForegroundColor Yellow }

# 1. Git (code updates: git pull on this machine)
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
        Write-Host "NO WINGET (Windows Server): download Python 3.11 from https://www.python.org/downloads/ and install WITH 'Add python.exe to PATH' checked, then re-run this script." -ForegroundColor Yellow
        Start-Process "https://www.python.org/downloads/release/python-3119/"
        exit 1
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { Write-Host "After install, OPEN A NEW PowerShell window and re-run."; exit 1 }
}

# 3. Dependencies (MetaTrader5 is Windows-only; subscription bot has its own reqs)
python -m pip install --upgrade pip
python -m pip install MetaTrader5
python -m pip install -r requirements.txt
python -m pip install -r subscription_bot\requirements.txt

# 4. .env from template (EDIT VALUES BEFORE ANYTHING RUNS!)
if (-not (Test-Path .env)) { Copy-Item deploy\.env.example .env; Write-Host "EDIT .env NOW — fill every key." -ForegroundColor Yellow }

# 5. Logs dir
New-Item -ItemType Directory -Force -Path logs | Out-Null

# 6. Scheduled tasks — wrappers live in deploy\tasks (env + logging handled there)
$t = Join-Path (Get-Location).Path "deploy\tasks"
schtasks /Create /TN "SS_PaperAnalysis"   /SC MINUTE /MO 5           /TR "cmd /c $t\paper_analysis.bat"   /F
schtasks /Create /TN "SS_PaperUpdates"    /SC MINUTE /MO 1           /TR "cmd /c $t\paper_updates.bat"    /F
schtasks /Create /TN "SS_MacroContext"    /SC HOURLY /MO 1           /TR "cmd /c $t\macro_context.bat"    /F
schtasks /Create /TN "SS_DailyReport"     /SC DAILY  /ST 23:00       /TR "cmd /c $t\daily_report.bat"     /F
schtasks /Create /TN "SS_WeeklyReport"    /SC WEEKLY /D SAT /ST 07:00 /TR "cmd /c $t\weekly_report.bat"   /F
schtasks /Create /TN "SS_SubscriptionBot" /SC DAILY  /ST 00:00       /TR "cmd /c $t\subscription_bot.bat" /F
schtasks /Create /TN "SS_DemoAnalysis"    /SC MINUTE /MO 5           /TR "cmd /c $t\demo_analysis.bat"    /F
schtasks /Create /TN "SS_DemoWatchdog"    /SC MINUTE /MO 1           /TR "cmd /c $t\demo_watchdog.bat"    /F
schtasks /Create /TN "SS_DemoLoop"        /SC ONLOGON                /TR "cmd /c $t\demo_loop.bat"        /F
schtasks /Create /TN "SS_TickManager"     /SC ONLOGON                /TR "cmd /c $t\tick_manager.bat"     /F
schtasks /Create /TN "SS_MT5Terminal"     /SC ONLOGON                /TR "`"C:\Program Files\MetaTrader 5\terminal64.exe`"" /F

# 7. Smoke test BEFORE the first scheduled run
python scripts\demo_smoke_test.py

Write-Host ""
Write-Host "Setup complete. NEXT STEPS:" -ForegroundColor Green
Write-Host "  1. Edit .env (all keys) and log into the MT5 DEMO terminal once."
Write-Host "  2. Enable AUTO-LOGON (netplwiz) so ONLOGON tasks survive reboot."
Write-Host "  3. Verify tasks manually: deploy\tasks\paper_analysis.bat etc."
Write-Host "  4. Only THEN cut GitHub off: deploy\GITHUB_SHUTDOWN_AR.md"
