"""Demo execution handoff (operator directive 2026-08-10, thesis round).

On demo, the embedded paper-style manager may still DECIDE (thesis exits,
scale-outs, staleness cancellations) but must never BOOK execution: the tick
manager is the single owner of MT5 actions. This wrapper converts the
manager's close intents into `requested_exit` / `requested_partial` flags on
the trade row; the tick manager then executes at the broker FIRST and sends
the truthful card. Virtual bookings (partial_close, BE/SL moves, candle-hit
statuses) are dropped so they can never mask a real broker action.
"""
from __future__ import annotations

from typing import Any, Dict

CLOSE_INTENTS = {"THESIS_EXIT", "MANUAL_CLOSE"}


class DemoHandoffDB:
    """Pass-through DatabaseService proxy with booking filtering."""

    def __init__(self, db: Any):
        object.__setattr__(self, "_db", db)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_db"), name)

    def update_trade(self, tid: str, fields: Dict[str, Any], *a, **k) -> Any:
        db = object.__getattribute__(self, "_db")
        f = dict(fields or {})
        st = str(f.get("status") or "").upper()
        if st in CLOSE_INTENTS:
            reasons = f.get("reasons") or []
            reason = str(reasons[0]) if isinstance(reasons, list) and reasons else st
            return db.update_trade(tid, {
                "requested_exit": True,
                "requested_exit_reason": reason,
            })
        if st == "THESIS_SCALE_OUT":
            return db.update_trade(tid, {"requested_partial": True})
        if st == "CANCELLED":
            return db.update_trade(tid, f)  # staleness cancels flow to broker
        # virtual bookings / candle-detected closes: dropped (broker owns)
        for key in ("partial_close", "sl_moved_to_entry", "stop_loss",
                    "status", "close_price", "pnl_points"):
            f.pop(key, None)
        if not f:
            return True
        return db.update_trade(tid, f)
