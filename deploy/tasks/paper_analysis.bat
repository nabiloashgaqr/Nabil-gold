@echo off
REM Paper stream: full 5-minute analysis cycle (baseline truth).
cd /d "%~dp0..\.."
if not exist logs mkdir logs
set EXECUTION_MODE=paper
set TRADES_TABLE=trades
set DATA_SOURCE_PRIMARY=twelvedata
python scripts\run_analysis.py >> logs\paper_analysis.log 2>&1
