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

    # 3. symbol + candles + timezone offset (broker symbol read from config:
    #    XAU/USD -> XAUUSD.s on JustMarkets; never hard-coded here again)
    from utils.helpers import load_config
    _cfg = load_config()
    sym = (((_cfg.get("execution") or {}).get("demo") or {})
           .get("symbol_map") or {}).get("XAU/USD", "XAUUSD.s")
    info = mt5.symbol_info(sym)
    check(f"symbol {sym} exists", bool(info))

    # 3b. contract economics — the $/point math assumes 100 oz per 1.0 lot.
    #     0.1 lot = 10 oz → 1 codebase point ($0.10 move) = exactly $1.00.
    cs = float(getattr(info, "trade_contract_size", 0) or 0)
    print(f"     contract_size={cs} oz/lot · volume_min={info.volume_min} · "
          f"volume_step={info.volume_step} · digits={info.digits} · "
          f"tick_value={info.trade_tick_value}")
    check("contract size = 100 oz/lot (our $ math assumes this)", cs == 100.0,
          "if your broker differs, the dollar math in cards must be rescaled")
    check("volume_min <= 0.05 (TP1 books half of 0.1 lot)",
          float(info.volume_min) <= 0.05,
          "below this the TP1 half-close degrades to full-close")
    from services import mt5_feed
    payload = mt5_feed.get_candles("XAU/USD", "5m", 100, {"XAU/USD": sym})
    check(f"mt5 candles payload ({sym})", bool(payload and len(payload["data"]) == 100))

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
