"""Tests for early breakeven (+N points) and trailing-before-TP1 behaviour."""

from __future__ import annotations

from datetime import datetime, timezone

from agents.open_trades_manager import OpenTradesManager


def _mgr(early=100.0, distance=100.0, step=30.0):
    cfg = {
        "trade_management": {"expire_after_hours": 0, "time_warning_hours": 999,
                             "auto_move_sl_to_entry_after_tp1": True},
        "trailing_stop": {"enabled": True, "trailing_distance": distance, "trailing_step": step,
                          "early_breakeven_points": early, "min_profit_lock": 0.0},
    }
    return OpenTradesManager(cfg)


def _buy_trade():
    return {"id": "T1", "type": "BUY", "status": "OPEN", "entry_price": 4000.0,
            "stop_loss": 3980.0, "tp1": 4026.7, "tp2": 4047.0,
            "sl_moved_to_entry": False, "updates_sent": []}


def _sell_trade():
    return {"id": "S1", "type": "SELL", "status": "OPEN", "entry_price": 4000.0,
            "stop_loss": 4020.0, "tp1": 3973.3, "tp2": 3953.0,
            "sl_moved_to_entry": False, "updates_sent": []}


NOW = datetime.now(timezone.utc)


def test_no_breakeven_below_threshold():
    ev = _mgr().evaluate_trade(_buy_trade(), 4005.0, now=NOW)  # +50 pts
    assert "MOVE_SL_TO_BE" not in ev["events"]
    assert ev["updates"].get("sl_moved_to_entry") is False


def test_early_breakeven_at_threshold_buy():
    ev = _mgr().evaluate_trade(_buy_trade(), 4010.0, now=NOW)  # +100 pts
    assert "MOVE_SL_TO_BE" in ev["events"]
    assert ev["updates"]["sl_moved_to_entry"] is True
    assert ev["updates"]["stop_loss"] == 4000.0  # moved to entry
    # Still OPEN (not TP1_HIT) — early BE doesn't fake a TP1.
    assert ev["new_status"] == "OPEN"


def test_early_breakeven_at_threshold_sell():
    ev = _mgr().evaluate_trade(_sell_trade(), 3990.0, now=NOW)  # +100 pts on SELL
    assert "MOVE_SL_TO_BE" in ev["events"]
    assert ev["updates"]["stop_loss"] == 4000.0


def test_trailing_after_early_breakeven_buy():
    t = _buy_trade()
    t.update({"sl_moved_to_entry": True, "stop_loss": 4000.0})
    ev = _mgr().evaluate_trade(t, 4015.0, now=NOW)  # +150 pts, trail 100 behind -> 4005
    assert "TRAILING_SL_UPDATED" in ev["events"]
    assert ev["updates"]["stop_loss"] == 4005.0


def test_required_trailing_rule_100_gap_30_step_buy():
    """Production rule: +100pts -> SL to entry; every extra 30pts moves SL 30pts.

    With a 100-point trailing gap, at +130pts the stop should be +30pts, and at
    +160pts the stop should be +60pts. Exact 30-point steps must trigger.
    """
    mgr = _mgr(early=100.0, distance=100.0, step=30.0)

    ev_be = mgr.evaluate_trade(_buy_trade(), 4010.0, now=NOW)  # +100 pts
    assert ev_be["events"] == ["MOVE_SL_TO_BE"]
    assert ev_be["updates"]["stop_loss"] == 4000.0

    t = _buy_trade()
    t.update({"sl_moved_to_entry": True, "stop_loss": 4000.0})
    ev_130 = mgr.evaluate_trade(t, 4013.0, now=NOW)  # +130 pts
    assert "TRAILING_SL_UPDATED" in ev_130["events"]
    assert ev_130["updates"]["stop_loss"] == 4003.0
    assert round((4013.0 - ev_130["updates"]["stop_loss"]) * 10, 1) == 100.0

    t.update({"stop_loss": 4003.0})
    ev_160 = mgr.evaluate_trade(t, 4016.0, now=NOW)  # +160 pts
    assert "TRAILING_SL_UPDATED" in ev_160["events"]
    assert ev_160["updates"]["stop_loss"] == 4006.0
    assert round((4016.0 - ev_160["updates"]["stop_loss"]) * 10, 1) == 100.0


def test_trailing_never_moves_backward():
    t = _buy_trade()
    t.update({"sl_moved_to_entry": True, "stop_loss": 4010.0})
    # Price pulls back to 4012 -> candidate 3912 < current stop, must NOT move.
    ev = _mgr().evaluate_trade(t, 4012.0, now=NOW)
    assert "TRAILING_SL_UPDATED" not in ev["events"]
    assert "stop_loss" not in ev["updates"] or ev["updates"]["stop_loss"] == 4010.0


def test_trailing_sl_hit_closes_as_win():
    t = _buy_trade()
    t.update({"sl_moved_to_entry": True, "stop_loss": 4010.0})  # trailed +100 locked
    ev = _mgr().evaluate_trade(t, 4010.0, now=NOW)  # pullback to trailed stop
    assert "TRAILING_SL_HIT" in ev["events"]
    assert ev["updates"]["result"] == "WIN"
    assert ev["updates"]["final_pnl"] == 100.0


def test_disabled_early_breakeven_keeps_legacy():
    # early_breakeven_points=0 -> no BE while OPEN before TP1.
    ev = _mgr(early=0.0).evaluate_trade(_buy_trade(), 4010.0, now=NOW)
    assert "MOVE_SL_TO_BE" not in ev["events"]
    assert ev["updates"].get("sl_moved_to_entry") is False


def test_tp1_still_moves_breakeven_and_partials():
    # Hitting TP1 must still work (partial + BE) as before.
    ev = _mgr().evaluate_trade(_buy_trade(), 4026.7, now=NOW)
    assert ev["new_status"] == "TP1_HIT"
    assert "TP1_HIT" in ev["events"]
    assert ev["updates"]["partial_close"] is True
    assert ev["updates"]["sl_moved_to_entry"] is True
