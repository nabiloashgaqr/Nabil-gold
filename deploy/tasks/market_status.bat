@echo off
REM Hourly Market Status card (was the cron-job.org status job on GitHub).
cd /d "%~dp0..\.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
set EXECUTION_MODE=mt5_demo
set TRADES_TABLE=trades_demo
set DATA_SOURCE_PRIMARY=mt5
set GITHUB_EVENT_NAME=workflow_dispatch
set SEND_STATUS_ON_MANUAL=true
python scripts\run_analysis.py >> logs\market_status.log 2>&1
