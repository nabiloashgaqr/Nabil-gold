"""Tests for phase-four open trades manager."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.open_trades_manager import OpenTradesManager


def base_trade(**overrides):
    trade = {
        "id": "TRADE_TEST_001",
        "type": "BUY",
        "entry_price": 2350.0,
        "entry_time": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
        "stop_loss": 2344.0,
        "tp1": 2356.0,
        "tp2": 2362.0,
        "status": "OPEN",
        "current_price": 2350.0,
        "current_pnl": 0,
        "sl_moved_to_entry": False,
        "partial_close": False,
        "updates_sent": [],
    }
    trade.update(overrides)
    return trade


def test_near_tp1_event_once() -> None:
    manager = OpenTradesManager({"trade_management": {"near_tp1_progress": 0.8, "time_warning_hours": 4, "expire_after_hours": 8}})
    result = manager.evaluate_trade(base_trade(), 2354.9)
    assert "NEAR_TP1" in result["events"]
    assert "NEAR_TP1" in result["updates"]["updates_sent"]

    repeated = manager.evaluate_trade(base_trade(updates_sent=["NEAR_TP1"]), 2354.9)
    assert "NEAR_TP1" not in repeated["events"]


def test_tp1_moves_to_break_even() -> None:
    manager = OpenTradesManager({"trade_management": {"auto_move_sl_to_entry_after_tp1": True}})
    result = manager.evaluate_trade(base_trade(), 2356.1)
    assert result["new_status"] == "TP1_HIT"
    assert "TP1_HIT" in result["events"]
    assert "MOVE_SL_TO_BE" in result["events"]
    assert result["updates"]["sl_moved_to_entry"] is True
    assert result["updates"]["partial_close"] is True


def test_tp2_closes_trade() -> None:
    manager = OpenTradesManager()
    result = manager.evaluate_trade(base_trade(status="TP1_HIT", sl_moved_to_entry=True), 2362.2)
    assert result["new_status"] == "TP2_HIT"
    assert result["updates"]["result"] == "WIN"
    assert result["updates"]["final_pnl"] > 0


def test_be_hit_after_tp1() -> None:
    manager = OpenTradesManager()
    result = manager.evaluate_trade(base_trade(status="TP1_HIT", sl_moved_to_entry=True), 2350.0)
    assert result["new_status"] == "BE_HIT"
    assert result["updates"]["result"] == "BREAKEVEN"
    assert result["updates"]["final_pnl"] == 0


def test_long_running_and_expired() -> None:
    manager = OpenTradesManager({"trade_management": {"time_warning_hours": 4, "expire_after_hours": 8}})
    old_trade = base_trade(entry_time=(datetime.now(timezone.utc) - timedelta(hours=9)).isoformat())
    result = manager.evaluate_trade(old_trade, 2351.0)
    assert "LONG_RUNNING" in result["events"]
    assert "EXPIRED" in result["events"]
    assert result["new_status"] == "EXPIRED"


def test_tp1_persists_actual_breakeven_stop_loss() -> None:
    """Before this fix, sl_moved_to_entry was set as a flag but the stop_loss
    column itself was never actually updated in the DB."""
    manager = OpenTradesManager({"trade_management": {"auto_move_sl_to_entry_after_tp1": True}})
    result = manager.evaluate_trade(base_trade(), 2356.1)
    assert result["updates"]["stop_loss"] == 2350.0  # == entry_price


def test_trailing_disabled_via_config_no_progressive_movement() -> None:
    manager = OpenTradesManager({"trailing_stop": {"enabled": False}})
    assert manager.trailing_enabled is False
    trade = base_trade(status="TP1_HIT", sl_moved_to_entry=True, stop_loss=2350.0)
    result = manager.evaluate_trade(trade, 2359.0)  # well past breakeven
    assert "stop_loss" not in result["updates"]


def test_trailing_moves_stop_loss_forward_for_buy() -> None:
    manager = OpenTradesManager(
        {"trailing_stop": {"enabled": True, "trailing_distance": 20.0, "trailing_step": 5.0}}
    )
    # price is 2356.1 in points -> entry 2350, current_price 2350 + 9.0 = 2359.0 (90 points up)
    trade = base_trade(status="TP1_HIT", sl_moved_to_entry=True, stop_loss=2350.0)
    result = manager.evaluate_trade(trade, 2359.0)
    assert result["new_status"] == "TP1_HIT"  # still open, not closed
    assert "TRAILING_SL_UPDATED" in result["events"]
    # new SL = current_price - trailing_distance(in price units: 20 points = 2.0 price)
    assert result["updates"]["stop_loss"] == 2357.0
    assert result["updates"]["stop_loss"] > 2350.0  # moved forward from breakeven


def test_trailing_never_moves_backward_on_pullback() -> None:
    manager = OpenTradesManager(
        {"trailing_stop": {"enabled": True, "trailing_distance": 20.0, "trailing_step": 5.0}}
    )
    # Stop already trailed to 2357.0 from a previous run; price pulls back a bit
    # but not enough to justify moving the stop further forward.
    trade = base_trade(status="TP1_HIT", sl_moved_to_entry=True, stop_loss=2357.0)
    result = manager.evaluate_trade(trade, 2358.0)
    assert "stop_loss" not in result["updates"]  # not enough favorable movement yet
    assert "TRAILING_SL_UPDATED" not in result["events"]


def test_trailing_respects_min_profit_lock_floor() -> None:
    manager = OpenTradesManager(
        {"trailing_stop": {"enabled": True, "trailing_distance": 20.0, "trailing_step": 1.0, "min_profit_lock": 10.0}}
    )
    # Price has only moved marginally past breakeven; raw current_price - distance
    # would land BELOW entry + min_profit_lock, so it must be floored there instead.
    trade = base_trade(status="TP1_HIT", sl_moved_to_entry=True, stop_loss=2350.0)
    result = manager.evaluate_trade(trade, 2351.0)
    assert result["updates"]["stop_loss"] == 2351.0  # entry(2350) + min_profit_lock(10pts=1.0)


def test_trailing_stop_hit_closes_as_win_with_locked_profit() -> None:
    manager = OpenTradesManager(
        {"trailing_stop": {"enabled": True, "trailing_distance": 20.0, "trailing_step": 5.0}}
    )
    # Stop has already been trailed to 2357.0 (beyond entry 2350) by a previous run.
    trade = base_trade(status="TP1_HIT", sl_moved_to_entry=True, stop_loss=2357.0)
    result = manager.evaluate_trade(trade, 2357.0)  # price pulls back exactly onto the trailed stop
    assert result["new_status"] == "SL_HIT"
    assert "TRAILING_SL_HIT" in result["events"]
    assert result["updates"]["result"] == "WIN"
    assert result["updates"]["final_pnl"] == 70.0  # (2357-2350)*10 points
    assert result["updates"]["close_price"] == 2357.0


def test_plain_breakeven_hit_still_works_when_stop_not_yet_trailed() -> None:
    """Regression guard: when stop_loss is still at literal breakeven (not yet
    trailed forward), a pullback to entry must still classify as BE_HIT/BREAKEVEN,
    not as a TRAILING_SL_HIT/WIN."""
    manager = OpenTradesManager(
        {"trailing_stop": {"enabled": True, "trailing_distance": 20.0, "trailing_step": 5.0}}
    )
    trade = base_trade(status="TP1_HIT", sl_moved_to_entry=True, stop_loss=2350.0)  # == entry
    result = manager.evaluate_trade(trade, 2350.0)
    assert result["new_status"] == "BE_HIT"
    assert result["updates"]["result"] == "BREAKEVEN"
    assert "TRAILING_SL_HIT" not in result["events"]
