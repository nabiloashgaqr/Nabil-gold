"""Direct fix: close TRADE_20260706_124621_400170_3e420018 as BE_HIT.

Usage:
    python scripts/repair_trade_be_hit.py
"""

from __future__ import annotations
import os, sys, json, logging
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService
from utils.helpers import load_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TRADE_ID = os.environ.get("TRADE_ID", "TRADE_20260706_124621_400170_3e420018")
ENTRY_PRICE = 4132.07
NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    cfg = load_config()
    db = DatabaseService(cfg)

    # ── Step 1: Find the trade ──
    trade = None
    for t in db.get_recent_trades(limit=200):
        if str(t.get("id")) == TRADE_ID:
            trade = t
            break
    if not trade:
        for t in db.get_open_trades():
            if str(t.get("id")) == TRADE_ID:
                trade = t
                break

    if not trade:
        print(f"❌ {TRADE_ID} not found in recent or open trades!")
        return 1

    old_status = str(trade.get("status", "?")).upper()
    print(f"🔍 {TRADE_ID}  |  Status: {old_status}  |  Entry: {ENTRY_PRICE}")

    # ── Step 2: Build the BE_HIT payload ──
    updates: dict[str, any] = {
        "status": "BE_HIT",
        "result": "BREAKEVEN",
        "sl_moved_to_entry": True,
        "stop_loss": ENTRY_PRICE,
        "close_price": ENTRY_PRICE,
        "final_pnl": 0.0,
        "final_pnl_points": 0.0,
        "closed_at": NOW,
        "close_time": NOW,
        "last_updated": NOW,
    }

    # ── Step 3: Write directly with raw Supabase call (bypass column-dropping complexity) ──
    if db.use_supabase and db.client:
        try:
            response = (
                db.client.table("trades")
                .update(updates)
                .eq("id", TRADE_ID)
                .execute()
            )
            print(f"✅ {TRADE_ID} → BE_HIT  |  PnL: 0  |  Supabase OK")
            return 0
        except Exception as exc:
            error_text = str(exc)
            print(f"⚠️  Full update failed: {error_text[:120]}")
            # Try the legacy payload
            legacy = {
                "status": "BE_HIT",
                "result": "BREAKEVEN",
                "close_price": ENTRY_PRICE,
                "final_pnl": 0.0,
                "closed_at": NOW,
                "close_time": NOW,
                "last_updated": NOW,
            }
            try:
                db.client.table("trades").update(legacy).eq("id", TRADE_ID).execute()
                print(f"✅ {TRADE_ID} → BE_HIT  |  PnL: 0  |  Supabase (legacy fallback) OK")
                return 0
            except Exception as exc2:
                print(f"❌ Legacy update also failed: {exc2}")
                return 1
    else:
        print("❌ Supabase not configured")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
