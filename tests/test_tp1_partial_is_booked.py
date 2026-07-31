"""TP1's "50% partial close" must actually reduce the position.

2026-07-31, trade TRADE_20260731_060253_794033_d917b1d5 (SELL 4074.78):

    Take Profit 2 Hit
    Exit Price: 4029.17
    Actual PnL: +456.1 pts

456.1 is the full position settled at the TP2 price. But config.json has
always said:

    "partial_close_at_tp1": true,
    "partial_close_percentage": 50

Half the position left at TP1 = 4051.98 (+228.0 on that half), and the
remainder ran to TP2 = 4029.17 (+228.05 on that half). The account made
+342.1. The card claimed +456.1 -- 114 points that never existed.

Nothing in the codebase read `partial_close_percentage`. TP1 set a
`partial_close` boolean and the position kept running at full size, so the
closing branch settled 100% at the closing price. The same class of fault
was already found and fixed for the thesis scale-out; TP1 was missed.

This matters beyond one card. `final_pnl` is what the scoreboard, the weekly
report and the learning service consume, so every two-target trade taught
the system a payoff the account never received -- and the inflation is
one-directional, so it cannot average out.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager  # noqa: E402
from utils.helpers import calculate_pips, load_config  # noqa: E402

ENTRY = 4074.78
TP1 = 4051.98
TP2 = 4029.17
SYMBOL = "XAU/USD"

TP1_MOMENT = datetime(2026, 7, 31, 13, 45, tzinfo=timezone.utc)
TP2_MOMENT = datetime(2026, 7, 31, 13, 50, tzinfo=timezone.utc)


def _manager() -> OpenTradesManager:
    return OpenTradesManager(load_config())


def _trade(status: str, stop: float, **extra) -> dict:
    trade = {
        "id": "TRADE_20260731_060253_794033_d917b1d5",
        "symbol": SYMBOL, "type": "SELL", "status": status,
        "entry_price": ENTRY, "stop_loss": stop, "initial_stop_loss": 4089.78,
        "tp1": TP1, "tp2": TP2,
        "entry_time": "2026-07-31T06:02:53+00:00",
        "created_at": "2026-07-31T06:02:53+00:00",
        "last_updated": "2026-07-31T13:40:00+00:00",
        "sl_moved_to_entry": False, "partial_close": False,
        "max_favorable_excursion": 250.0, "max_adverse_excursion": -69.3,
        "updates_sent": ["ORDER_FILLED"], "signal_snapshot": {},
    }
    trade.update(extra)
    return trade


def _take_tp1(manager: OpenTradesManager) -> dict:
    return manager.evaluate_trade(
        _trade("OPEN", ENTRY, sl_moved_to_entry=True),
        current_price=4051.00, now=TP1_MOMENT,
        candle_high=4058.00, candle_low=4050.00,
        recent_candles=[{"time": "2026-07-31T13:44:00Z", "open": 4057.0,
                         "high": 4058.00, "low": 4050.00, "close": 4051.0}],
    )


def test_tp1_books_half_the_position_at_the_tp1_price() -> None:
    result = _take_tp1(_manager())
    updates = result["updates"]

    assert "TP1_HIT" in result["events"]
    assert updates["partial_close"] is True
    assert updates["closed_fraction"] == 0.5, (
        "config promises 50% at TP1; a boolean flag with no size behind it is "
        "not a partial close"
    )
    # Booked at TP1 itself, not at the candle close: TP1 is a limit order.
    expected = round(calculate_pips(ENTRY, TP1, "SELL", SYMBOL) * 0.5, 1)
    assert updates["realized_pnl_points"] == expected == 114.0
    assert updates["scale_out_price"] == TP1


def test_tp2_settles_only_the_half_that_was_still_running() -> None:
    """The exact number the card overstated."""
    manager = _manager()
    tp1 = _take_tp1(manager)["updates"]

    settled = manager.evaluate_trade(
        _trade(
            "TP1_HIT", 4043.47,
            sl_moved_to_entry=True, partial_close=True,
            closed_fraction=tp1["closed_fraction"],
            realized_pnl_points=tp1["realized_pnl_points"],
            max_favorable_excursion=400.0,
        ),
        current_price=4023.21, now=TP2_MOMENT,
        candle_high=4035.00, candle_low=4023.00,
        recent_candles=[{"time": "2026-07-31T13:49:00Z", "open": 4034.0,
                         "high": 4035.00, "low": 4023.00, "close": 4023.21}],
    )

    assert "TP2_HIT" in settled["events"]
    full_size = calculate_pips(ENTRY, TP2, "SELL", SYMBOL)
    honest = round(
        0.5 * calculate_pips(ENTRY, TP1, "SELL", SYMBOL)
        + 0.5 * calculate_pips(ENTRY, TP2, "SELL", SYMBOL),
        1,
    )
    assert full_size == 456.1, "the number the card printed"
    assert honest == 342.1, "the number the account actually made"
    assert settled["updates"]["final_pnl"] == honest, (
        "settling the full size at TP2 overstates every two-target trade by "
        "the distance between TP1 and TP2 on half the position"
    )
    assert settled["updates"]["final_pnl"] != full_size


def test_a_stop_out_after_tp1_keeps_the_booked_half() -> None:
    """The half taken at TP1 is realized; a later stop cannot un-book it."""
    manager = _manager()

    settled = manager.evaluate_trade(
        _trade(
            "TP1_HIT", ENTRY,
            sl_moved_to_entry=True, partial_close=True,
            closed_fraction=0.5, realized_pnl_points=114.0,
        ),
        current_price=ENTRY, now=TP2_MOMENT,
        candle_high=4076.00, candle_low=4070.00,
        recent_candles=[{"time": "2026-07-31T13:49:00Z", "open": 4071.0,
                         "high": 4076.00, "low": 4070.00, "close": 4074.78}],
    )

    assert settled["new_status"] == "BE_HIT"
    assert settled["updates"]["final_pnl"] == 114.0, (
        "breakeven on the remaining half still leaves the TP1 half banked; "
        "reporting 0.0 would erase a real gain"
    )


def test_partial_close_can_be_switched_off() -> None:
    """With the config disabled, the full size runs to TP2 as before."""
    config = load_config()
    config.setdefault("trade_management", {})["partial_close_at_tp1"] = False
    manager = OpenTradesManager(config)

    result = manager.evaluate_trade(
        _trade("OPEN", ENTRY, sl_moved_to_entry=True),
        current_price=4051.00, now=TP1_MOMENT,
        candle_high=4058.00, candle_low=4050.00,
        recent_candles=[{"time": "2026-07-31T13:44:00Z", "open": 4057.0,
                         "high": 4058.00, "low": 4050.00, "close": 4051.0}],
    )

    assert "TP1_HIT" in result["events"]
    assert result["updates"].get("closed_fraction") is None


def test_fault_injection_the_unbooked_label_inflates_the_result() -> None:
    """Reintroduce the fault: a flag with no size behind it.

    This is the pre-fix behaviour verbatim -- `partial_close = True` and
    nothing else -- and it produces the number printed on the card.
    """
    full_size_at_tp2 = calculate_pips(ENTRY, TP2, "SELL", SYMBOL)
    honest = round(
        0.5 * calculate_pips(ENTRY, TP1, "SELL", SYMBOL)
        + 0.5 * calculate_pips(ENTRY, TP2, "SELL", SYMBOL),
        1,
    )

    assert full_size_at_tp2 == 456.1
    assert honest == 342.1
    assert round(full_size_at_tp2 - honest, 1) == 114.0, (
        "the overstatement equals half the TP1->TP2 distance and is always "
        "positive, so it inflates the scoreboard on every winning two-target "
        "trade and can never cancel out"
    )

    config = load_config()
    tm = config.get("trade_management") or {}
    assert tm.get("partial_close_at_tp1") is True
    assert float(tm.get("partial_close_percentage") or 0) == 50.0, (
        "if this config ever stops promising a 50% partial close, this whole "
        "file must be revisited rather than silently passing"
    )
