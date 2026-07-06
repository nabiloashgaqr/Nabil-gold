"""Fix trade that was wrongly closed as SL_HIT when breakeven should have applied.

Usage:
    python scripts/fix_trade_breakeven.py TRADE_20260706_004128_763880_0d6c2abd

Or via environment variable:
    TRADE_ID=TRADE_20260706_004128_763880_0d6c2abd python scripts/fix_trade_breakeven.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService  # noqa: E402
from utils.helpers import load_config  # noqa: E402


def _clean_trade_id(raw: str) -> str:
    """Clean trade ID — strip whitespace, quotes, and 'TRADE_ID=' prefix."""
    s = raw.strip().strip("\"'")
    # Handle case where user typed "TRADE_ID=TRADE_..." in the input field
    if "=" in s and s.upper().startswith("TRADE_ID="):
        s = s.split("=", 1)[1].strip().strip("\"'")
    return s


def _find_trade(db: DatabaseService, trade_id: str) -> dict | None:
    """Search for a trade across all available methods."""
    # 1. Open trades
    for t in db.get_open_trades():
        if str(t.get("id")) == trade_id:
            return t

    # 2. Recent trades (widen search)
    for limit in (200, 500, 1000):
        for t in db.get_recent_trades(limit=limit):
            if str(t.get("id")) == trade_id:
                return t

    # 3. Direct Supabase query (for closed trades not in recent list)
    if db.use_supabase and db.client:
        try:
            resp = db.client.table("trades").select("*").eq("id", trade_id).limit(1).execute()
            rows = list(resp.data or [])
            if rows:
                return rows[0]
        except Exception as exc:
            print(f"⚠️ Direct Supabase lookup failed: {exc}")

    return None


def main() -> int:
    # Get trade ID from: command line arg > env variable
    raw_id = ""
    if len(sys.argv) > 1:
        raw_id = sys.argv[1]
    else:
        raw_id = os.environ.get("TRADE_ID", "")

    trade_id = _clean_trade_id(raw_id)
    if not trade_id:
        print("❌ TRADE_ID not provided")
        print("Usage: python scripts/fix_trade_breakeven.py TRADE_...")
        print("   or: TRADE_ID=TRADE_... python scripts/fix_trade_breakeven.py")
        return 1

    print(f"🔍 Looking for trade: {trade_id}")

    cfg = load_config()
    db = DatabaseService(cfg)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    trade = _find_trade(db, trade_id)

    if not trade:
        print(f"❌ Trade {trade_id} not found in database")
        print("   Checked: open trades, recent trades, and direct Supabase query")
        return 1

    entry = float(trade.get("entry_price", 0))
    old_status = trade.get("status", "?")
    old_close = trade.get("close_price", "?")
    old_pnl = trade.get("final_pnl_points", trade.get("current_pnl_points", "?"))

    print(f"\n=== Before ===")
    print(f"  ID:           {trade_id}")
    print(f"  Status:       {old_status}")
    print(f"  Entry:        {entry}")
    print(f"  Close Price:  {old_close}")
    print(f"  PnL Points:   {old_pnl}")
    print(f"  Result:       {trade.get('result', '?')}")
    print()

    # Apply breakeven fix
    updates = {
        "status": "BE_HIT",
        "close_price": entry,
        "current_price": entry,
        "current_pnl": 0,
        "current_pnl_points": 0,
        "final_pnl": 0,
        "final_pnl_points": 0,
        "result": "BREAKEVEN",
        "sl_moved_to_entry": True,
        "management_phase": "breakeven",
        "closed_at": now,
        "close_time": now,
        "last_updated": now,
        "updated_at": now,
    }

    db.update_trade(trade_id, updates)

    print(f"=== After ===")
    print(f"  Status:       BE_HIT")
    print(f"  Close Price:  {entry} (entry)")
    print(f"  PnL Points:   0")
    print(f"  Result:       BREAKEVEN")
    print()
    print(f"✅ Trade {trade_id} updated: {old_status} → BE_HIT (breakeven)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
