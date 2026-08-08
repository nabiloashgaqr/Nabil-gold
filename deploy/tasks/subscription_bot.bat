@echo off
REM Subscription bot daily maintenance (was subscription_cron.yml at 00:00).
cd /d "%~dp0..\.."
if not exist logs mkdir logs
python subscription_bot\cron_maintenance.py >> logs\subscription_bot.log 2>&1
