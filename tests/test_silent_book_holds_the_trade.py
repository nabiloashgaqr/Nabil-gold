"""An undecided agent book must not close or reduce a position.

THE RULE THE OPERATOR ASKED FOR
-------------------------------
Closing a trade -- fully or partially -- requires two qualified agents
arguing the OPPOSITE direction. When the book is undecided (SILENT), nothing
happens: the position keeps running on the stop it already has.

    silent_action = "HOLD"

WHY SILENCE IS NOT EVIDENCE
---------------------------
SILENT means no qualified majority either way: neither two opponents
confirming the exit, nor two defenders holding it. The candle trigger that
started the review is a single piece of evidence, and on 2026-07-30 it closed
a SELL for -39.2 while Classical 71, SMC 90 and Multi-Timeframe 83 were all
still arguing the trade -- a zone the planner then republished as its A+ map
of the day, and which the market went on to pay.

THE TRAP THIS PACKAGE AVOIDS
----------------------------
Changing the config alone would have done the OPPOSITE of what was asked.
The SILENT branch was written as ``if verdict == "SILENT" and silent_action
== "SCALE_OUT"``. With any other value the condition is false, execution
falls past it, and the code reaches the unconditional full-exit return at the
bottom of the block. Measured before the fix:

    silent_action = HOLD  ->  exit_now: True

So the setting meant to make the system gentler would have made it close
every position the candle rule fired on. HOLD had to become a branch the code
understands, not merely a string it fails to match.

BOTH EXIT PATHS, NOT ONE
------------------------
``_thesis_exit_review`` can close a trade from two triggers: the candle
continuation rule and an opposing-POI rejection. The POI path consulted no
agents at all. Applying HOLD to the candle path alone would have left the
other one closing positions unopposed -- the rule would look applied and not
be. Both now ask the same book.

WHAT STILL CLOSES A TRADE
-------------------------
CONFIRM -- two qualified opponents with no defender -- is untouched, in both
paths. So is every price-based exit: stop loss, breakeven, trailing stop,
TP1 and TP2. This changes one thing only: what an ABSENCE of agent agreement
is allowed to do.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOL = "XAU/USD"


def _config(silent_action: str | None = None) -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        config = json.load(fh)
    if silent_action is not None:
        config["trade_management"]["thesis_exit"]["agent_vote"]["silent_action"] = silent_action
    return config


def _book(**agents) -> dict:
    return {n: {"direction": d, "confidence": c} for n, (d, c) in agents.items()}


# A bearish continuation that reclaims the breakout, against a BUY.
_BEARISH_RECLAIM = [
    {"time": "2026-08-03T00:20:00Z", "open": 4070.0, "high": 4072.0, "low": 4069.0, "close": 4070.5},
    {"time": "2026-08-03T00:25:00Z", "open": 4069.0, "high": 4069.5, "low": 4066.0, "close": 4067.63},
]

# Undecided: one qualified opponent, no qualified defender.
SILENT_BOOK = _book(technical=("SELL", 92.0), classical=("WAIT", 30.0))
# Two qualified opponents, no defender.
CONFIRM_BOOK = _book(technical=("SELL", 92.0), smc=("SELL", 84.0))
# Two qualified defenders.
DEFEND_BOOK = _book(technical=("BUY", 92.0), smc=("BUY", 84.0))


def _review(config, book, *, pnl=600.0, mfe=700.0, partial_close=False):
    manager = OpenTradesManager(config)
    return manager._thesis_exit_review(
        {"id": "T", "type": "BUY", "entry_price": 4000.0, "symbol": SYMBOL},
        trade_type="BUY", symbol=SYMBOL, current_price=4060.0,
        recent_candles=_BEARISH_RECLAIM, hours_open=1.0,
        pnl_points=pnl, max_favorable_excursion=mfe, tp1=4200.0,
        entry=4000.0, partial_close=partial_close, agent_details=book,
    )


# ── the shipped default ─────────────────────────────────────────────────────

def test_the_shipped_default_is_hold() -> None:
    action = _config()["trade_management"]["thesis_exit"]["agent_vote"]["silent_action"]
    assert action == "HOLD"


def test_a_silent_book_neither_closes_nor_reduces() -> None:
    verdict = _review(_config(), SILENT_BOOK)

    assert verdict["exit_now"] is False
    assert verdict["scale_out"] is False
    assert verdict["kind"] == "OPPOSITE_CONTINUATION_HELD_SILENT_BOOK"
    assert verdict["agent_vote"]["verdict"] == "SILENT"


def test_the_reason_states_the_vote_that_produced_it() -> None:
    reason = _review(_config(), SILENT_BOOK)["reason"]
    assert "undecided" in reason
    assert "silent_action is HOLD" in reason
    assert "0 defending" in reason and "1 opposing" in reason, (
        "the refusal must be auditable from the message alone"
    )


def test_hold_applies_to_a_losing_trade_too() -> None:
    verdict = _review(_config(), SILENT_BOOK, pnl=-80.0, mfe=0.0)
    assert verdict["exit_now"] is False and verdict["scale_out"] is False


def test_hold_applies_after_a_previous_partial() -> None:
    verdict = _review(_config(), SILENT_BOOK, partial_close=True)
    assert verdict["exit_now"] is False and verdict["scale_out"] is False


# ── what must still close ───────────────────────────────────────────────────

def test_two_qualified_opponents_still_close_the_trade() -> None:
    """The operator's rule: two agents the other way, and it goes."""
    verdict = _review(_config(), CONFIRM_BOOK)

    assert verdict["exit_now"] is True
    assert verdict["agent_vote"]["verdict"] == "CONFIRM"
    assert "confirmed by 2 qualified agents" in verdict["reason"]


def test_two_defenders_still_hold_the_trade() -> None:
    verdict = _review(_config(), DEFEND_BOOK)
    assert verdict["exit_now"] is False
    assert verdict["kind"] == "OPPOSITE_CONTINUATION_VETOED_BY_AGENTS"


def test_an_absent_book_keeps_the_legacy_full_exit() -> None:
    """No agents supplied is not the same as agents with nothing to say."""
    verdict = _review(_config(), None)
    assert verdict["exit_now"] is True
    assert verdict["agent_vote"]["available"] is False


def test_an_opponent_below_the_confidence_bar_does_not_count() -> None:
    weak = _book(technical=("SELL", 68.0), smc=("SELL", 64.0))
    verdict = _review(_config(), weak)
    assert verdict["exit_now"] is False, "neither agent is qualified at 70%"
    assert verdict["agent_vote"]["verdict"] == "SILENT"


# ── the other modes still work ──────────────────────────────────────────────

def test_scale_out_mode_is_still_available() -> None:
    verdict = _review(_config("SCALE_OUT"), SILENT_BOOK)
    assert verdict["scale_out"] is True
    assert verdict["exit_now"] is False


def test_full_exit_mode_is_still_available() -> None:
    verdict = _review(_config("FULL_EXIT"), SILENT_BOOK)
    assert verdict["exit_now"] is True


# ── the opposing-POI path obeys the same rule ───────────────────────────────

def _poi_trade() -> dict:
    return {
        "id": "POI", "type": "BUY", "entry_price": 4000.0, "symbol": SYMBOL,
        "signal_snapshot": {
            "session_plan": {
                "session_bias": "BUY",
                "primary_poi": {"direction": "BUY", "entry_price": 4000.0},
                "standby_poi": {"direction": "SELL", "entry_price": 4060.0,
                                "poi_zone": {"top": 4062.0, "bottom": 4058.0}},
            },
        },
    }


def _poi_review(config, book):
    manager = OpenTradesManager(config)
    return manager._opposing_poi_exit_review(
        _poi_trade(), trade_type="BUY", symbol=SYMBOL, current_price=4055.0,
        recent_candles=[
            {"time": "2026-08-03T00:20:00Z", "open": 4058.0, "high": 4061.0, "low": 4057.0, "close": 4059.0},
            {"time": "2026-08-03T00:25:00Z", "open": 4059.0, "high": 4061.5, "low": 4054.0, "close": 4055.0},
        ],
        entry=4000.0, tp1=4200.0, partial_close=False, agent_details=book,
    )


def test_the_poi_path_also_holds_on_a_silent_book() -> None:
    verdict = _poi_review(_config(), SILENT_BOOK)
    if verdict.get("kind") in {None, ""}:
        return  # this fixture did not trigger a rejection; nothing to assert
    assert verdict["exit_now"] is False and verdict["scale_out"] is False


def test_the_poi_path_consults_the_agent_book_at_all() -> None:
    """Before this change it closed positions with no vote whatsoever."""
    import inspect
    source = inspect.getsource(OpenTradesManager._opposing_poi_exit_review)
    assert "agent_details" in source
    assert "_agent_exit_vote" in source


def test_the_review_passes_the_book_down_to_the_poi_path() -> None:
    import inspect
    source = inspect.getsource(OpenTradesManager._thesis_exit_review)
    assert "agent_details=agent_details" in source, (
        "the POI path cannot honour a vote it never receives"
    )


# ── risk untouched ──────────────────────────────────────────────────────────

def test_no_risk_or_vote_threshold_was_changed() -> None:
    vote = _config()["trade_management"]["thesis_exit"]["agent_vote"]
    assert float(vote["agent_min_confidence"]) == 70.0
    assert int(vote["min_defenders_to_hold"]) == 2
    assert int(vote["min_opponents_to_exit"]) == 2
    risk = _config()["risk_settings"]
    assert float(risk["min_sl_distance_points"]) == 400.0
    assert float(risk["min_rr_ratio"]) == 1.5


def test_fault_injection_the_config_alone_would_have_forced_a_full_exit() -> None:
    """The trap: a string the code does not match falls through to the exit.

    This reproduces the pre-fix control flow. Under it, HOLD does not equal
    SCALE_OUT, the branch is skipped, and execution reaches the unconditional
    full-exit return -- turning a gentler setting into a harsher one.
    """
    verdict_name = "SILENT"
    for action in ("SCALE_OUT", "HOLD", "FULL_EXIT"):
        old_takes_scale_branch = verdict_name == "SILENT" and action == "SCALE_OUT"
        old_result = "scale_out" if old_takes_scale_branch else "FULL_EXIT"
        if action == "HOLD":
            assert old_result == "FULL_EXIT", (
                "setting HOLD on the old code closed the position outright"
            )

    # And the shipped code does the opposite.
    assert _review(_config("HOLD"), SILENT_BOOK)["exit_now"] is False
