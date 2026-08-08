@echo off
REM Paper stream: open-trade management every 1 minute (faster than GitHub's 5).
cd /d "%~dp0..\.."
if not exist logs mkdir logs
set EXECUTION_MODE=paper
set TRADES_TABLE=trades
set DATA_SOURCE_PRIMARY=twelvedata
python scripts\run_trade_updates.py >> logs\paper_updates.log 2>&1
