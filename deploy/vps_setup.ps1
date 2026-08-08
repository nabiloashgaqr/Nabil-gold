# Phase-2 VPS bootstrap (run PowerShell AS ADMIN on the Windows VPS).
# Idempotent: safe to re-run.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

# 1. Python 3.11+
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements }

# 2. Deps (MetaTrader5 package is Windows-only)
python -m pip install --upgrade pip
python -m pip install MetaTrader5
python -m pip install -r requirements.txt

# 3. .env from template (edit values afterwards!)
if (-not (Test-Path .env)) { Copy-Item deploy\.env.example .env; Write-Host "EDIT .env NOW with demo credentials" }

# 4. Scheduled tasks: demo loop every 5 min + watchdog every 1 min
$here = (Get-Location).Path
schtasks /Create /TN "SS_DemoLoop" /SC MINUTE /MO 5 /TR "cmd /c cd /d $here && python scripts\run_demo_loop.py >> demo_loop.log 2>&1" /F
schtasks /Create /TN "SS_DemoWatchdog" /SC MINUTE /MO 1 /TR "cmd /c cd /d $here && python scripts\demo_watchdog.py" /F

# 5. Smoke test before first loop run
python scripts\demo_smoke_test.py
Write-Host "Phase-2 setup complete."
