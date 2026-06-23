"""Tests for the redesigned, outcome-aware duplicate-signal filter in
scripts/run_analysis.py (duplicate_signal_reason).

Two separated concerns are covered:
  1) open-position stacking protection (in-zone / any-price)
  2) recently-closed, outcome-aware cooldown (loss > breakeven > win), same-zone only
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import scripts.run_analysis as ra


class _FakeDB:
    def __init__(self, open_trades: List[Dict[str, Any]] | None = None,
                 recent_trades: List[Dict[str, Any]] | None = None):
        self._open = open_trades or []
        self._recent = recent_trades or []

    def get_open_trades(self) -> List[Dict[str, Any]]:
        return self._open

    def get_recent_trades(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._recent[:limit]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _decision(direction: str = "SELL", price: float = 4130.0) -> Dict[str, Any]:
    return {
        "decision": direction,
        "current_price": price,
        "signal": {"entry": {"price": price}},
    }


# Standard config reflecting the new structure.
CONFIG = {
    "duplicate_signal_filter": {
        "enabled": True,
        "price_zone_points": 50,  # 50 points = 5.0 USD
        "open_trade": {
            "block_same_direction_in_zone": True,
            "block_same_direction_any_price": False,
        },
        "cooldown": {
            "lookback_hours": 6,
            "after_loss_minutes": 90,
            "after_breakeven_minutes": 45,
            "after_win_minutes": 30,
        },
    }
}


# ── Disabled / non-directional ────────────────────────────────────────────
def test_disabled_filter_allows_everything():
    cfg = {"duplicate_signal_filter": {"enabled": False}}
    db = _FakeDB(open_trades=[{"id": "x", "type": "SELL", "entry_price": 4130.0, "status": "OPEN"}])
    assert ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, cfg) is None


def test_wait_decision_not_blocked():
    db = _FakeDB()
    assert ra.duplicate_signal_reason(_decision("WAIT", 4130.0), db, CONFIG) is None


# ── 1) Open-position stacking protection ──────────────────────────────────
def test_open_same_direction_in_zone_blocked():
    db = _FakeDB(open_trades=[{"id": "t1", "type": "SELL", "entry_price": 4131.0, "status": "OPEN"}])
    # 1.0 USD = 10 points away, within the 50-point zone -> blocked
    reason = ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, CONFIG)
    assert reason and "open" in reason.lower()


def test_open_same_direction_out_of_zone_allowed():
    db = _FakeDB(open_trades=[{"id": "t1", "type": "SELL", "entry_price": 4145.0, "status": "OPEN"}])
    # 15 USD = 150 points away, outside 50-point zone -> allowed
    assert ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, CONFIG) is None


def test_open_opposite_direction_ignored():
    db = _FakeDB(open_trades=[{"id": "t1", "type": "BUY", "entry_price": 4130.0, "status": "OPEN"}])
    assert ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, CONFIG) is None


def test_open_any_price_blocks_even_far():
    cfg = {
        "duplicate_signal_filter": {
            "enabled": True,
            "price_zone_points": 50,
            "open_trade": {"block_same_direction_any_price": True},
        }
    }
    db = _FakeDB(open_trades=[{"id": "t1", "type": "SELL", "entry_price": 4200.0, "status": "OPEN"}])
    reason = ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, cfg)
    assert reason and "one position per direction" in reason.lower()


def test_side_field_recognized_as_direction():
    db = _FakeDB(open_trades=[{"id": "t1", "side": "SELL", "entry_price": 4131.0, "status": "OPEN"}])
    assert ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, CONFIG) is not None


# ── 2) Outcome-aware cooldown for closed trades (same zone) ────────────────
def _closed(outcome_status: str, minutes_ago: float, price: float = 4131.0, result: str | None = None):
    closed = _now() - timedelta(minutes=minutes_ago)
    t = {
        "id": f"c_{outcome_status}",
        "type": "SELL",
        "entry_price": price,
        "status": outcome_status,
        "created_at": _iso(closed - timedelta(minutes=30)),
        "closed_at": _iso(closed),
    }
    if result:
        t["result"] = result
    return t


def test_loss_cooldown_blocks_within_window():
    # SL_HIT 60 min ago, same zone -> within 90-min loss cooldown -> blocked
    db = _FakeDB(recent_trades=[_closed("SL_HIT", 60)])
    reason = ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, CONFIG)
    assert reason and "LOSS" in reason


def test_loss_cooldown_expires():
    # SL_HIT 120 min ago -> past 90-min loss cooldown -> allowed
    db = _FakeDB(recent_trades=[_closed("SL_HIT", 120)])
    assert ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, CONFIG) is None


def test_win_cooldown_shorter_than_loss():
    # TP2_HIT (WIN) 40 min ago -> past 30-min win cooldown -> allowed,
    # even though a loss at the same age WOULD still be blocked.
    db_win = _FakeDB(recent_trades=[_closed("TP2_HIT", 40)])
    assert ra.duplicate_signal_reason(_decision("SELL", 4130.0), db_win, CONFIG) is None
    db_loss = _FakeDB(recent_trades=[_closed("SL_HIT", 40)])
    assert ra.duplicate_signal_reason(_decision("SELL", 4130.0), db_loss, CONFIG) is not None


def test_breakeven_cooldown_medium():
    # BE_HIT 30 min ago -> within 45-min breakeven cooldown -> blocked
    db = _FakeDB(recent_trades=[_closed("BE_HIT", 30)])
    reason = ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, CONFIG)
    assert reason and "BREAKEVEN" in reason


def test_closed_out_of_zone_allowed_even_if_recent():
    # Loss 10 min ago but 150 points away -> different setup -> allowed
    db = _FakeDB(recent_trades=[_closed("SL_HIT", 10, price=4145.0)])
    assert ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, CONFIG) is None


def test_closed_beyond_lookback_hours_ignored():
    # Loss in zone but 7 hours ago -> beyond 6-hour lookback -> allowed
    db = _FakeDB(recent_trades=[_closed("SL_HIT", 7 * 60)])
    assert ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, CONFIG) is None


def test_result_field_used_when_status_ambiguous():
    # status MANUAL_CLOSE but explicit result LOSS -> longest cooldown applies
    db = _FakeDB(recent_trades=[_closed("MANUAL_CLOSE", 60, result="LOSS")])
    reason = ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, CONFIG)
    assert reason and "LOSS" in reason


# ── Backward compatibility with legacy keys ───────────────────────────────
def test_legacy_keys_still_work():
    legacy = {
        "duplicate_signal_filter": {
            "enabled": True,
            "lookback_minutes": 90,
            "same_direction_price_zone_points": 50,
        }
    }
    # Open trade in zone should still block via legacy fallback defaults.
    db = _FakeDB(open_trades=[{"id": "t1", "type": "SELL", "entry_price": 4131.0, "status": "OPEN"}])
    assert ra.duplicate_signal_reason(_decision("SELL", 4130.0), db, legacy) is not None
