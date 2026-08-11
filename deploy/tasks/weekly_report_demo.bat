@echo off
REM Weekly report over DEMO book (Saturday 07:00 UTC ~ 10:00 Hebron).
cd /d "%~dp0..\.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
set EXECUTION_MODE=mt5_demo
set TRADES_TABLE=trades_demo
python scripts\run_weekly_report.py >> logs\weekly_report.log 2>&1
