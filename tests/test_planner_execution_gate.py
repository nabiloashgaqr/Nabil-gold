from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

import scripts.run_analysis as ra


def _config() -> dict:
    return {
        "signal_requirements": {
            "min_agents_agree": 3,
            "agent_min_confidence": 70,
        }
    }


def test_planner_execution_gate_allows_three_agent_admission() -> None:
    """Three aligned agents admit the plan when dissent is within the limit."""
    decision = {
        "decision": "SELL",
        "agent_details": {
            "technical": {"direction": "SELL", "confidence": 82},
            "classical": {"direction": "SELL", "confidence": 80},
            "smc": {"direction": "SELL", "confidence": 90},
            "price_action": {"direction": "BUY", "confidence": 79},
            "multitimeframe": {"direction": "WAIT", "confidence": 55},
        },
    }
    gate = ra._planner_execution_gate(decision, _config())
    assert gate["allow"] is True
    assert gate["kind"] == "THREE_AGENT_ADMISSION"
    assert gate["support_count"] == 3


def test_three_agent_admission_is_refused_when_dissent_exceeds_the_limit() -> None:
    """Majority is not consensus.

    Three agents agreeing while two qualified agents argue the other way used
    to be admitted, because opposition was reported here and enforced only
    when the planner first built the map. A revived snapshot or a WAIT cycle
    falling back to session_bias never re-tested it, so an order could be
    placed under an "aligned" banner against live disagreement.
    """
    decision = {
        "decision": "SELL",
        "agent_details": {
            "technical": {"direction": "SELL", "confidence": 82},
            "classical": {"direction": "SELL", "confidence": 80},
            "smc": {"direction": "SELL", "confidence": 90},
            "price_action": {"direction": "BUY", "confidence": 79},
            "multitimeframe": {"direction": "BUY", "confidence": 86},
        },
    }
    gate = ra._planner_execution_gate(decision, _config())
    assert gate["allow"] is False
    assert gate["kind"] == "OPPOSED_BY_LIVE_AGENTS"
    assert gate["oppose_count"] == 2
    assert "oppose the mapped SELL" in gate["reason"]


def test_planner_execution_gate_allows_two_agent_plus_macro() -> None:
    decision = {
        "decision": "BUY",
        "confirm_source": "macro",
        "confirm_confidence": 64,
        "agent_details": {
            "technical": {"direction": "BUY", "confidence": 92},
            "classical": {"direction": "WAIT", "confidence": 30},
            "smc": {"direction": "BUY", "confidence": 84},
            "price_action": {"direction": "SELL", "confidence": 68},
            "multitimeframe": {"direction": "WAIT", "confidence": 41},
        },
    }
    gate = ra._planner_execution_gate(decision, _config())
    assert gate["allow"] is True
    assert gate["kind"] == "TWO_AGENT_CONFIRMED_ADMISSION"
    assert gate["support_count"] == 2
    assert gate["confirm_source"] == "macro"


def test_planner_execution_gate_allows_two_agent_direct_macro_context_confirmation() -> None:
    decision = {
        "decision": "SELL",
        "agent_details": {
            "technical": {"direction": "SELL", "confidence": 92},
            "classical": {"direction": "WAIT", "confidence": 27},
            "smc": {"direction": "SELL", "confidence": 84},
            "price_action": {"direction": "WAIT", "confidence": 35},
            "multitimeframe": {"direction": "WAIT", "confidence": 48},
        },
        "news_context": {
            "macro": {"macro_direction": {"bias": "BEARISH_GOLD", "confidence": 66}}
        },
    }
    gate = ra._planner_execution_gate(decision, _config())
    assert gate["allow"] is True
    assert gate["kind"] == "TWO_AGENT_CONTEXT_CONFIRMED_ADMISSION"
    assert gate["confirm_source"] == "macro"
    assert gate["support_count"] == 2


def test_planner_execution_gate_allows_two_agent_direct_gemini_macro_confirmation() -> None:
    decision = {
        "decision": "BUY",
        "agent_details": {
            "technical": {"direction": "BUY", "confidence": 78},
            "classical": {"direction": "WAIT", "confidence": 25},
            "smc": {"direction": "BUY", "confidence": 84},
            "price_action": {"direction": "WAIT", "confidence": 40},
            "multitimeframe": {"direction": "WAIT", "confidence": 48},
        },
        "gemini_macro_review": {"available": True, "macro_verdict": "BULLISH_GOLD", "confidence": 74},
    }
    gate = ra._planner_execution_gate(decision, _config())
    assert gate["allow"] is True
    assert gate["kind"] == "TWO_AGENT_CONTEXT_CONFIRMED_ADMISSION"
    assert gate["confirm_source"] == "gemini"
    assert gate["support_count"] == 2


def test_planner_execution_gate_allows_objective_aligned_two_agent_override() -> None:
    decision = {
        "decision": "BUY",
        "agent_details": {
            "technical": {"direction": "SELL", "confidence": 74},
            "classical": {"direction": "WAIT", "confidence": 30},
            "smc": {"direction": "BUY", "confidence": 84},
            "price_action": {"direction": "BUY", "confidence": 76},
            "multitimeframe": {"direction": "WAIT", "confidence": 55},
        },
        "session_plan": {
            "market_objective_direction": "BUY",
            "objective_alignment": "ALIGNED_WITH_MARKET_OBJECTIVE",
            "scenario_type": "STRUCTURE_CONTINUATION",
            "poi_classification": "EXTREME_POI",
            "structure_trend": "BULLISH",
            "recent_sweep": {"type": "sell_side"},
        },
    }
    gate = ra._planner_execution_gate(decision, _config())
    assert gate["allow"] is True
    assert gate["kind"] == "OBJECTIVE_ALIGNED_TWO_AGENT_OVERRIDE"
    assert gate["support_count"] == 2
    assert "smc" in gate["support_agents"]


def test_planner_execution_gate_blocks_without_required_admission() -> None:
    decision = {
        "decision": "SELL",
        "agent_details": {
            "technical": {"direction": "BUY", "confidence": 92},
            "classical": {"direction": "WAIT", "confidence": 30},
            "smc": {"direction": "SELL", "confidence": 90},
            "price_action": {"direction": "SELL", "confidence": 79},
            "multitimeframe": {"direction": "BUY", "confidence": 86},
        },
        "session_plan": {
            "market_objective_direction": "BUY",
            "objective_alignment": "COUNTER_OBJECTIVE_REVERSAL_CONFIRMED",
            "scenario_type": "LIQUIDITY_REVERSAL",
            "poi_classification": "EXTREME_POI",
            "structure_trend": "BULLISH",
            "recent_sweep": {"type": "sell_side"},
        },
    }
    gate = ra._planner_execution_gate(decision, _config())
    assert gate["allow"] is False
    # Two qualified agents argue the other way, so the refusal now names the
    # dissent rather than reporting a bare shortfall in support. Either way
    # the leg is refused; the reason is simply the truer one.
    assert gate["oppose_count"] == 2
    assert "oppose the mapped SELL" in gate["reason"]


def test_gate_admits_a_ready_plan_when_live_consensus_reads_wait() -> None:
    """The planner maps a direction in advance and waits at a level.

    Live consensus therefore often reads WAIT while a confirmed map exists.
    Requiring decision to be directional closed the gate before the agents were
    counted, so a READY map was published every cycle and no pending order was
    ever created.
    """
    decision = {
        "decision": "WAIT",
        "session_plan": {"plan_ready": True, "session_bias": "SELL"},
        "agent_details": {
            "technical": {"direction": "WAIT", "confidence": 44},
            "classical": {"direction": "SELL", "confidence": 75},
            "smc": {"direction": "SELL", "confidence": 82},
            "price_action": {"direction": "SELL", "confidence": 73},
            "multitimeframe": {"direction": "WAIT", "confidence": 20},
        },
    }
    gate = ra._planner_execution_gate(decision, _config())
    assert gate["allow"] is True
    assert gate["kind"] == "THREE_AGENT_ADMISSION"
    assert gate["support_count"] == 3


def test_gate_still_requires_agent_support_under_the_plan_fallback() -> None:
    """Falling back to the mapped bias must not bypass the agent count."""
    decision = {
        "decision": "WAIT",
        "session_plan": {"plan_ready": True, "session_bias": "SELL"},
        "agent_details": {
            "technical": {"direction": "WAIT", "confidence": 44},
            "classical": {"direction": "SELL", "confidence": 75},
            "smc": {"direction": "WAIT", "confidence": 30},
            "price_action": {"direction": "WAIT", "confidence": 20},
            "multitimeframe": {"direction": "WAIT", "confidence": 20},
        },
    }
    assert ra._planner_execution_gate(decision, _config())["allow"] is False


def test_gate_does_not_fall_back_to_an_unready_plan() -> None:
    decision = {
        "decision": "WAIT",
        "session_plan": {"plan_ready": False, "session_bias": "SELL"},
        "agent_details": {
            "classical": {"direction": "SELL", "confidence": 75},
            "smc": {"direction": "SELL", "confidence": 82},
            "price_action": {"direction": "SELL", "confidence": 73},
        },
    }
    gate = ra._planner_execution_gate(decision, _config())
    assert gate["allow"] is False
    assert "no approved directional admission" in gate["reason"]
