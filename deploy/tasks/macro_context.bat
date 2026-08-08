@echo off
REM Hourly macro context update (yfinance, free; was cron-job.org :07).
cd /d "%~dp0..\.."
if not exist logs mkdir logs
set EXECUTION_MODE=paper
set TRADES_TABLE=trades
python scripts\update_macro_context.py >> logs\macro_context.log 2>&1
