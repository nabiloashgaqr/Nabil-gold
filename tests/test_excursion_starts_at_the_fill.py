"""A position may only be judged by price action it was exposed to.

2026-07-30, trade 8d6ad198. The card contradicted itself in two lines:

    Status       : TP1_HIT -> BE_HIT
    Current Price: 4075.69          <- 110 pts ABOVE entry
    Exit Price   : 4064.69
    Actual PnL   : +0.0 pts

Price never came back. `_window_extremes_since` took its baseline from
`last_updated` and fell back to `created_at` -- the moment the PLAN was
written, hours before the pending order filled. Any 5m bar from that window
counted as this trade's own excursion, so a dip printed BEFORE the fill was
read as a live breakeven touch and closed a +110 pt winner for zero.

The fill time is the only honest floor for a trade's own excursion.

Fault injection: restore `created_at` ahead of `entry_time` in the baseline
and `test_a_bar_from_before_the_fill_cannot_close_the_trade` fails.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = 4064.69
SYMBOL = "XAU/USD"


def _config() -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _protected_runner(**over) -> dict:
    """The live trade: filled 15:20, TP1 taken, stop carried to breakeven."""
    trade = {
        "id": "TRADE_20260730_104606_040058_8d6ad198", "type": "BUY",
        "status": "TP1_HIT", "symbol": SYMBOL, "entry_price": ENTRY,
        "stop_loss": ENTRY, "initial_stop_loss": 4049.69,
        "tp1": 4081.63, "tp2": 4093.31,
        "partial_close": True, "sl_moved_to_entry": True,
        "created_at": "2026-07-30T10:46:06+00:00",   # the PLAN, hours earlier
        "entry_time": "2026-07-30T15:20:00+00:00",   # the actual fill
        "updates_sent": ["ORDER_FILLED", "TP1_HIT", "MOVE_SL_TO_BE"],
    }
    trade.update(over)
    return trade


def _run(trade, price, candles, high, low, minute=45):
    return OpenTradesManager(_config()).evaluate_trade(
        trade, price, now=datetime(2026, 7, 30, 15, minute, tzinfo=timezone.utc),
        candle_high=high, candle_low=low, recent_candles=candles,
    )


# ── the bug ────────────────────────────────────────────────────────────────

def test_a_bar_from_before_the_fill_cannot_close_the_trade() -> None:
    candles = [
        # 15:05 -- BEFORE the 15:20 fill. Price was down here, but this trade
        # did not exist yet.
        {"time": "2026-07-30T15:05:00+00:00", "open": 4062.0, "high": 4066.0,
         "low": 4059.0, "close": 4065.0},
        {"time": "2026-07-30T15:40:00+00:00", "open": 4074.0, "high": 4077.0,
         "low": 4073.5, "close": 4075.69},
    ]
    result = _run(_protected_runner(), 4075.69, candles, 4077.0, 4073.5)
    assert result["new_status"] != "BE_HIT", (
        "a bar printed before the fill closed a trade that was 110 pts up"
    )
    assert "BE_HIT" not in result["events"]
    assert result["pnl_points"] > 100


def test_the_reported_price_and_the_verdict_agree() -> None:
    """The card must never say 'exited at entry' while price is far above it."""
    candles = [
        {"time": "2026-07-30T15:05:00+00:00", "open": 4062.0, "high": 4066.0,
         "low": 4059.0, "close": 4065.0},
        {"time": "2026-07-30T15:40:00+00:00", "open": 4074.0, "high": 4077.0,
         "low": 4073.5, "close": 4075.69},
    ]
    result = _run(_protected_runner(), 4075.69, candles, 4077.0, 4073.5)
    close_price = result["updates"].get("close_price")
    assert close_price is None, f"closed at {close_price} while price was 4075.69"


# ── what must still work ───────────────────────────────────────────────────

def test_a_real_pullback_after_the_fill_still_closes_at_breakeven() -> None:
    """The protection itself is untouched."""
    candles = [
        {"time": "2026-07-30T15:40:00+00:00", "open": 4074.0, "high": 4077.0,
         "low": 4073.5, "close": 4075.0},
        {"time": "2026-07-30T15:45:00+00:00", "open": 4075.0, "high": 4076.0,
         "low": 4060.0, "close": 4063.0},   # genuine dip through entry
    ]
    result = _run(_protected_runner(), 4063.0, candles, 4076.0, 4060.0, minute=50)
    assert result["new_status"] == "BE_HIT"
    assert "BE_HIT" in result["events"]


def test_a_real_stop_out_before_breakeven_still_fires() -> None:
    exposed = _protected_runner(
        status="OPEN", stop_loss=4049.69, sl_moved_to_entry=False,
        partial_close=False, updates_sent=["ORDER_FILLED"],
    )
    candles = [
        {"time": "2026-07-30T15:40:00+00:00", "open": 4060.0, "high": 4062.0,
         "low": 4055.0, "close": 4056.0},
        {"time": "2026-07-30T15:45:00+00:00", "open": 4056.0, "high": 4057.0,
         "low": 4045.0, "close": 4048.0},
    ]
    result = _run(exposed, 4048.0, candles, 4057.0, 4045.0, minute=50)
    assert result["new_status"] == "SL_HIT"


def test_targets_still_register_from_bars_after_the_fill() -> None:
    candles = [
        {"time": "2026-07-30T15:40:00+00:00", "open": 4074.0, "high": 4077.0,
         "low": 4073.5, "close": 4076.0},
        {"time": "2026-07-30T15:45:00+00:00", "open": 4076.0, "high": 4095.0,
         "low": 4075.0, "close": 4094.0},   # through TP2
    ]
    result = _run(_protected_runner(), 4094.0, candles, 4095.0, 4075.0, minute=50)
    assert result["new_status"] == "TP2_HIT"


# ── the baseline rule itself ───────────────────────────────────────────────

def test_the_baseline_never_reaches_back_before_the_fill() -> None:
    manager = OpenTradesManager(_config())
    trade = _protected_runner(last_updated="2026-07-30T11:00:00+00:00")  # stale
    candles = [
        {"time": "2026-07-30T15:05:00+00:00", "high": 4066.0, "low": 4059.0},
        {"time": "2026-07-30T15:40:00+00:00", "high": 4077.0, "low": 4073.5},
    ]
    high, low = manager._window_extremes_since(trade, candles)
    assert low == 4073.5, f"pre-fill low {low} leaked into the excursion window"
    assert high == 4077.0


def test_last_updated_after_the_fill_still_narrows_the_window() -> None:
    """Fill time is a floor, not a replacement: newer progress still counts."""
    manager = OpenTradesManager(_config())
    trade = _protected_runner(last_updated="2026-07-30T15:42:00+00:00")
    candles = [
        {"time": "2026-07-30T15:40:00+00:00", "high": 4077.0, "low": 4073.5},
        {"time": "2026-07-30T15:45:00+00:00", "high": 4090.0, "low": 4080.0},
    ]
    high, low = manager._window_extremes_since(trade, candles)
    assert (high, low) == (4090.0, 4080.0)


def test_an_undated_bar_is_not_trusted_when_a_baseline_exists() -> None:
    manager = OpenTradesManager(_config())
    candles = [
        {"high": 4066.0, "low": 4059.0},   # no timestamp
        {"time": "2026-07-30T15:40:00+00:00", "high": 4077.0, "low": 4073.5},
    ]
    high, low = manager._window_extremes_since(_protected_runner(), candles)
    assert low == 4073.5, "an undated bar widened the excursion on trust"


def test_a_trade_with_no_fill_time_still_works() -> None:
    """Older rows may lack entry_time; fall back rather than crash."""
    manager = OpenTradesManager(_config())
    trade = _protected_runner()
    trade.pop("entry_time")
    trade.pop("last_updated", None)
    candles = [{"time": "2026-07-30T15:40:00+00:00", "high": 4077.0, "low": 4073.5}]
    high, low = manager._window_extremes_since(trade, candles)
    assert (high, low) == (4077.0, 4073.5)
