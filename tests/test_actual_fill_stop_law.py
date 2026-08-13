"""Actual broker fill must still obey the operator's 270..400 stop law."""
from __future__ import annotations

import types

from scripts.run_tick_manager import TickManager, corrected_execution_stop


def test_live_buy_example_is_corrected_from_246_to_270_points() -> None:
    stop, required, actual = corrected_execution_stop(
        "BUY", actual_entry=4392.63, current_stop=4368.00,
        planned_entry=4395.00, planned_stop=4368.00,
    )
    assert actual == 246.3
    assert required == 270.0
    assert stop == 4365.63


def test_sell_side_is_mirrored() -> None:
    stop, required, actual = corrected_execution_stop(
        "SELL", actual_entry=4392.63, current_stop=4417.26,
        planned_entry=4390.00, planned_stop=4417.00,
    )
    assert actual == 246.3
    assert required == 270.0
    assert stop == 4419.63


def test_legal_structural_stop_is_not_moved() -> None:
    stop, required, actual = corrected_execution_stop(
        "BUY", actual_entry=4392.63, current_stop=4362.63,
        planned_entry=4395.00, planned_stop=4368.00,
    )
    assert stop is None
    assert required == 270.0
    assert actual == 300.0


def test_be_or_profitable_stop_is_never_widened() -> None:
    stop, required, actual = corrected_execution_stop(
        "BUY", actual_entry=4392.63, current_stop=4395.00,
        planned_entry=4395.00, planned_stop=4368.00,
    )
    assert (stop, required, actual) == (None, 0.0, 0.0)


class _DB:
    def __init__(self):
        self.updates = []

    def update_trade(self, tid, updates):
        self.updates.append((tid, dict(updates)))


class _Executor:
    def __init__(self):
        self.stops = []

    def apply_stop(self, tid, stop, tp, symbol):
        self.stops.append((tid, stop, tp, symbol))
        return True


def test_tick_manager_repairs_legacy_live_row_before_management() -> None:
    db = _DB()
    tm = TickManager({
        "trading_rules": {"stop": {
            "min_liquidity_points": 200,
            "safety_buffer_points": 70,
            "max_stop_points": 400,
        }}
    }, database=db)
    tm._notify = lambda text: None
    row = {
        "id": "LIVE", "symbol": "XAU/USD", "type": "BUY",
        "entry_price": 4392.63, "stop_loss": 4368.0,
        "initial_stop_loss": 4368.0, "tp2": 4450.0,
        "sl_moved_to_entry": False, "partial_close": False,
        "signal_snapshot": {"signal": {
            "entry": {"price": 4395.0}, "stop_loss": 4368.0,
        }},
    }
    pos = types.SimpleNamespace(price_open=4392.63, sl=4368.0)
    ex = _Executor()
    actual = tm._normalize_execution_stop(row, pos, ex, "BUY")
    assert actual == 4365.63
    assert ex.stops == [("LIVE", 4365.63, 4450.0, "XAU/USD")]
    assert row["stop_loss"] == 4365.63
    assert row["initial_stop_loss"] == 4365.63
    assert row["execution_stop_points"] == 270.0
    assert row["execution_stop_normalized"] is True
