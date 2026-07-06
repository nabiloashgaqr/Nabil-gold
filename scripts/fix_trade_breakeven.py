"""Fix trade that was wrongly closed as SL_HIT when breakeven should have applied.

The price reached 4202 (+184 pts from entry 4183.58), which is above the
early_breakeven threshold of +150 pts. The stop should have moved to entry
before the price dropped back, but the 5-min update cycle missed the peak.

This script changes the trade from SL_HIT/LOSS to BE_HIT/BREAKEVEN.

Usage:
    TRADE_ID=TRADE_20260706_004128_763880_0d6c2abd python scripts/fix_trade_breakeven.py

Or with environment variables for GitHub Actions:
    TRADE_ID=TRADE_20260706_004128_763880_0d6c2abd \
    SUPABASE_URL=... \
    SUPABASE_KEY=... \
    python scripts/fix_trade_breakeven.py
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


def main() -> int:
    trade_id = os.environ.get("TRADE_ID", "").strip()
    if not trade_id:
        print("❌ TRADE_ID not set")
        print("Usage: TRADE_ID=TRADE_... python scripts/fix_trade_breakeven.py")
        return 1

    cfg = load_config()
    db = DatabaseService(cfg)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    # Fetch the trade
    trade = None
    for t in db.get_recent_trades(limit=200):
        if str(t.get("id")) == trade_id:
            trade = t
            break
    if not trade:
        for t in db.get_open_trades():
            if str(t.get("id")) == trade_id:
                trade = t
                break

    if not trade:
        print(f"❌ Trade {trade_id} not found in database")
        return 1

    entry = float(trade.get("entry_price", 0))
    old_status = trade.get("status", "?")
    old_close = trade.get("close_price", "?")
    old_pnl = trade.get("final_pnl_points", trade.get("current_pnl_points", "?"))

    print(f"=== Before ===")
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
    print(f"✅ Trade {trade_id} updated: SL_HIT → BE_HIT (breakeven)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
