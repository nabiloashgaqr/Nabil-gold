@echo off
REM Manual: full environment smoke test (run BEFORE enabling the scheduled tasks).
cd /d "%~dp0..\.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set EXECUTION_MODE=mt5_demo
set TRADES_TABLE=trades_demo
set DATA_SOURCE_PRIMARY=mt5
python scripts\demo_smoke_test.py
