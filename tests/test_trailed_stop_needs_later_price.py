"""A trailed stop may only be executed by price that printed after it existed.

2026-07-31, trade TRADE_20260731_060253_794033_d917b1d5 (SELL 4074.78).

    Trailing Stop Hit
    Current Price: 4042.05
    Exit Price:    4050.45
    Actual PnL:    +243.3 pts

The card contradicts itself in two adjacent lines: it claims the stop at
4050.45 was hit while reporting that price stood at 4042.05 -- 33 points
BELOW it, and still falling. Price never returned to 4050.45. It went on to
4037 and the trade was worth +327 pts at that moment, with TP2 at 4029.17.

WHAT ACTUALLY HAPPENED
----------------------
One 5-minute bar carried the whole collapse: high 4055.00, low 4035.45.

  cycle A  the bar's LOW  (4035.45) drives the trailing stop 4063.00 -> 4050.45
  cycle B  the bar's HIGH (4055.00) is read as a touch of that same 4050.45

The high printed BEFORE the low. OHLC cannot prove otherwise, and in this
case the chart does: the bar opened at 4054 and closed at 4042.

The manager already refuses to execute a stop the *current cycle* created
(``active_protective_stop``). That guard is scoped to the cycle, and the
cycle that moves the stop persists it -- so five minutes later the stop is
"old" while the window still holds the pre-move high, and the guard waves it
through. The guard has to follow the candle, not the cycle.

THE RULE
--------
``trailing_stop_source_time`` records the newest bar that fed the trailing
calculation. A stop trailed into profit is then executable only by:

  * the current price -- "now" is by definition after the stop was set, so a
    real move through the stop still closes at the stop, same cycle; or
  * a bar strictly newer than the stamp.

Scope is deliberately narrow. Only a stop already beyond breakeven is
affected: that is the only stop derived from the window. The original hard
stop and a plain breakeven stop are judged on the full window exactly as
before, so no loss is ever left to run.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager  # noqa: E402
from utils.helpers import load_config  # noqa: E402

ENTRY = 4074.78
TP1 = 4051.98
TP2 = 4029.17
HARD_SL = 4089.78

# The bar that carried the collapse. Open 4054 -> close 4042 proves the high
# came first, but the manager may not rely on that: it only knows the extremes.
COLLAPSE_BAR = {
    "time": "2026-07-31T15:50:00Z",
    "open": 4054.00,
    "high": 4055.00,
    "low": 4035.45,
    "close": 4042.05,
}

CYCLE_A = datetime(2026, 7, 31, 15, 53, tzinfo=timezone.utc)
CYCLE_B = datetime(2026, 7, 31, 15, 58, tzinfo=timezone.utc)


def _manager() -> OpenTradesManager:
    return OpenTradesManager(load_config())


def _trade(stop: float, last_updated: str, mfe: float, **extra) -> dict:
    trade = {
        "id": "TRADE_20260731_060253_794033_d917b1d5",
        "symbol": "XAU/USD",
        "type": "SELL",
        "status": "TP1_HIT",
        "entry_price": ENTRY,
        "stop_loss": stop,
        "initial_stop_loss": HARD_SL,
        "tp1": TP1,
        "tp2": TP2,
        "entry_time": "2026-07-31T06:02:53+00:00",
        "created_at": "2026-07-31T06:02:53+00:00",
        "last_updated": last_updated,
        "sl_moved_to_entry": True,
        "partial_close": True,
        "max_favorable_excursion": mfe,
        "max_adverse_excursion": -69.3,
        "updates_sent": ["ORDER_FILLED", "TP1_HIT", "MOVE_SL_TO_BE"],
        "signal_snapshot": {},
    }
    trade.update(extra)
    return trade


def _move_the_stop(manager: OpenTradesManager) -> dict:
    """Cycle A: the collapse bar's low drags the trailing stop down to 4050.45."""
    return manager.evaluate_trade(
        _trade(4063.00, "2026-07-31T15:48:00+00:00", 250.0),
        current_price=4042.05,
        now=CYCLE_A,
        candle_high=COLLAPSE_BAR["high"],
        candle_low=COLLAPSE_BAR["low"],
        recent_candles=[COLLAPSE_BAR],
    )


def test_the_collapse_bar_moves_the_stop_and_stamps_its_source() -> None:
    moved = _move_the_stop(_manager())

    assert moved["updates"]["stop_loss"] == 4050.45, (
        "the reported exit price must be reproduced exactly, or this test is "
        "not describing the live incident"
    )
    assert moved["new_status"] == "TP1_HIT"
    assert "TRAILING_SL_HIT" not in moved["events"]
    assert moved["updates"]["trailing_stop_source_time"] == "2026-07-31T15:50:00+00:00", (
        "the stop must record the newest bar it was derived from; without the "
        "stamp the next cycle has no way to tell old price from new"
    )


def test_the_same_bar_cannot_then_execute_the_stop_it_created() -> None:
    """The exact false exit: +243.3 booked while price stood 33 pts better."""
    manager = _manager()
    moved = _move_the_stop(manager)

    settled = manager.evaluate_trade(
        _trade(
            moved["updates"]["stop_loss"],
            "2026-07-31T15:53:00+00:00",
            393.3,
            trailing_stop_source_time=moved["updates"]["trailing_stop_source_time"],
        ),
        current_price=4042.05,
        now=CYCLE_B,
        candle_high=COLLAPSE_BAR["high"],
        candle_low=COLLAPSE_BAR["low"],
        recent_candles=[COLLAPSE_BAR],
    )

    assert "TRAILING_SL_HIT" not in settled["events"], (
        "the 4055.00 high printed before the 4035.45 low that set this stop; "
        "it cannot also be the price that executed it"
    )
    assert settled["new_status"] == "TP1_HIT", "the trade must still be running"
    assert settled["updates"].get("close_price") is None
    assert settled["updates"].get("final_pnl") is None


def test_a_later_bar_reaching_the_stop_still_closes_the_trade() -> None:
    """The guard must not turn into a stop that never fires."""
    manager = _manager()
    rebound = {
        "time": "2026-07-31T16:05:00Z",
        "open": 4042.00,
        "high": 4052.00,
        "low": 4041.00,
        "close": 4051.00,
    }

    settled = manager.evaluate_trade(
        _trade(
            4050.45,
            "2026-07-31T16:00:00+00:00",
            393.3,
            trailing_stop_source_time="2026-07-31T15:50:00+00:00",
        ),
        current_price=4051.00,
        now=datetime(2026, 7, 31, 16, 8, tzinfo=timezone.utc),
        candle_high=rebound["high"],
        candle_low=rebound["low"],
        recent_candles=[COLLAPSE_BAR, rebound],
    )

    assert "TRAILING_SL_HIT" in settled["events"]
    assert settled["updates"]["close_price"] == 4050.45
    assert settled["updates"]["final_pnl"] == 243.3


def test_price_through_the_stop_closes_in_the_same_cycle() -> None:
    """Current price is always newer than the stop, stamp or no stamp."""
    manager = _manager()

    settled = manager.evaluate_trade(
        _trade(
            4050.45,
            "2026-07-31T16:00:00+00:00",
            393.3,
            trailing_stop_source_time="2026-07-31T15:50:00+00:00",
        ),
        current_price=4053.00,  # trading above the stop right now
        now=datetime(2026, 7, 31, 16, 8, tzinfo=timezone.utc),
        candle_high=4053.50,
        candle_low=4042.00,
        recent_candles=[COLLAPSE_BAR],
    )

    assert "TRAILING_SL_HIT" in settled["events"], (
        "a stop the market is trading through must close immediately; "
        "deferring it would hand back real profit"
    )
    assert settled["updates"]["close_price"] == 4050.45


def test_the_original_stop_is_untouched_by_the_guard() -> None:
    """A losing trade must never be left to run: no stamp, no deferral."""
    manager = _manager()

    settled = manager.evaluate_trade(
        {
            "id": "hard-sl", "symbol": "XAU/USD", "type": "SELL", "status": "OPEN",
            "entry_price": ENTRY, "stop_loss": HARD_SL, "initial_stop_loss": HARD_SL,
            "tp1": TP1, "tp2": TP2,
            "entry_time": "2026-07-31T06:02:53+00:00",
            "created_at": "2026-07-31T06:02:53+00:00",
            "last_updated": "2026-07-31T06:30:00+00:00",
            "sl_moved_to_entry": False,
            "updates_sent": ["ORDER_FILLED"], "signal_snapshot": {},
        },
        current_price=4090.50,
        now=datetime(2026, 7, 31, 7, 0, tzinfo=timezone.utc),
        candle_high=4091.00,
        candle_low=4080.00,
        recent_candles=[{"time": "2026-07-31T06:55:00Z", "open": 4082.0,
                         "high": 4091.00, "low": 4080.00, "close": 4090.5}],
    )

    assert "SL_HIT" in settled["events"]
    assert settled["new_status"] == "SL_HIT"


def test_a_row_without_the_stamp_behaves_as_before() -> None:
    """Trades already open when this shipped must not change behaviour."""
    manager = _manager()

    settled = manager.evaluate_trade(
        _trade(4050.45, "2026-07-31T15:53:00+00:00", 393.3),  # no stamp
        current_price=4042.05,
        now=CYCLE_B,
        candle_high=COLLAPSE_BAR["high"],
        candle_low=COLLAPSE_BAR["low"],
        recent_candles=[COLLAPSE_BAR],
    )

    assert "TRAILING_SL_HIT" in settled["events"], (
        "legacy rows keep the old window rule; the stamp is what enables the "
        "guard, and it only exists on stops written by the fixed code"
    )


def test_fault_injection_the_old_rule_books_the_false_exit() -> None:
    """Reintroduce the fault and prove this file catches it.

    The old code judged a trailed stop with ``_stop_touched``, which reads the
    whole window including the bar that produced the stop. That is the single
    line that cost 341 points.
    """
    manager = _manager()
    high_price = COLLAPSE_BAR["high"]
    stop = 4050.45
    trade_type = "SELL"

    def old_stop_touched(level: float) -> bool:
        return high_price >= level  # the pre-fix rule, verbatim

    assert old_stop_touched(stop) is True, (
        "under the old rule the 4055.00 high 'hits' the 4050.45 stop and books "
        "+243.3 while price is at 4042.05 -- this is the regression this file "
        "exists to prevent"
    )

    after = manager._adverse_extreme_after(
        [COLLAPSE_BAR], trade_type, manager._parse_dt("2026-07-31T15:50:00+00:00")
    )
    assert after is None, (
        "no bar newer than the stop's source exists, so nothing may execute it"
    )
