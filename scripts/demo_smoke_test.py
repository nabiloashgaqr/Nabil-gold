"""Phase-2 smoke test: verify the VPS environment end-to-end BEFORE the loop.

Run on the VPS inside C:\\Nabil-gold:  python scripts/demo_smoke_test.py
Every step prints PASS/FAIL; any FAIL aborts with a clear hint. Never writes
trades; the Telegram step sends ONE 🧪 message to the demo chat.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- VPS: load .env if present (real env vars ALWAYS win over .env) ---
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()  # override=False: task-wrapper vars take precedence
except Exception:
    pass


def check(name, ok, hint=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {hint}" if hint and not ok else ""))
    if not ok:
        sys.exit(1)


def main() -> None:
    # 1. env
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "TELEGRAM_BOT_TOKEN",
                "TRADES_TABLE", "MT5_LOGIN", "MT5_SERVER"):
        check(f"env {var}", bool(os.environ.get(var)), "set it in .env")

    # 2. MT5 terminal + login
    import MetaTrader5 as mt5
    ok = mt5.initialize(path=os.environ.get("MT5_PATH") or None)
    check("mt5.initialize", bool(ok), str(mt5.last_error()))
    ok = mt5.login(int(os.environ["MT5_LOGIN"]),
                   password=os.environ.get("MT5_PASSWORD") or "",
                   server=os.environ["MT5_SERVER"])
    check("mt5.login(demo)", bool(ok), str(mt5.last_error()))
    info = mt5.account_info()
    check("account is demo (trade_mode != REAL)",
          bool(info) and "real" not in str(getattr(info, "trade_mode", "")).lower(),
          "REFUSE real accounts in this phase")

    # 3. symbol + candles + timezone offset
    sym = "XAUUSD"
    check(f"symbol {sym} exists", bool(mt5.symbol_info(sym)))
    from services import mt5_feed
    payload = mt5_feed.get_candles("XAU/USD", "5m", 100, {"XAU/USD": "XAUUSD"})
    check("mt5 candles payload", bool(payload and len(payload["data"]) == 100))

    # 4. demo table reachable
    from services.database import DatabaseService
    from utils.helpers import load_config
    db = DatabaseService(load_config())
    check("TRADES_TABLE routes to demo", db.trades_table == "trades_demo")
    rows = db.get_open_trades()
    check("trades_demo readable", isinstance(rows, list))

    # 5. telegram demo chat
    if os.environ.get("TELEGRAM_DEMO_CHAT_ID"):
        from services.telegram_bot import TelegramService
        ok = TelegramService(load_config()).send_message("🧪 DEMO smoke test OK")
        check("telegram demo chat", bool(ok))
    print("SMOKE OK — safe to schedule run_demo_loop")


if __name__ == "__main__":
    main()
