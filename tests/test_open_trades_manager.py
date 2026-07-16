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


def test_buy_tp1_detected_from_candle_high_even_if_close_below_target() -> None:
    manager = OpenTradesManager({"trade_management": {"auto_move_sl_to_entry_after_tp1": True}})
    # Close is below TP1, but the 5m candle high touched TP1.
    result = manager.evaluate_trade(base_trade(), 2354.0, candle_high=2356.2, candle_low=2352.0)
    assert result["new_status"] == "TP1_HIT"
    assert "TP1_HIT" in result["events"]
    assert "MOVE_SL_TO_BE" in result["events"]
    assert result["updates"]["last_candle_high"] == 2356.2
    assert result["updates"]["last_candle_low"] == 2352.0


def test_sell_stop_loss_detected_from_candle_high_even_if_close_below_stop() -> None:
    manager = OpenTradesManager()
    trade = base_trade(type="SELL", entry_price=2350.0, stop_loss=2356.0, tp1=2344.0, tp2=2338.0)
    # Close is still below SL, but the 5m candle high touched the SELL stop.
    result = manager.evaluate_trade(trade, 2353.0, candle_high=2356.2, candle_low=2348.5)
    assert result["new_status"] == "SL_HIT"
    assert "SL_HIT" in result["events"]
    assert result["updates"]["result"] == "LOSS"
    assert result["updates"]["close_price"] == 2356.0


def test_same_candle_touching_tp_and_sl_uses_conservative_stop_priority() -> None:
    manager = OpenTradesManager()
    # BUY trade: high touches TP2, low touches SL in the same 5m candle.
    # With OHLC only, order is unknown, so the manager must choose SL.
    result = manager.evaluate_trade(base_trade(), 2351.0, candle_high=2362.5, candle_low=2343.8)
    assert result["new_status"] == "SL_HIT"
    assert "SL_HIT" in result["events"]
    assert "TP2_HIT" not in result["events"]
    assert result["updates"]["result"] == "LOSS"
    assert result["updates"]["close_price"] == 2344.0


def test_pending_sell_limit_fills_from_candle_high_touch() -> None:
    manager = OpenTradesManager({"order_execution": {"entry_style": "market"}})
    trade = base_trade(type="SELL", status="PENDING", order_type="SELL_LIMIT", entry_price=2355.0)
    # Close is below entry, but high touched the pending sell limit.
    result = manager.evaluate_trade(trade, 2352.0, candle_high=2355.1, candle_low=2350.0)
    assert result["new_status"] == "OPEN"
    assert result["events"] == ["ORDER_FILLED"]


def test_pending_order_expires_after_hours_when_not_filled() -> None:
    manager = OpenTradesManager({"order_execution": {"entry_style": "hybrid", "pending_expire_after_hours": 4, "pending_order_max_cycles": 99}})
    trade = base_trade(
        type="SELL",
        status="PENDING",
        order_type="SELL_LIMIT",
        entry_price=2360.0,
        entry_time=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
    )
    result = manager.evaluate_trade(trade, 2352.0, candle_high=2354.0, candle_low=2350.0)
    assert result["new_status"] == "EXPIRED"
    assert result["events"] == ["EXPIRED"]
    assert result["updates"]["result"] == "EXPIRED"


def test_pending_touched_during_news_blackout_enters_news_hold() -> None:
    manager = OpenTradesManager({"order_execution": {"entry_style": "hybrid", "pending_news_hold": {"enabled": True, "reactivation_delay_minutes": 3}}})
    trade = base_trade(type="SELL", status="PENDING", order_type="SELL_LIMIT", entry_price=2355.0)
    result = manager.evaluate_trade(trade, 2352.0, candle_high=2355.1, candle_low=2350.0, news_blocked=True, news_context={"market_status": "DANGER"})
    assert result["new_status"] == "PENDING"
    assert result["events"] == ["NEWS_HOLD"]
    runtime = result["updates"]["signal_snapshot"]["pending_runtime"]
    assert runtime["news_hold_active"] is True


def test_pending_news_hold_reactivates_to_market_after_block_clears() -> None:
    manager = OpenTradesManager({"order_execution": {"entry_style": "hybrid", "pending_news_hold": {"enabled": True, "reactivation_delay_minutes": 0, "limit_max_drift_points": 40}}})
    trade = base_trade(
        type="SELL",
        status="PENDING",
        order_type="SELL_LIMIT",
        entry_price=2355.0,
        stop_loss=2365.0,
        tp2=2330.0,
        signal_snapshot={"pending_runtime": {"news_hold_active": True, "touch_time": datetime.now(timezone.utc).isoformat()}},
    )
    result = manager.evaluate_trade(trade, 2352.5, candle_high=2354.0, candle_low=2351.0, news_blocked=False)
    assert result["new_status"] == "OPEN"
    assert result["events"] == ["ORDER_FILLED"]
    assert result["updates"]["entry_price"] == 2352.5


def test_protected_sell_tp2_has_priority_when_candle_low_hits_tp2_then_rebounds() -> None:
    """Regression: protected SELL should not close at old trailing SL when the
    same candle low already reached TP2, even if it later rebounds above SL.
    """
    manager = OpenTradesManager({"trailing_stop": {"enabled": True, "trailing_distance": 100.0, "trailing_step": 30.0}})
    trade = base_trade(
        type="SELL",
        status="TP1_HIT",
        entry_price=4015.41,
        stop_loss=3971.15,
        tp1=3975.41,
        tp2=3945.41,
        sl_moved_to_entry=True,
        partial_close=True,
    )
    result = manager.evaluate_trade(trade, 3967.01, candle_high=3987.56, candle_low=3943.37)
    assert result["new_status"] == "TP2_HIT"
    assert "TP2_HIT" in result["events"]
    assert "TRAILING_SL_HIT" not in result["events"]
    assert result["updates"]["close_price"] == 3945.41
    assert result["updates"]["final_pnl"] == 700.0


def test_protected_sell_trailing_uses_candle_low_before_pullback_stop_hit() -> None:
    """Regression: trailing for SELL must use candle LOW + distance, not close +
    distance, before checking whether the rebound hit the new stop.
    """
    manager = OpenTradesManager({"trailing_stop": {"enabled": True, "trailing_distance": 100.0, "trailing_step": 30.0}})
    trade = base_trade(
        type="SELL",
        status="TP1_HIT",
        entry_price=4002.03,
        stop_loss=3971.15,
        tp1=3962.03,
        tp2=3932.03,
        sl_moved_to_entry=True,
        partial_close=True,
    )
    result = manager.evaluate_trade(trade, 3967.01, candle_high=3987.56, candle_low=3943.37)
    assert result["new_status"] == "SL_HIT"
    assert "TRAILING_SL_HIT" in result["events"]
    # low 3943.37 + 100 points ($10) = 3953.37
    assert result["updates"]["close_price"] == 3953.37
    assert result["updates"]["stop_loss"] == 3953.37
    assert result["updates"]["final_pnl"] == 486.6
