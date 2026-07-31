"""The cancellation story must describe the reason that actually fired.

2026-07-31, order TRADE_20260731_131634_538414_6e31ddf6:

    Execution story:     "Main mapped execution was cancelled before
                          activation because the day map lost validity."
    Cancellation reason: "Planner pending cancelled as stale: market covered
                          61% of target path without fill"

Two different claims in one card, and the headline one is false. The day map
had not lost validity -- it was RIGHT. It called the move down, price went
exactly there, and the system's own live trade took TP2 at 4029.17 on that
very path. The order was simply never reachable.

`_plan_execution_context` hardcoded that sentence for any PENDING_CANCELLED
on a PRIMARY/STARTER leg, ignoring the reason entirely. Telling the user his
map failed on the day it worked teaches him to distrust a correct map -- the
opposite of what the message exists to do.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager  # noqa: E402
from utils.helpers import load_config  # noqa: E402


def _story_for(reason: str) -> str:
    manager = OpenTradesManager(load_config())
    trade = {
        "id": "TRADE_20260731_131634_538414_6e31ddf6",
        "symbol": "XAU/USD", "type": "SELL", "status": "PENDING",
        "entry_price": 4076.93,
        # The role is read from setup_context.pending_plan_role -- this
        # mirrors how a real planner-ladder order is stored.
        "signal_snapshot": {
            "setup_context": {
                "pending_plan_role": "PRIMARY",
                "direction": "SELL",
                "scenario_id": "SELL_MAIN_20260731",
            },
            "session_plan": {
                "scenario_id": "SELL_MAIN_20260731",
                "session_bias": "SELL",
                "primary_poi": {"entry_price": 4076.93},
            },
        },
    }
    evaluation = {
        "events": ["PENDING_CANCELLED"],
        "updates": {"status": "CANCELLED", "reasons": [reason]},
    }
    context = manager._plan_execution_context(trade, evaluation, [trade])
    return str((context or {}).get("story") or "")


REAL_REASON = (
    "Planner pending cancelled as stale: market covered 61% of target path "
    "without fill"
)


def test_the_target_path_reason_no_longer_blames_the_map() -> None:
    story = _story_for(REAL_REASON)
    assert story, "a cancelled primary leg must still explain itself"
    assert "lost validity" not in story.lower(), (
        "the map was correct -- price reached the objective it predicted. "
        f"Got: {story}"
    )
    assert "objective" in story.lower() or "without ever returning" in story.lower()


def test_the_story_and_the_reason_agree() -> None:
    """Whatever the story says must be consistent with the stated reason."""
    story = _story_for(REAL_REASON).lower()
    assert ("reached" in story or "objective" in story), (
        "the reason says the market completed the planned path; the story "
        "must say the same thing in plain words"
    )


def test_a_timeout_cancellation_says_it_waited_too_long() -> None:
    story = _story_for(
        "Planner pending cancelled as stale: waiting too long (7.2h)"
    ).lower()
    assert "waited" in story or "window" in story
    assert "lost validity" not in story


def test_a_genuine_map_invalidation_still_says_so() -> None:
    """The original sentence is correct when the map really did fail."""
    story = _story_for("Day map invalidated: structure flipped bullish")
    assert "lost validity" in story.lower(), (
        "this fix narrows a wrong default, it must not delete a true message"
    )


def test_fault_injection_the_hardcoded_sentence_contradicted_the_reason() -> None:
    """The pre-fix behaviour, stated as an assertion."""
    old_story = (
        "Main mapped execution was cancelled before activation because the "
        "day map lost validity."
    )
    assert "lost validity" in old_story
    assert "target path" not in old_story.lower(), (
        "the old story could not mention the actual reason because it never "
        "read it -- one sentence for every cancellation cause"
    )
    assert _story_for(REAL_REASON) != old_story
