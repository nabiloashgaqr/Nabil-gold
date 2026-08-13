@echo off
REM Persistent public read-only dashboard, entirely hosted on this VPS.
cd /d "%~dp0..\.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
python scripts\run_dashboard_server.py >> logs\dashboard_api.log 2>&1
