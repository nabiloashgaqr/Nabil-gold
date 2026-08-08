@echo off
REM Demo stream: TICK-LEVEL execution authority (single-instance guarded).
REM 60s startup delay lets the MT5 terminal finish logging in after logon.
cd /d "%~dp0..\.."
if not exist logs mkdir logs
timeout /t 60 /nobreak >nul
set EXECUTION_MODE=mt5_demo
set TRADES_TABLE=trades_demo
set DATA_SOURCE_PRIMARY=mt5
set TICK_MANAGER=true
python scripts\run_tick_manager.py >> logs\tick_manager.log 2>&1
