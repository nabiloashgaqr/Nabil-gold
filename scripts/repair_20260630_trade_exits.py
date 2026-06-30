"""Repair 2026-06-30 XAU/USD exits affected by old trailing order.

Use after the high/low trailing fix when a protected SELL candle made a deep low
then rebounded. The old code checked the previous trailing stop before advancing
trailing from the candle low, so trades could be closed too early.

GitHub Actions env:
  TRADE_IDS     comma-separated IDs. Defaults to the three known 2026-06-30 IDs.
  CANDLE_LOW    lowest XAU/USD spot price reached by that candle (e.g. 3943.376)
  CANDLE_HIGH   optional candle high / rebound price. If omitted, assumes rebound
                reached the corrected stop because these trades are already SL_HIT.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import DatabaseService
from utils.helpers import calculate_pips, load_config, setup_logging
from utils.instruments import points_to_price, price_decimals

setup_logging()

DEFAULT_TRADE_IDS = [
    "TRADE_20260630_000052_813919_95677c6b",
    "TRADE_20260630_000541_812389_c469be2f",
    "TRADE_20260630_003542_667024_7e662953",
]


def _ids() -> list[str]:
    raw = os.environ.get("TRADE_IDS", "").strip()
    if not raw:
        return DEFAULT_TRADE_IDS
    return [x.strip() for x in raw.split(",") if x.strip()]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_trade(db: DatabaseService, trade_id: str) -> Dict[str, Any] | None:
    client = getattr(db, "client", None)
    if getattr(db, "use_supabase", False) and client is not None:
        res = client.table("trades").select("*").eq("id", trade_id).limit(1).execute()
        rows = list(res.data or [])
        return rows[0] if rows else None
    for t in db.get_recent_trades(limit=200):
        if str(t.get("id")) == trade_id:
            return t
    return None


def main() -> None:
    config = load_config()
    db = DatabaseService(config)
    symbol = "XAU/USD"
    low = _f(os.environ.get("CANDLE_LOW"))
    if low <= 0:
        raise SystemExit("CANDLE_LOW is required, e.g. CANDLE_LOW=3943.376")
    high_raw = os.environ.get("CANDLE_HIGH")
    high = _f(high_raw, 0.0) if high_raw else 0.0

    trailing_points = float((config.get("trailing_stop", {}) or {}).get("trailing_distance", 100.0) or 100.0)
    trailing_distance = points_to_price(trailing_points, symbol)
    decimals = price_decimals(symbol)
    corrected_sell_stop = round(low + trailing_distance, decimals)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    print(f"Repair candle low={low}, corrected SELL trailing stop={corrected_sell_stop}")
    for trade_id in _ids():
        trade = _get_trade(db, trade_id)
        if not trade:
            print(f"SKIP {trade_id}: not found")
            continue
        side = str(trade.get("type") or trade.get("side") or "").upper()
        if side != "SELL":
            print(f"SKIP {trade_id}: side={side}, only SELL repair supported")
            continue
        entry = _f(trade.get("entry_price"))
        tp2 = _f(trade.get("tp2"))
        if entry <= 0:
            print(f"SKIP {trade_id}: missing entry")
            continue

        if tp2 > 0 and low <= tp2:
            status = "TP2_HIT"
            close_price = round(tp2, decimals)
            final_pnl = round(calculate_pips(entry, close_price, side, symbol), 1)
            event_note = "repaired_to_TP2_from_candle_low"
        else:
            close_price = corrected_sell_stop
            if high and high < close_price:
                print(f"SKIP {trade_id}: CANDLE_HIGH={high} did not rebound to corrected stop={close_price}")
                continue
            final_pnl = round(calculate_pips(entry, close_price, side, symbol), 1)
            status = "SL_HIT"
            event_note = "repaired_to_corrected_trailing_sl_from_candle_low"

        updates = {
            "status": status,
            "result": "WIN" if final_pnl > 0 else "BREAKEVEN" if final_pnl == 0 else "LOSS",
            "close_price": close_price,
            "current_price": close_price,
            "stop_loss": close_price if status == "SL_HIT" else trade.get("stop_loss"),
            "final_pnl": final_pnl,
            "final_pnl_points": final_pnl,
            "current_pnl": final_pnl,
            "current_pnl_points": final_pnl,
            "last_candle_low": round(low, decimals),
            "last_candle_high": round(high, decimals) if high else trade.get("last_candle_high"),
            "closed_at": trade.get("closed_at") or now,
            "close_time": trade.get("close_time") or now,
            "last_updated": now,
            "repair_note": event_note,
        }
        db.update_trade(trade_id, updates)
        print(f"UPDATED {trade_id}: {status} close={close_price} pnl={final_pnl} ({event_note})")


if __name__ == "__main__":
    main()
