@echo off
REM Daily report chain (was daily_report.yml): updates(force,quiet) -> learning -> report. 23:00 UTC.
cd /d "%~dp0..\.."
if not exist logs mkdir logs
set EXECUTION_MODE=paper
set TRADES_TABLE=trades
set DATA_SOURCE_PRIMARY=twelvedata
set FORCE_TRADE_UPDATE=true
set EOD_QUIET=true
python scripts\run_trade_updates.py >> logs\daily_report.log 2>&1
set FORCE_TRADE_UPDATE=
set EOD_QUIET=
python scripts\run_learning.py >> logs\daily_report.log 2>&1
python scripts\run_daily_report.py >> logs\daily_report.log 2>&1
