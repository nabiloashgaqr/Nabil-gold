Runtime files are intentionally not committed. The application creates local JSON/SQLite state on the VPS.
Never commit .env, trades.json, session_plans.json, auction_flow.sqlite3, WAL/SHM, logs, PID or calibration artifacts.
