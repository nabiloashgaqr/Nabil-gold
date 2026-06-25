"""Production guard for Daily Bias counter-trend override threshold."""

from __future__ import annotations

import copy

from agents.decision_agent import DecisionAgent
from utils.helpers import load_config


def _result(confidence: float) -> dict:
    return {
        "signal": "BUY",
        "decision": "BUY",
        "confidence": confidence,
        "warnings": [],
        "reasoning": "Groq approved counter-trend scalp",
    }


def _context() -> dict:
    return {
        "session": {"trading_allowed": True, "allow_signals": True},
        "daily_bias": {"enabled": True, "bias": "BEARISH", "confidence": 95.0},
        "risk": {"approved": True},
        "news": {"can_trade": True, "market_status": "SAFE"},
    }


def test_config_countertrend_threshold_is_70() -> None:
    config = load_config()
    assert config["daily_bias_filter"]["contrarian_min_confidence"] == 70
    assert config["daily_bias_filter"]["block_strong_contrarian_below"] == 70


def test_countertrend_trade_at_71_percent_is_allowed() -> None:
    config = load_config()
    agent = DecisionAgent(config)
    result = agent._apply_safety_filters(_result(71), copy.deepcopy(_context()))
    assert result["decision"] == "BUY"
    assert result["signal"] == "BUY"
    assert not any("Daily Bias" in w for w in result.get("warnings", []))


def test_countertrend_trade_below_70_percent_is_blocked() -> None:
    config = load_config()
    agent = DecisionAgent(config)
    result = agent._apply_safety_filters(_result(69.9), copy.deepcopy(_context()))
    assert result["decision"] == "WAIT"
    assert result["signal"] == "WAIT"
    assert any("Daily Bias" in w and "≥70" in w for w in result.get("warnings", []))
