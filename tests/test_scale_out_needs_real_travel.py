"""A scale-out must not close a trade that has barely left the gate.

2026-08-03, TRADE_20260803_000202_045834_bdde9a5f (BUY, filled 4067.02):

    Thesis Risk Scale-Out
    Current PnL: +6.1 pts
    TP1 Progress: 1%
    Closed: 50% of the position at 4067.63
    Realized so far: +3.0 pts
    Stop loss moved to entry / breakeven protection

    Breakeven Hit          (ten minutes later)
    Current Price: 4065.33
    Actual PnL: +0.0 pts

Six points of open profit. One percent of the way to the first target. The
scale-out carried the stop to breakeven, breakeven sat inside the noise, and
the position was closed flat on the next pullback.

TWO SEPARATE FAULTS
-------------------
1. The SILENT-verdict scale-out checked only ``pnl_points > 0``. Any profit
   at all, however small, was enough to move the stop to entry -- and moving
   the stop to entry on a six-point trade does not protect a gain, it
   guarantees a flat exit. ``thesis_exit.min_mfe_points`` (35) already states
   how far a trade must travel before its excursion means anything, and it
   already guards the countertrend branch; it was simply never applied here.

2. The Breakeven card reported ``+0.0`` although ``+3.0`` had genuinely been
   booked. The composite settlement needs both ``closed_fraction`` and
   ``realized_pnl_points``; when either is missing -- an older row, or a
   Supabase write that dropped an unknown column -- it fell back to the
   remaining leg alone and the money already taken off the table disappeared
   from the record.

Neither fix changes a risk setting. The first refuses an action; the second
repairs a number that was already true.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager  # noqa: E402
from utils.helpers import load_config  # noqa: E402

def _scaling_config() -> dict:
    """Config with the silent verdict pinned to SCALE_OUT.

    The shipped default is HOLD -- an undecided agent book neither closes nor
    reduces a position. This file tests the travel floor that applies WHEN a
    scale-out is enabled, so it sets the mode explicitly. Otherwise every
    assertion here would pass vacuously the moment the default changed, which
    is the quietest way for a guard to stop guarding anything.
    """
    config = load_config()
    config["trade_management"]["thesis_exit"]["agent_vote"]["silent_action"] = "SCALE_OUT"
    return config


CONFIG = _scaling_config()
ENTRY = 4067.02          # the zone-edge fill
STOP = 4027.02           # 400 pts
TP1, TP2 = 4117.02, 4157.02
SYMBOL = "XAU/USD"

# Two 5m bars forming a bearish continuation against a BUY.
BEARISH_CONTINUATION = [
    {"time": "2026-08-03T00:20:00Z", "open": 4070.0, "high": 4072.0, "low": 4069.0, "close": 4070.5},
    {"time": "2026-08-03T00:25:00Z", "open": 4069.0, "high": 4069.5, "low": 4066.0, "close": 4067.63},
]
# The agent book that produced a SILENT verdict on the day.
SILENT_BOOK = {
    "technical": {"direction": "SELL", "confidence": 40},
    "classical": {"direction": "WAIT", "confidence": 30},
}


def _manager(config=None) -> OpenTradesManager:
    return OpenTradesManager(config or CONFIG)


def _live_trade(mfe: float, **extra) -> dict:
    trade = {
        "id": "TRADE_20260803_000202_045834_bdde9a5f",
        "symbol": SYMBOL, "type": "BUY", "status": "OPEN",
        "entry_price": ENTRY, "stop_loss": STOP, "initial_stop_loss": STOP,
        "tp1": TP1, "tp2": TP2,
        "entry_time": "2026-08-03T00:05:00+00:00",
        "created_at": "2026-08-03T00:02:02+00:00",
        "last_updated": "2026-08-03T00:20:00+00:00",
        "sl_moved_to_entry": False, "partial_close": False,
        "max_favorable_excursion": mfe, "max_adverse_excursion": -2.0,
        "updates_sent": ["ORDER_FILLED"], "signal_snapshot": {},
    }
    trade.update(extra)
    return trade


def _evaluate(trade, price: float, manager=None, bars=None):
    return (manager or _manager()).evaluate_trade(
        trade, current_price=price,
        now=datetime(2026, 8, 3, 0, 25, tzinfo=timezone.utc),
        candle_high=4069.5, candle_low=4066.0,
        recent_candles=bars if bars is not None else BEARISH_CONTINUATION,
        agent_details=SILENT_BOOK,
    )


# ── fault 1: the six-point scale-out ────────────────────────────────────────

def test_the_configured_floor_exists_and_is_meaningful() -> None:
    floor = float(CONFIG["trade_management"]["thesis_exit"]["min_mfe_points"])
    assert floor == 35.0
    assert floor > 6.1, "the card's whole excursion was smaller than the floor"


def test_a_six_point_trade_is_not_scaled_out() -> None:
    """The exact incident."""
    result = _evaluate(_live_trade(mfe=8.0), 4067.63)

    assert "THESIS_SCALE_OUT" not in result["events"], (
        "moving the stop to entry on a 6-pt trade does not protect a gain, "
        "it guarantees a flat exit on the next pullback"
    )
    assert "MOVE_SL_TO_BE" not in result["events"]
    assert result["updates"]["sl_moved_to_entry"] is False
    assert result["updates"].get("closed_fraction") is None
    assert result["new_status"] == "OPEN", "the trade must still be running"


def test_a_trade_that_genuinely_travelled_is_still_scaled() -> None:
    """The guard must not disable the feature it narrows."""
    bars = [
        {"time": "2026-08-03T00:20:00Z", "open": 4074.0, "high": 4076.0, "low": 4073.0, "close": 4074.5},
        {"time": "2026-08-03T00:25:00Z", "open": 4073.0, "high": 4073.5, "low": 4070.0, "close": 4071.0},
    ]
    result = _evaluate(_live_trade(mfe=60.0), 4071.0, bars=bars)

    assert "THESIS_SCALE_OUT" in result["events"]
    assert result["updates"]["closed_fraction"] == 0.5


def test_the_floor_is_measured_on_the_best_excursion_not_the_last_price() -> None:
    """A trade that ran and gave it back is still protected."""
    bars = [
        {"time": "2026-08-03T00:20:00Z", "open": 4074.0, "high": 4076.0, "low": 4073.0, "close": 4074.5},
        {"time": "2026-08-03T00:25:00Z", "open": 4073.0, "high": 4073.5, "low": 4070.0, "close": 4071.0},
    ]
    # Only +40 pts open right now, but it reached +90 earlier.
    result = _evaluate(_live_trade(mfe=90.0), 4071.0, bars=bars)
    assert "THESIS_SCALE_OUT" in result["events"]


def test_an_offside_trade_is_still_refused_for_its_own_reason() -> None:
    """The pre-existing offside guard is untouched."""
    result = _evaluate(_live_trade(mfe=0.0), 4060.00)
    assert "THESIS_SCALE_OUT" not in result["events"]
    assert result["updates"]["sl_moved_to_entry"] is False


def test_the_floor_can_be_tuned() -> None:
    config = _scaling_config()
    config["trade_management"]["thesis_exit"]["min_mfe_points"] = 5.0
    result = _evaluate(_live_trade(mfe=8.0), 4067.63, manager=_manager(config))
    assert "THESIS_SCALE_OUT" in result["events"], (
        "an 8-pt excursion clears a 5-pt floor; the setting must be live"
    )


# ── fault 2: the erased half ────────────────────────────────────────────────

def _settled(**overrides) -> dict:
    trade = {
        "id": "settle", "symbol": SYMBOL, "type": "BUY", "status": "PARTIAL",
        "entry_price": ENTRY, "stop_loss": ENTRY, "initial_stop_loss": STOP,
        "tp1": TP1, "tp2": TP2,
        "entry_time": "2026-08-03T00:05:00+00:00",
        "created_at": "2026-08-03T00:02:02+00:00",
        "last_updated": "2026-08-03T00:30:00+00:00",
        "sl_moved_to_entry": True, "partial_close": True,
        "scale_out_price": 4067.63,
        "max_favorable_excursion": 8.0,
        "updates_sent": ["ORDER_FILLED", "THESIS_SCALE_OUT"],
        "signal_snapshot": {},
    }
    trade.update(overrides)
    return _manager().evaluate_trade(
        trade, current_price=4065.33,
        now=datetime(2026, 8, 3, 0, 35, tzinfo=timezone.utc),
        candle_high=4067.5, candle_low=4065.0,
        recent_candles=[{"time": "2026-08-03T00:34:00Z", "open": 4067.0,
                         "high": 4067.5, "low": 4065.0, "close": 4065.33}],
    )["updates"]


def test_a_complete_partial_settles_correctly() -> None:
    updates = _settled(closed_fraction=0.5, realized_pnl_points=3.0)
    assert updates["final_pnl"] == 3.0


def test_a_booked_half_survives_a_missing_fraction() -> None:
    """The card's state: partial_close set, the numbers gone."""
    updates = _settled()
    assert updates["final_pnl"] == 3.0, (
        "the Breakeven card reported +0.0 while +3.0 had genuinely been "
        "booked; a dropped column must not erase realized money"
    )


def test_a_booked_half_survives_a_missing_realized_value() -> None:
    updates = _settled(closed_fraction=0.5)
    assert updates["final_pnl"] == 3.0


def test_a_trade_that_never_scaled_is_untouched() -> None:
    """No partial flag means nothing to reconstruct."""
    updates = _settled(partial_close=False, scale_out_price=None)
    assert updates["final_pnl"] == 0.0


def test_reconstruction_needs_a_scale_price() -> None:
    """Without the price the half left at, no number is invented."""
    updates = _settled(scale_out_price=None)
    assert updates["final_pnl"] == 0.0


def test_a_tp1_partial_is_never_reconstructed_as_a_scale_out() -> None:
    """The regression two existing tests caught, pinned here permanently.

    ``partial_close`` is set by TP1 as well as by a thesis scale-out. An
    earlier version of the reconstruction keyed on that flag alone and halved
    every legacy TP1 trade -- 700 pts became 350, and 243.3 became 121.7 in
    test_open_trades_manager and test_trailed_stop_needs_later_price. Those
    tests were right and the fix was too broad.

    ``scale_out_price`` is written only by a thesis scale-out, so it is the
    single trigger. A TP1 partial with no such price must settle untouched.
    """
    manager = _manager()
    tp1_partial = {
        "id": "tp1-legacy", "symbol": SYMBOL, "type": "SELL", "status": "TP1_HIT",
        "entry_price": 4100.0, "stop_loss": 4100.0, "initial_stop_loss": 4140.0,
        "tp1": 4070.0, "tp2": 4030.0,
        "entry_time": "2026-08-03T00:05:00+00:00",
        "created_at": "2026-08-03T00:02:02+00:00",
        "last_updated": "2026-08-03T00:30:00+00:00",
        "sl_moved_to_entry": True,
        "partial_close": True,            # set by TP1, not by a scale-out
        "max_favorable_excursion": 700.0,
        "updates_sent": ["ORDER_FILLED", "TP1_HIT"], "signal_snapshot": {},
    }
    assert "scale_out_price" not in tp1_partial

    settled = manager.evaluate_trade(
        tp1_partial, current_price=4030.0,
        now=datetime(2026, 8, 3, 0, 35, tzinfo=timezone.utc),
        candle_high=4035.0, candle_low=4029.0,
        recent_candles=[{"time": "2026-08-03T00:34:00Z", "open": 4034.0,
                         "high": 4035.0, "low": 4029.0, "close": 4030.0}],
    )
    assert settled["updates"]["final_pnl"] == 700.0, (
        "a TP1 partial with no scale-out price must not be halved"
    )


# ── risk untouched ──────────────────────────────────────────────────────────

def test_no_risk_setting_was_changed() -> None:
    risk = CONFIG["risk_settings"]
    assert float(risk["min_sl_distance_points"]) == 400.0
    assert float(risk["min_rr_ratio"]) == 1.5
    assert float(risk["max_rr_ratio"]) == 4.0
    thesis = CONFIG["trade_management"]["thesis_exit"]
    assert float(thesis["min_mfe_points"]) == 35.0
    assert float(thesis["reclaim_points"]) == 12.0
    assert float(thesis["agent_vote"]["silent_scale_fraction"]) == 0.5


def test_fault_injection_a_bare_pnl_check_admits_the_six_point_scale() -> None:
    """Reproduce the pre-fix condition and show it fires on the card."""
    pnl_points = 6.1
    max_favorable_excursion = 8.0
    floor = float(CONFIG["trade_management"]["thesis_exit"]["min_mfe_points"])

    old_allows = pnl_points > 0
    new_allows = pnl_points > 0 and max_favorable_excursion >= floor

    assert old_allows is True, (
        "the old rule asked only whether the trade was green, so six points "
        "was enough to move the stop to entry"
    )
    assert new_allows is False
    assert _evaluate(_live_trade(mfe=8.0), 4067.63)["new_status"] == "OPEN"
