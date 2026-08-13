@echo off
REM Daily report over DEMO book (23:00 UTC): updates(force,quiet) -> learning -> report.
cd /d "%~dp0..\.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
set EXECUTION_MODE=mt5_demo
set TRADES_TABLE=trades_demo
set DATA_SOURCE_PRIMARY=mt5
set TICK_MANAGER=true
set FORCE_TRADE_UPDATE=true
set EOD_QUIET=true
python scripts\run_trade_updates.py >> logs\daily_report.log 2>&1
set FORCE_TRADE_UPDATE=
set EOD_QUIET=
python scripts\run_learning.py >> logs\daily_report.log 2>&1
python scripts\run_daily_report.py >> logs\daily_report.log 2>&1
