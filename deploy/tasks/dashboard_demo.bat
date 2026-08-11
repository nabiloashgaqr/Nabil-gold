@echo off
REM Dashboard generation over the continuous book (paper history + demo), 22:00 daily.
cd /d "%~dp0..\.."
if not exist logs mkdir logs
set TRADES_TABLE=trades_demo
python scripts\generate_dashboard.py >> logs\dashboard.log 2>&1
