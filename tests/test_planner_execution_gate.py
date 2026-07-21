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
    assert gate["allow"] is True
    assert gate["kind"] == "THREE_AGENT_ADMISSION"
    assert gate["support_count"] == 3


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
    }
    gate = ra._planner_execution_gate(decision, _config())
    assert gate["allow"] is False
    assert "requires 3 qualified agents or 2 agents + macro/gemini" in gate["reason"]
