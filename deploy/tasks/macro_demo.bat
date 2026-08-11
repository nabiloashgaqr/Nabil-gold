@echo off
REM Hourly macro context (was cron-job.org on GitHub).
cd /d "%~dp0..\.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
set EXECUTION_MODE=mt5_demo
set TRADES_TABLE=trades_demo
python scripts\update_macro_context.py >> logs\macro_context.log 2>&1
