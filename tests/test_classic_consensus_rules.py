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
    # min_agents_agree is now 3, so 1 agent is always rejected
    assert "Need at least" in result["rejection_reason"]


def test_three_agents_weighted_confidence_above_threshold_is_allowed() -> None:
    agent = DecisionAgent(load_config())
    min_cons = agent.min_consensus_confidence
    votes = {
        "BUY": [
            _vote("technical", min_cons + 5, 0.20),
            _vote("smc", min_cons + 5, 0.20),
            _vote("classical", min_cons + 5, 0.25),
        ],
        "SELL": [],
        "WAIT": [],
    }

    result = agent._classic_decision(votes)

    assert result["decision"] == "BUY"
    assert result["confidence"] >= min_cons
    assert result["consensus"]["BUY"]["support_count"] >= 3


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
