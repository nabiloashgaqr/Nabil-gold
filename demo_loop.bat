@echo off
REM Demo stream: persistent 5-min bookkeeping loop (single-instance guarded).
cd /d "%~dp0..\.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
set EXECUTION_MODE=mt5_demo
set TRADES_TABLE=trades_demo
set DATA_SOURCE_PRIMARY=mt5
set TICK_MANAGER=true
python scripts\run_demo_loop.py >> logs\demo_loop.log 2>&1
