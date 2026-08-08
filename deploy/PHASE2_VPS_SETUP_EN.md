# Phase 2 — VPS Environment Setup (Agent Execution Sheet)

Prereq: branch `demo/mt5` on GitHub contains Phase-1 files (services/mt5_feed.py,
services/mt5_executor.py, scripts/run_demo_loop.py, scripts/demo_watchdog.py,
scripts/demo_smoke_test.py, deploy/...). Main stays paper; do not touch it.

## 1. Provision
- Windows Server VPS (2-4 vCPU / 8 GB / SSD), region close to the demo broker.
- Open RDP; enable automatic Windows updates OFF during trading hours.

## 2. Install (PowerShell AS ADMIN, repo root C:\Nabil-gold)
```
git clone -b demo/mt5 https://github.com/nabiloashgaqr/Nabil-gold.git C:\Nabil-gold
cd C:\Nabil-gold
deploy\vps_setup.ps1
```
The script: installs Python 3.11 if absent, `pip install MetaTrader5 -r requirements.txt`,
copies `.env` from template if missing, registers Task Scheduler tasks
`SS_DemoLoop` (every 5 min) and `SS_DemoWatchdog` (every 1 min), then runs
`scripts\demo_smoke_test.py` which MUST print SMOKE OK.

## 3. MT5 terminal
- Install MetaTrader 5 (official installer), log in ONCE manually with the DEMO
  account so the terminal stores the session; keep terminal running at boot
  (Settings > Options > "Start at startup").
- .env values: EXECUTION_MODE=mt5_demo, TRADES_TABLE=trades_demo,
  DATA_SOURCE_PRIMARY=mt5, MT5_PATH/LOGIN/PASSWORD/SERVER, TELEGRAM_DEMO_CHAT_ID,
  plus all existing secrets. NEVER a real account (smoke test refuses REAL).

## 4. Database (Supabase SQL editor, once)
Run `deploy/trades_demo.sql` exactly. Verify the check query returns the three
new columns.

## 5. Verification (agent must paste outputs)
1. `python scripts\demo_smoke_test.py` → all PASS + SMOKE OK.
2. Task Scheduler history shows SS_DemoLoop success every 5 min for 1 hour.
3. Demo Telegram chat received the 🧪 smoke message and loop heartbeats.
4. `type heartbeat.json` shows fresh UTC timestamps (<420s).
5. `type demo_loop.log | findstr ERROR` → empty.

## 6. Rollback
`shutdown /t 0` or disable the two tasks (`schtasks /Change /TN SS_DemoLoop /DISABLE`);
paper on main is unaffected; trades_demo remains for audit.

## 7. Handoff to Phase 3
Only after 24h of clean heartbeats and zero log errors. Report: uptime %,
smoke output, first 24h demo_metrics row count (expect 0 mismatches).
