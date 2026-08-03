"""Breakeven protection must never be applied to a losing position.

2026-07-31, trade d917b1d5:

    SELL 4074.78 · stop 4089.78 · TP1 4051.98 · TP2 4029.17

    Thesis Risk Scale-Out at 4081.45   -- 67 pts OFFSIDE
      "Closed: 50% of the position"
      "Stop loss moved to entry / breakeven protection"
    Breakeven Hit at 4081.71           -- the very next tick
      Actual PnL: +0.0

    Price then fell to 4044: the trade was worth +308 pts.
    Booked: -33.4. Cost of the fault: 341 points.

Two faults, one sequence:

  1. The scale-out fired at all. The agent book that shipped with the signal
     read Classical 82 SELL and SMC 90 SELL against one opponent -- a DEFEND
     verdict. A later cycle saw one defender slip under the 70 confidence bar,
     which flipped DEFEND to SILENT, and SILENT scales out. The thesis had not
     changed; one agent's confidence wobbled.

  2. Scaling out moves the stop to breakeven. On a losing trade, entry sits
     between the market and the stop, so that move tightens the stop THROUGH
     the live price -- it does not protect the position, it executes it.

Both are fixed: SILENT no longer scales an offside position, and the
scale-out branch refuses to set a breakeven stop that is on the wrong side of
the market whatever asked for it.

Fault injection: drop the `pnl_points <= 0` guard, or the
`_beyond_breakeven_or_at` check in the scale-out branch, and these fail.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOL = "XAU/USD"
ENTRY = 4074.78
STOP = 4089.78


def _config(silent_action: str = "SCALE_OUT") -> dict:
    """Config with the silent verdict pinned to SCALE_OUT.

    The shipped default is HOLD (an undecided book changes nothing), but this
    file exists to prove that WHEN a scale-out does happen it never moves the
    stop to the wrong side of the market. Pinning the setting keeps that
    guarantee under test whatever the default becomes.
    """
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        config = json.load(fh)
    config["trade_management"]["thesis_exit"]["agent_vote"]["silent_action"] = silent_action
    return config


def _book(**agents) -> dict:
    return {n: {"direction": d, "confidence": c} for n, (d, c) in agents.items()}


# One defender under the 70 bar -> SILENT, the verdict that scaled the trade.
SILENT_BOOK = _book(
    technical=("WAIT", 40.6), classical=("SELL", 82.0), smc=("SELL", 65.0),
    price_action=("BUY", 84.0), multitimeframe=("BUY", 42.0),
)
# The book actually published with the signal -> DEFEND.
DEFEND_BOOK = _book(
    technical=("WAIT", 40.6), classical=("SELL", 82.0), smc=("SELL", 90.0),
    price_action=("BUY", 84.0), multitimeframe=("BUY", 42.0),
)


def _opened() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=30)


def _sell(**over) -> dict:
    opened = _opened()
    trade = {
        "id": "TRADE_20260731_060253_794033_d917b1d5", "type": "SELL",
        "status": "OPEN", "symbol": SYMBOL, "entry_price": ENTRY,
        "stop_loss": STOP, "initial_stop_loss": STOP,
        "tp1": 4051.98, "tp2": 4029.17,
        "created_at": opened.isoformat(), "entry_time": opened.isoformat(),
        "updates_sent": [],
    }
    trade.update(over)
    return trade


def _bullish_reclaim(opened: datetime) -> list[dict]:
    """Candles that trigger the continuation check against a SELL."""
    return [
        {"time": (opened + timedelta(minutes=20)).isoformat(), "open": 4078.0,
         "high": 4080.0, "low": 4077.0, "close": 4079.0},
        {"time": (opened + timedelta(minutes=25)).isoformat(), "open": 4079.0,
         "high": 4082.0, "low": 4078.5, "close": 4081.45},
    ]


def _evaluate(book, price=4081.45, trade=None):
    manager = OpenTradesManager(_config())
    trade = trade or _sell()
    opened = datetime.fromisoformat(trade["entry_time"])
    return manager.evaluate_trade(
        trade, price, now=datetime.now(timezone.utc),
        candle_high=4082.0, candle_low=4078.5,
        recent_candles=_bullish_reclaim(opened), agent_details=book,
    )


# ── the live incident ──────────────────────────────────────────────────────

def test_a_losing_position_is_not_scaled_out() -> None:
    result = _evaluate(SILENT_BOOK)
    assert "THESIS_SCALE_OUT" not in result["events"], (
        "a 67-pt losing SELL was scaled, which moved its stop to the wrong "
        "side of the market"
    )
    assert result["pnl_points"] < 0, "this fixture must be offside to be meaningful"


def test_the_original_stop_survives() -> None:
    result = _evaluate(SILENT_BOOK)
    assert result["updates"].get("stop_loss", STOP) == STOP
    assert "MOVE_SL_TO_BE" not in result["events"]


def test_the_trade_stays_open() -> None:
    result = _evaluate(SILENT_BOOK)
    assert result["new_status"] == "OPEN"
    assert "BE_HIT" not in result["events"]


def test_the_reason_explains_the_refusal() -> None:
    manager = OpenTradesManager(_config())
    opened = _opened()
    verdict = manager._thesis_exit_review(
        _sell(), trade_type="SELL", symbol=SYMBOL, current_price=4081.45,
        recent_candles=_bullish_reclaim(opened), hours_open=0.5,
        pnl_points=-66.7, max_favorable_excursion=0.0, tp1=4051.98,
        entry=ENTRY, partial_close=False, agent_details=SILENT_BOOK,
    )
    assert verdict["scale_out"] is False
    assert verdict["kind"] == "OPPOSITE_CONTINUATION_HELD_WHILE_OFFSIDE"
    assert "offside" in verdict["reason"]


# ── what must still happen ─────────────────────────────────────────────────

def test_a_winning_position_is_still_scaled_on_a_silent_book() -> None:
    """The feature the operator asked for is intact for its real case."""
    # Keep the original stop: setting it to entry would make 4060 an SL_HIT
    # for a SELL before the scale-out branch is ever reached.
    winner = _sell()
    result = _evaluate(SILENT_BOOK, price=4060.0, trade=winner)
    assert result["pnl_points"] > 0
    assert "THESIS_SCALE_OUT" in result["events"]
    assert result["updates"]["closed_fraction"] == 0.5


def test_a_defending_book_never_scales_at_all() -> None:
    result = _evaluate(DEFEND_BOOK)
    assert "THESIS_SCALE_OUT" not in result["events"]
    assert result["new_status"] == "OPEN"


def test_the_published_book_was_a_defend_verdict() -> None:
    """The signal shipped with two qualified defenders against one opponent."""
    manager = OpenTradesManager(_config())
    vote = manager._agent_exit_vote(DEFEND_BOOK, "SELL")
    assert vote["verdict"] == "DEFEND"
    assert set(vote["defenders"]) == {"classical", "smc"}


def test_a_confirmed_reversal_still_exits_a_losing_trade() -> None:
    """Holding offside is not the same as never exiting."""
    confirmed = _book(
        technical=("BUY", 85.0), classical=("BUY", 80.0), smc=("BUY", 88.0),
        price_action=("BUY", 84.0), multitimeframe=("BUY", 82.0),
    )
    result = _evaluate(confirmed)
    assert result["new_status"] == "THESIS_EXIT"


# ── the structural guard ───────────────────────────────────────────────────

def test_breakeven_is_never_set_on_the_wrong_side_of_price() -> None:
    """Second line of defence, independent of which branch asked for it."""
    manager = OpenTradesManager(_config())
    # SELL: price ABOVE entry means a breakeven stop would be below the market
    assert manager._beyond_breakeven_or_at("SELL", 4081.45, ENTRY) is False
    assert manager._beyond_breakeven_or_at("SELL", 4070.00, ENTRY) is True
    # BUY mirrors it
    assert manager._beyond_breakeven_or_at("BUY", 4070.00, 4074.78) is False
    assert manager._beyond_breakeven_or_at("BUY", 4081.45, 4074.78) is True


def test_a_buy_offside_is_protected_the_same_way() -> None:
    opened = _opened()
    buy = {
        "id": "B", "type": "BUY", "status": "OPEN", "symbol": SYMBOL,
        "entry_price": 4100.0, "stop_loss": 4060.0, "initial_stop_loss": 4060.0,
        "tp1": 4140.0, "tp2": 4180.0,
        "created_at": opened.isoformat(), "entry_time": opened.isoformat(),
        "updates_sent": [],
    }
    bearish = [
        {"time": (opened + timedelta(minutes=20)).isoformat(), "open": 4095.0,
         "high": 4096.0, "low": 4092.0, "close": 4093.0},
        {"time": (opened + timedelta(minutes=25)).isoformat(), "open": 4093.0,
         "high": 4093.5, "low": 4088.0, "close": 4089.0},
    ]
    silent_for_buy = _book(
        technical=("WAIT", 40.0), classical=("BUY", 82.0), smc=("BUY", 65.0),
        price_action=("SELL", 84.0), multitimeframe=("SELL", 42.0),
    )
    manager = OpenTradesManager(_config())
    result = manager.evaluate_trade(
        buy, 4089.0, now=datetime.now(timezone.utc),
        candle_high=4093.5, candle_low=4088.0,
        recent_candles=bearish, agent_details=silent_for_buy,
    )
    assert result["pnl_points"] < 0
    assert "THESIS_SCALE_OUT" not in result["events"]
    assert result["updates"].get("stop_loss", 4060.0) == 4060.0


def test_the_scale_out_branch_refuses_a_wrong_side_breakeven_directly() -> None:
    """Cover the second guard on its own.

    `_thesis_exit_review` already refuses to scale an offside position, so the
    branch guard is unreachable through the normal path. It exists because any
    future caller that sets scale_out must not be able to reintroduce the
    fault, and a guard nothing exercises is a guard nobody can trust.
    """
    manager = OpenTradesManager(_config())
    opened = _opened()
    trade = _sell()

    # Force a scale-out verdict on a losing position, bypassing the review.
    manager._thesis_exit_review = lambda *a, **k: {  # type: ignore[assignment]
        "exit_now": False, "scale_out": True, "scale_fraction": 0.5,
        "kind": "FORCED_FOR_TEST", "reason": "forced",
    }
    result = manager.evaluate_trade(
        trade, 4081.45, now=datetime.now(timezone.utc),
        candle_high=4082.0, candle_low=4078.5,
        recent_candles=_bullish_reclaim(opened),
    )
    assert "THESIS_SCALE_OUT" in result["events"], "the forced scale-out should run"
    assert "MOVE_SL_TO_BE" not in result["events"], (
        "breakeven was applied to a position 67 pts offside"
    )
    assert result["updates"].get("stop_loss", STOP) == STOP
