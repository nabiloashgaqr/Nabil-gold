@echo off
REM Weekly report (was weekly_report.yml). Saturday ~10:00 Hebron (07:00 UTC in summer).
cd /d "%~dp0..\.."
if not exist logs mkdir logs
set EXECUTION_MODE=paper
set TRADES_TABLE=trades
python scripts\run_weekly_report.py >> logs\weekly_report.log 2>&1
