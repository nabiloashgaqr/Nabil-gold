"""Tests for pending (LIMIT/STOP) order handling — the fix for phantom fills.

Regression: a SELL LIMIT @ 4101 placed while price was 4068 was stored as OPEN
and immediately reported TP1 (+277 pts) although price never traded up to 4101.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from agents.open_trades_manager import OpenTradesManager
from services.database import DatabaseService


NOW = datetime.now(timezone.utc)


def _mgr():
    return OpenTradesManager({
        "trade_management": {"expire_after_hours": 24},
        "trailing_stop": {"enabled": True, "early_breakeven_points": 100, "trailing_distance": 100},
    })


def _pending(order_type, entry, **over):
    t = {"id": "P", "type": "SELL" if "SELL" in order_type else "BUY", "status": "PENDING",
         "order_type": order_type, "entry_price": entry, "stop_loss": entry + 20,
         "tp1": entry - 26, "tp2": entry - 46, "entry_time": NOW.isoformat(),
         "created_at": NOW.isoformat(), "updates_sent": []}
    t.update(over)
    return t


# ── Manager: fill-on-touch ────────────────────────────────────────────────
def test_sell_limit_not_filled_below_entry():
    t = _pending("SELL_LIMIT", 4101.05)
    ev = _mgr().evaluate_trade(t, 4068.24, now=NOW)
    assert ev["new_status"] == "PENDING"
    assert ev["pnl_points"] == 0.0
    assert "TP1_HIT" not in ev["events"]


def test_sell_limit_fills_when_price_reaches_entry():
    t = _pending("SELL_LIMIT", 4101.05)
    ev = _mgr().evaluate_trade(t, 4101.5, now=NOW)
    assert ev["new_status"] == "OPEN"
    assert "ORDER_FILLED" in ev["events"]
    assert ev["pnl_points"] == 0.0  # just filled, no profit yet


def test_buy_limit_fills_when_price_falls_to_entry():
    t = _pending("BUY_LIMIT", 4000.0)
    assert _mgr().evaluate_trade(t, 4001.0, now=NOW)["new_status"] == "PENDING"
    assert _mgr().evaluate_trade(t, 3999.5, now=NOW)["new_status"] == "OPEN"


def test_sell_stop_fills_when_price_falls_through_entry():
    t = _pending("SELL_STOP", 4050.0)
    assert _mgr().evaluate_trade(t, 4060.0, now=NOW)["new_status"] == "PENDING"
    assert _mgr().evaluate_trade(t, 4049.0, now=NOW)["new_status"] == "OPEN"


def test_buy_stop_fills_when_price_rises_through_entry():
    t = _pending("BUY_STOP", 4150.0)
    assert _mgr().evaluate_trade(t, 4140.0, now=NOW)["new_status"] == "PENDING"
    assert _mgr().evaluate_trade(t, 4151.0, now=NOW)["new_status"] == "OPEN"


def test_filled_then_reaches_tp1():
    # After activation (OPEN @4101), drop to TP1 4074 -> real TP1.
    t = _pending("SELL_LIMIT", 4101.05, status="OPEN")
    ev = _mgr().evaluate_trade(t, 4074.0, now=NOW)
    assert ev["new_status"] == "TP1_HIT"
    assert ev["pnl_points"] > 0


# ── DB: PENDING persistence + cancellation ────────────────────────────────
def _db(tmp):
    return DatabaseService({"database": {"provider": "local", "local_fallback_file": str(tmp / "t.json")}})


def test_limit_order_saved_as_pending(tmp_path):
    db = _db(tmp_path)
    decision = {
        "decision": "SELL", "current_price": 4068.24,
        "signal": {"type": "SELL", "entry": {"price": 4101.05, "kind": "LIMIT", "order_type": "SELL_LIMIT"},
                   "entry_kind": "LIMIT", "order_type": "SELL_LIMIT",
                   "stop_loss": 4121.05, "tp1": 4074.38, "tp2": 4054.38},
    }
    tid = db.save_trade(decision)
    saved = [t for t in db.get_open_trades() if t["id"] == tid][0]
    assert saved["status"] == "PENDING"
    assert saved["order_kind"] == "LIMIT"


def test_market_order_saved_as_open(tmp_path):
    db = _db(tmp_path)
    decision = {
        "decision": "BUY", "current_price": 4000.0,
        "signal": {"type": "BUY", "entry": {"price": 4000.0, "kind": "MARKET", "order_type": "BUY_MARKET"},
                   "entry_kind": "MARKET", "order_type": "BUY_MARKET",
                   "stop_loss": 3980.0, "tp1": 4026.0, "tp2": 4046.0},
    }
    tid = db.save_trade(decision)
    saved = [t for t in db.get_open_trades() if t["id"] == tid][0]
    assert saved["status"] == "OPEN"


def test_cancel_pending_orders(tmp_path):
    db = _db(tmp_path)
    decision = {
        "decision": "SELL", "current_price": 4068.0,
        "signal": {"type": "SELL", "entry": {"price": 4101.0, "kind": "LIMIT", "order_type": "SELL_LIMIT"},
                   "entry_kind": "LIMIT", "order_type": "SELL_LIMIT",
                   "stop_loss": 4121.0, "tp1": 4074.0, "tp2": 4054.0},
    }
    db.save_trade(decision)
    n = db.cancel_pending_orders("Replaced by a newer signal")
    assert n == 1
    # No more pending/active orders.
    assert all(t.get("status") != "PENDING" for t in db.get_open_trades())
