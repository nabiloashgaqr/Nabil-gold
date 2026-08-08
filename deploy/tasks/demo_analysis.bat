@echo off
REM Demo stream: 5-minute analysis writing to trades_demo (MT5 data source).
cd /d "%~dp0..\.."
if not exist logs mkdir logs
set EXECUTION_MODE=mt5_demo
set TRADES_TABLE=trades_demo
set DATA_SOURCE_PRIMARY=mt5
python scripts\run_analysis.py >> logs\demo_analysis.log 2>&1
