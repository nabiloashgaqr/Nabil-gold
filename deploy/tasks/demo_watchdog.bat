@echo off
REM Demo stream: heartbeat watchdog every 1 minute.
cd /d "%~dp0..\.."
if not exist logs mkdir logs
set EXECUTION_MODE=mt5_demo
set TRADES_TABLE=trades_demo
python scripts\demo_watchdog.py >> logs\demo_watchdog.log 2>&1
