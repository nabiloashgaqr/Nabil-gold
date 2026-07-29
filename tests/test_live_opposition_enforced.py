"""Live agent dissent must veto a mapped direction, not merely annotate it.

A signal went out headed "Admission: 3 qualified agents aligned with the
mapped direction" while the very same message listed Technical 92%, Price
Action 84% and Multi-Timeframe 92% all opposing. The gate counted support and
printed opposition without acting on it, on the reasoning that the planner had
already applied `max_opposing_agents_for_ready`.

That reasoning has a hole. The planner tests dissent when it *builds* a map;
this gate is the last code to see the agents as they are immediately before an
order is created. Two paths cross that gap:

  - `_revive_recent_ready_plan` replays a snapshot built hours earlier;
  - a WAIT cycle falls back to the plan's `session_bias`, re-counting agents
    against a direction the live consensus did not pick.

These tests pin the veto at the point of execution.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import scripts.run_analysis as ra

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

AGENTS = ["technical", "classical", "smc", "price_action", "multitimeframe"]


def _details(*directions, confidence: float = 85.0):
    """Build agent_details from a per-agent direction list."""
    out = {}
    for name, direction in zip(AGENTS, directions):
        conf = 30.0 if direction == "WAIT" else confidence
        out[name] = {"direction": direction, "confidence": conf}
    return out


def _gate(details, side="BUY", plan_ready=True, decision=None):
    payload = {
        "decision": decision if decision is not None else side,
        "agent_details": details,
        "session_plan": {"plan_ready": plan_ready, "session_bias": side},
    }
    return ra._planner_execution_gate(payload, CONFIG)


def test_the_live_signal_that_shipped_is_now_refused() -> None:
    """The exact agent split from the 2026-07-29 BUY."""
    details = {
        "technical": {"direction": "SELL", "confidence": 92},
        "classical": {"direction": "WAIT", "confidence": 30},
        "smc": {"direction": "WAIT", "confidence": 27},
        "price_action": {"direction": "SELL", "confidence": 84},
        "multitimeframe": {"direction": "SELL", "confidence": 92},
    }

    gate = _gate(details, side="BUY", decision="WAIT")

    assert gate["allow"] is False
    assert gate["kind"] == "OPPOSED_BY_LIVE_AGENTS"
    assert gate["oppose_count"] == 3
    assert "oppose the mapped BUY" in gate["reason"]


def test_majority_does_not_override_strong_dissent() -> None:
    """Three for, two against: refused."""
    gate = _gate(_details("BUY", "BUY", "BUY", "SELL", "SELL"))

    assert gate["allow"] is False
    assert gate["oppose_count"] == 2


def test_dissent_within_the_limit_still_admits() -> None:
    """One dissenter is tolerated; the gate must not become a blanket veto."""
    gate = _gate(_details("BUY", "BUY", "BUY", "SELL", "WAIT"))

    assert gate["allow"] is True
    assert gate["kind"] == "THREE_AGENT_ADMISSION"


def test_low_confidence_dissent_does_not_count() -> None:
    """Only qualified agents vote, on both sides of the ledger."""
    details = _details("BUY", "BUY", "BUY", "SELL", "SELL")
    details["price_action"]["confidence"] = 40
    details["multitimeframe"]["confidence"] = 35

    gate = _gate(details)

    assert gate["allow"] is True
    assert gate["oppose_agents"] == []


def test_refusals_report_the_dissent_they_found() -> None:
    """A rejection that hides opposition reads as a near miss."""
    gate = _gate(_details("BUY", "WAIT", "SELL", "SELL", "WAIT"))

    assert gate["allow"] is False
    assert gate["oppose_count"] == 2
    assert set(gate["oppose_agents"]) == {"smc", "price_action"}


def test_limit_defaults_safely_when_planner_config_is_absent() -> None:
    """A missing session_planner block must not collapse the limit to zero."""
    payload = {
        "decision": "SELL",
        "agent_details": _details("SELL", "SELL", "SELL", "BUY", "WAIT"),
    }
    gate = ra._planner_execution_gate(
        payload, {"signal_requirements": {"min_agents_agree": 3, "agent_min_confidence": 70}}
    )

    assert gate["allow"] is True, "one dissenter must still be tolerated by default"


def test_enforcement_is_surgical() -> None:
    """Sweep every combination; only 3-support/2-oppose should change.

    Guards against the veto quietly tightening admission across the board.
    """
    newly_refused = []
    for combo in itertools.product(["BUY", "SELL", "WAIT"], repeat=5):
        gate = _gate(_details(*combo))
        support = combo.count("BUY")
        opposed = combo.count("SELL")
        admitted_before = support >= 3          # the old rule
        if admitted_before and not gate["allow"]:
            newly_refused.append((support, opposed))

    assert newly_refused, "the veto must actually refuse something"
    assert set(newly_refused) == {(3, 2)}, (
        f"enforcement leaked beyond the intended case: {sorted(set(newly_refused))}"
    )
