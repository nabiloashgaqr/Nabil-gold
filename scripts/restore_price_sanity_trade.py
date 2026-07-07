"""Restore a trade falsely closed by a corrupted data-provider price tick.

These ticks are random bad snapshots (e.g. XAU/USD at 3366 instead of 4150).
The price-sanity gate now prevents them, but trades already corrupted by
earlier ticks need a one-click repair.

Usage (local or GitHub Actions):
    TRADE_ID=TRADE_20260706_124621_400170_3e420018 CURRENT_PRICE=4150 python scripts/restore_price_sanity_trade.py

It reopens the trade by clearing close/result fields and setting status back
to OPEN. It also strips any TP2_HIT / SL_HIT / BE_HIT event from updates_sent
so the trade manager does not suppress a genuine future close.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import logging

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService  # noqa: E402
from utils.helpers import load_config, calculate_pips  # noqa: E402

logger = logging.getLogger(__name__)

# Events that must be purged from updates_sent so the trade manager
# can detect the real close when it actually happens.
_CLEARABLE_EVENTS = {"TP2_HIT", "TP1_HIT", "SL_HIT", "TRAILING_SL_HIT", "BE_HIT",
                     "EXPIRED", "MANUAL_CLOSE", "MOVE_SL_TO_BE", "TRAILING_SL_UPDATED",
                     "EXIT_WARNING", "NEAR_TP1", "LONG_RUNNING", "PRICE_SANITY_FAILED",
                     "PARTIAL_CLOSE", "ORDER_FILLED"}


def _trade_id() -> str:
    return os.environ.get("TRADE_ID", "TRADE_20260706_124621_400170_3e420018").strip()


def _current_price() -> float | None:
    raw = os.environ.get("CURRENT_PRICE", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"Invalid CURRENT_PRICE: {raw}")


def _action() -> str:
    return os.environ.get("ACTION", "reopen").strip().lower()


def main() -> int:
    cfg = load_config()
    db = DatabaseService(cfg)
    trade_id = _trade_id()
    current_price = _current_price()
    action = _action()
    now = datetime.now(timezone.utc)
    now_iso = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")

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

    old_status = str(trade.get("status", "UNKNOWN")).upper()
    entry_price = float(trade.get("entry_price", 0))
    trade_type = str(trade.get("type") or trade.get("side") or "SELL").upper()
    symbol = str(trade.get("symbol") or "XAU/USD")

    print(f"🔍 Trade {trade_id}")
    print(f"   Symbol: {symbol}  |  Type: {trade_type}")
    print(f"   Entry: {entry_price}")
    print(f"   Old status: {old_status}")
    print(f"   Action: {action}")

    # Clean updates_sent
    old_updates = trade.get("updates_sent") or []
    cleaned_updates = [e for e in old_updates if e not in _CLEARABLE_EVENTS]
    if len(old_updates) != len(cleaned_updates):
        print(f"   Cleaned updates_sent: {len(old_updates)} → {len(cleaned_updates)} events")

    pnl = 0.0
    price_to_write = current_price or float(trade.get("current_price", entry_price))
    if current_price is not None and entry_price > 0:
        pnl = calculate_pips(entry_price, current_price, trade_type, symbol)
        print(f"   Current price: {current_price} → PnL: {pnl:+.1f} pts")

    if action == "be_hit":
        # ── Close as BE_HIT: price earned +150 pts before the corrupted close,
        #     then retraced to entry. The real outcome is breakeven. ──
        updates = {
            "status": "BE_HIT",
            "result": "BREAKEVEN",
            "sl_moved_to_entry": True,
            "stop_loss": round(entry_price, 2),
            "close_price": round(entry_price, 2),
            "final_pnl": 0.0,
            "final_pnl_points": 0.0,
            "current_price": round(price_to_write, 2),
            "current_pnl": round(pnl, 1),
            "current_pnl_points": round(pnl, 1),
            "max_favorable_excursion": max(float(trade.get("max_favorable_excursion", 0) or 0), 150.0),
            "max_adverse_excursion": 0.0,
            "closed_at": now_iso,
            "close_time": now_iso,
            "last_updated": now_iso,
            "updates_sent": [e for e in cleaned_updates if e not in {"BE_HIT", "MOVE_SL_TO_BE"}] + ["MOVE_SL_TO_BE", "BE_HIT"],
        }
        if "TP2_HIT" in old_updates:
            updates["updates_sent"] = [e for e in updates["updates_sent"] if e != "TP2_HIT"]
        db.update_trade(trade_id, updates)
        print(f"\n✅ Trade {trade_id} → BE_HIT (breakeven at entry {entry_price})")
        return 0

    # Default: reopen
    updates = {
        "status": "OPEN",
        "result": None,
        "closed_at": None,
        "close_time": None,
        "close_price": None,
        "final_pnl": None,
        "max_favorable_excursion": round(pnl, 1),
        "max_adverse_excursion": round(pnl, 1),
        "current_price": round(price_to_write, 2),
        "current_pnl": round(pnl, 1),
        "current_pnl_points": round(pnl, 1),
        "last_updated": now_iso,
        "updates_sent": cleaned_updates,
    }
    db.update_trade(trade_id, updates)
    print(f"\n✅ Trade {trade_id} restored → OPEN (PnL: {pnl:+.1f} pts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
