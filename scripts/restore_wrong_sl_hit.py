"""Restore trades that were falsely closed by a bad fallback price.

Usage in GitHub Actions/local shell:

    TRADE_IDS="TRADE_... , TRADE_..." CURRENT_PRICE=4045.00 python scripts/restore_wrong_sl_hit.py

It reopens the listed trades by clearing close/result fields and setting status
back to OPEN. It does NOT change entry, TP, or stop_loss.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService  # noqa: E402
from utils.helpers import load_config, calculate_pips  # noqa: E402


DEFAULT_TRADE_IDS = [
    "TRADE_20260629_100239_933911_2576427e",
]


def _ids() -> list[str]:
    raw = os.environ.get("TRADE_IDS", "").strip()
    if not raw:
        return DEFAULT_TRADE_IDS
    return [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()]


def _current_price() -> float | None:
    raw = os.environ.get("CURRENT_PRICE", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"Invalid CURRENT_PRICE: {raw}")


def main() -> int:
    cfg = load_config()
    db = DatabaseService(cfg)
    ids = _ids()
    current_price = _current_price()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    # Fetch recent trades to calculate optional current_pnl if possible.
    recent = {str(t.get("id")): t for t in db.get_recent_trades(limit=200)}
    recent.update({str(t.get("id")): t for t in db.get_open_trades()})

    for trade_id in ids:
        trade = recent.get(trade_id, {})
        updates = {
            "status": "OPEN",
            "result": None,
            "closed_at": None,
            "close_time": None,
            "close_price": None,
            "final_pnl": None,
            "last_updated": now,
        }
        if current_price is not None:
            updates["current_price"] = round(current_price, 2)
            entry = trade.get("entry_price")
            trade_type = str(trade.get("type") or trade.get("side") or "SELL").upper()
            symbol = str(trade.get("symbol") or cfg.get("symbol", "XAU/USD"))
            try:
                pnl = calculate_pips(float(entry), current_price, trade_type, symbol)
                updates["current_pnl"] = round(pnl, 1)
                updates["current_pnl_points"] = round(pnl, 1)
            except Exception:
                pass
        db.update_trade(trade_id, updates)
        print(f"Restored {trade_id} -> OPEN")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
