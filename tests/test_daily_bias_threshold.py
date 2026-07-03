"""Production guard for Daily Bias counter-trend override thresholds."""

from __future__ import annotations

import copy

from agents.decision_agent import DecisionAgent
from utils.helpers import load_config


def _result(confidence: float, buy_agents: int = 3) -> dict:
    return {
        "signal": "BUY",
        "decision": "BUY",
        "confidence": confidence,
        "warnings": [],
        "reasoning": "Classic consensus approved counter-trend setup",
        "votes": {
            "BUY": [
                {"agent": f"agent_{i}", "confidence": 80 + i, "score": 0.1}
                for i in range(buy_agents)
            ],
            "SELL": [],
            "WAIT": [],
        },
        "classic": {"buy_count": buy_agents, "sell_count": 0},
    }


def _context() -> dict:
    return {
        "session": {"trading_allowed": True, "allow_signals": True},
        "daily_bias": {"enabled": True, "bias": "BEARISH", "confidence": 95.0},
        "risk": {"approved": True},
        "news": {"can_trade": True, "market_status": "SAFE"},
    }


def test_config_countertrend_thresholds() -> None:
    config = load_config()
    assert config["daily_bias_filter"]["contrarian_min_confidence"] == 80
    assert config["daily_bias_filter"]["contrarian_min_confidence_two_agents"] == 80
    assert config["daily_bias_filter"]["contrarian_min_agents_for_lower_confidence"] == 3
    assert config["daily_bias_filter"]["block_strong_contrarian_below"] == 80


def test_countertrend_one_agent_even_at_80_percent_is_blocked() -> None:
    config = load_config()
    agent = DecisionAgent(config)
    result = agent._apply_safety_filters(_result(85, buy_agents=1), copy.deepcopy(_context()))
    assert result["decision"] == "WAIT"
    assert result["signal"] == "WAIT"
    assert any("Daily Bias" in w and "≥3" in w for w in result.get("warnings", []))


def test_countertrend_three_agents_at_80_percent_is_allowed() -> None:
    config = load_config()
    agent = DecisionAgent(config)
    result = agent._apply_safety_filters(_result(80, buy_agents=3), copy.deepcopy(_context()))
    assert result["decision"] == "BUY"
    assert result["signal"] == "BUY"
    assert not any("Daily Bias" in w for w in result.get("warnings", []))


def test_countertrend_two_agents_below_80_percent_is_blocked() -> None:
    config = load_config()
    agent = DecisionAgent(config)
    result = agent._apply_safety_filters(_result(79.9, buy_agents=2), copy.deepcopy(_context()))
    assert result["decision"] == "WAIT"
    assert result["signal"] == "WAIT"
    assert any("Daily Bias" in w and "≥80" in w for w in result.get("warnings", []))
