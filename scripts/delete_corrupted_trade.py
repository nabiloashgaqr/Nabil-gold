"""Delete a trade corrupted by a bad data-provider price tick.

Usage (local or GitHub Actions):
    TRADE_ID=TRADE_20260706_173158_076329_7ca4f66c python scripts/delete_corrupted_trade.py
"""

from __future__ import annotations

import os, sys, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService
from utils.helpers import load_config

logger = logging.getLogger(__name__)


def _trade_id() -> str:
    return os.environ.get("TRADE_ID", "TRADE_20260706_173158_076329_7ca4f66c").strip()


def main() -> int:
    cfg = load_config()
    db = DatabaseService(cfg)
    trade_id = _trade_id()

    # Find the trade
    all_trades = {}
    for t in db.get_recent_trades(limit=200):
        all_trades[str(t.get("id"))] = t
    for t in db.get_open_trades():
        all_trades[str(t.get("id"))] = t

    trade = all_trades.get(trade_id)
    if not trade:
        print(f"❌ Trade {trade_id} not found!")
        return 1

    entry = trade.get("entry_price", "?")
    tp = trade.get("type", "?")
    status = trade.get("status", "?")

    print(f"🔍 Trade {trade_id}")
    print(f"   {tp} @ {entry}  |  Status: {status}")
    print(f"   Symbol: {trade.get('symbol', '?')}")

    if db.use_supabase and db.client:
        try:
            db.client.table("trades").delete().eq("id", trade_id).execute()
            print(f"✅ Trade {trade_id} DELETED from Supabase")
            return 0
        except Exception as exc:
            print(f"❌ Supabase delete failed: {exc}")
            return 1
    else:
        print("❌ Supabase not configured — cannot delete")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
