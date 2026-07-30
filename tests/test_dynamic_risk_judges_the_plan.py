"""Dynamic risk must judge a planner map on the map's own numbers.

On 2026-07-30 a CONFIRMED SELL DAY MAP graded A+ 98.6% was refused with:

    WAIT signal blocked at dynamic risk —
    Confidence 0.0% below Dynamic Risk requirement 65.0%

The map never had 0.0% confidence. When the live consensus reads WAIT, the
gate substitutes the plan's session_bias for the direction -- correctly, so a
trading halt also covers the ladder route -- but it kept reading `confidence`
and `quality` off the WAIT decision, where both are 0.0. Every planner-led
map on a WAIT cycle was therefore blocked by arithmetic that described a
different object.

Fault injection: drop the planner_confidence substitution and
`test_confirmed_plan_survives_on_a_wait_cycle` fails with the 0.0% message.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.run_analysis import _dynamic_risk_block_for_cycle

DYN = {"enabled": True, "can_trade": True,
       "min_confidence_required": 65.0, "min_quality_score": 0.0}
WAIT = {"decision": "WAIT", "confidence": 0.0, "quality": {"score": 0.0}}


def _gate(decision_type: str, decision: dict, plan: dict, dyn: dict = DYN):
    """Call the production function itself -- never a copy of its logic.

    An earlier version of this file re-implemented the rule here. It passed
    against a deliberately broken build, because the test was grading its own
    mirror instead of the shipped code.
    """
    return _dynamic_risk_block_for_cycle(
        decision_type=decision_type,
        decision=decision,
        session_plan=plan,
        dynamic_risk=dyn,
    )


def test_confirmed_plan_survives_on_a_wait_cycle() -> None:
    plan = {"plan_ready": True, "session_bias": "SELL", "planner_confidence": 98.6}
    assert _gate("WAIT", WAIT, plan) is None


def test_the_zero_percent_message_can_no_longer_appear_for_a_ready_plan() -> None:
    plan = {"plan_ready": True, "session_bias": "SELL", "planner_confidence": 98.6}
    reason = _gate("WAIT", WAIT, plan)
    assert reason is None or "0.0%" not in reason


def test_a_weak_plan_is_still_refused() -> None:
    """The gate must keep its teeth: only the number it reads was wrong."""
    plan = {"plan_ready": True, "session_bias": "SELL", "planner_confidence": 40.0}
    reason = _gate("WAIT", WAIT, plan)
    assert reason is not None
    assert "40.0%" in reason


def test_a_trading_halt_still_stops_a_perfect_plan() -> None:
    halted = {**DYN, "can_trade": False, "warnings": ["3 consecutive losses"]}
    plan = {"plan_ready": True, "session_bias": "SELL", "planner_confidence": 98.6}
    assert _gate("WAIT", WAIT, plan, halted) == "3 consecutive losses"


def test_a_live_directional_cycle_is_unchanged() -> None:
    """When the agents themselves say BUY/SELL, their own confidence rules."""
    live = {"decision": "SELL", "confidence": 50.0, "quality": {"score": 50.0}}
    plan = {"plan_ready": True, "session_bias": "SELL", "planner_confidence": 98.6}
    reason = _gate("SELL", live, plan)
    assert reason is not None and "50.0%" in reason


def test_an_unready_plan_on_a_wait_cycle_is_not_gated_at_all() -> None:
    plan = {"plan_ready": False, "session_bias": "SELL", "planner_confidence": 98.6}
    assert _gate("WAIT", WAIT, plan) is None
