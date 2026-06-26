"""Production classic-consensus entry rules."""

from __future__ import annotations

from agents.decision_agent import DecisionAgent
from utils.helpers import load_config


def _vote(agent: str, confidence: float, weight: float) -> dict:
    return {
        "agent": agent,
        "confidence": confidence,
        "adjusted_confidence": confidence,
        "weight": weight,
        "score": (confidence / 100.0) * weight,
    }


def test_single_strong_agent_is_not_enough_in_production_config() -> None:
    agent = DecisionAgent(load_config())
    votes = {
        "BUY": [_vote("technical", 92, 0.20)],
        "SELL": [],
        "WAIT": [],
    }

    result = agent._classic_decision(votes)

    assert result["decision"] == "WAIT"
    assert "Need at least 2" in result["rejection_reason"]


def test_two_agents_weighted_confidence_above_65_is_allowed() -> None:
    agent = DecisionAgent(load_config())
    votes = {
        "BUY": [_vote("technical", 66, 0.20), _vote("smc", 66, 0.25)],
        "SELL": [],
        "WAIT": [],
    }

    result = agent._classic_decision(votes)

    assert result["decision"] == "BUY"
    assert result["confidence"] >= 65
    assert result["consensus"]["BUY"]["support_count"] == 2


def test_opposing_agent_subtracts_from_signal_confidence() -> None:
    agent = DecisionAgent(load_config())
    votes = {
        "BUY": [_vote("technical", 66, 0.20), _vote("smc", 66, 0.25)],
        "SELL": [_vote("price_action", 80, 0.15)],
        "WAIT": [],
    }

    result = agent._classic_decision(votes)

    assert result["decision"] == "WAIT"
    assert result["consensus"]["BUY"]["support_avg_confidence"] >= 65
    assert result["consensus"]["BUY"]["opposition_penalty"] > 0
    assert result["consensus"]["BUY"]["confidence"] < 65
