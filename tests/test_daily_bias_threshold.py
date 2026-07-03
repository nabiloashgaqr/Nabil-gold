"""Production guard for Daily Bias counter-trend override thresholds.

Three layers of counter-trend protection:
1. block_strong_contrarian_below: if daily bias confidence >= this → block ALL contrarian
2. contrarian_min_agents_for_lower_confidence: minimum agents agreeing (3)
3. contrarian_min_confidence: minimum net weighted confidence (80%)
"""

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


def _context(bias_conf: float = 95.0) -> dict:
    return {
        "session": {"trading_allowed": True, "allow_signals": True},
        "daily_bias": {"enabled": True, "bias": "BEARISH", "confidence": bias_conf},
        "risk": {"approved": True},
        "news": {"can_trade": True, "market_status": "SAFE"},
    }


# ── Config threshold tests ──────────────────────────────────────────────────

def test_config_countertrend_thresholds() -> None:
    config = load_config()
    assert config["daily_bias_filter"]["contrarian_min_confidence"] == 80
    assert config["daily_bias_filter"]["contrarian_min_confidence_two_agents"] == 80
    assert config["daily_bias_filter"]["contrarian_min_agents_for_lower_confidence"] == 3
    assert config["daily_bias_filter"]["block_strong_contrarian_below"] == 80


# ── Layer 1: Strong bias block (block_strong_contrarian_below=80) ───────────

def test_strong_bias_blocks_contran_even_with_3_agents_at_80() -> None:
    """When bias confidence >= block_strong_contrarian_below, block ALL contrarian."""
    config = load_config()
    agent = DecisionAgent(config)
    # bias_conf=95% >= block_below=80% → block regardless of agents/confidence
    result = agent._apply_safety_filters(_result(85, buy_agents=3), copy.deepcopy(_context(bias_conf=95.0)))
    assert result["decision"] == "WAIT"
    assert result["signal"] == "WAIT"
    assert any("Daily Bias" in w and "80" in w and "threshold" in w for w in result.get("warnings", []))


def test_strong_bias_at_exactly_80_blocks_contran() -> None:
    """bias_conf exactly at the threshold (80%) should also block."""
    config = load_config()
    agent = DecisionAgent(config)
    result = agent._apply_safety_filters(_result(85, buy_agents=3), copy.deepcopy(_context(bias_conf=80.0)))
    assert result["decision"] == "WAIT"
    assert result["signal"] == "WAIT"


def test_bias_just_below_threshold_allows_contran_check() -> None:
    """bias_conf just below threshold (79.9%) should NOT trigger strong block,
    but may still block via agent count / confidence check."""
    config = load_config()
    agent = DecisionAgent(config)
    # 3 agents, 80% confidence, bias=79.9% < 80 → strong block NOT triggered
    result = agent._apply_safety_filters(_result(80, buy_agents=3), copy.deepcopy(_context(bias_conf=79.9)))
    assert result["decision"] == "BUY"
    assert result["signal"] == "BUY"


# ── Layer 2+3: Agent count and confidence (when bias < threshold) ───────────

def test_contran_one_agent_blocked_by_count() -> None:
    """With bias below strong threshold, 1 agent is still blocked (need 3)."""
    config = load_config()
    agent = DecisionAgent(config)
    result = agent._apply_safety_filters(_result(85, buy_agents=1), copy.deepcopy(_context(bias_conf=70.0)))
    assert result["decision"] == "WAIT"
    assert result["signal"] == "WAIT"
    assert any("Daily Bias" in w and "≥3" in w for w in result.get("warnings", []))


def test_contran_two_agents_blocked_by_count() -> None:
    """2 agents also blocked (need 3)."""
    config = load_config()
    agent = DecisionAgent(config)
    result = agent._apply_safety_filters(_result(85, buy_agents=2), copy.deepcopy(_context(bias_conf=70.0)))
    assert result["decision"] == "WAIT"
    assert result["signal"] == "WAIT"
    assert any("Daily Bias" in w and "≥3" in w for w in result.get("warnings", []))


def test_contran_three_agents_below_80_conf_blocked() -> None:
    """3 agents but < 80% confidence is blocked."""
    config = load_config()
    agent = DecisionAgent(config)
    result = agent._apply_safety_filters(_result(79.9, buy_agents=3), copy.deepcopy(_context(bias_conf=70.0)))
    assert result["decision"] == "WAIT"
    assert result["signal"] == "WAIT"
    assert any("Daily Bias" in w and "≥80" in w for w in result.get("warnings", []))


def test_contran_three_agents_at_80_conf_allowed() -> None:
    """3 agents at exactly 80% confidence is allowed when bias < strong threshold."""
    config = load_config()
    agent = DecisionAgent(config)
    result = agent._apply_safety_filters(_result(80, buy_agents=3), copy.deepcopy(_context(bias_conf=70.0)))
    assert result["decision"] == "BUY"
    assert result["signal"] == "BUY"
    assert not any("Daily Bias" in w for w in result.get("warnings", []))


# ── Non-contrarian is never blocked by daily bias ────────────────────────────

def test_non_contran_never_blocked_by_bias() -> None:
    """Signal in same direction as bias should never be blocked."""
    config = load_config()
    agent = DecisionAgent(config)
    # BUY signal + BULLISH bias = same direction, not contrarian
    ctx = {
        "session": {"trading_allowed": True, "allow_signals": True},
        "daily_bias": {"enabled": True, "bias": "BULLISH", "confidence": 95.0},
        "risk": {"approved": True},
        "news": {"can_trade": True, "market_status": "SAFE"},
    }
    result = agent._apply_safety_filters(_result(75, buy_agents=3), copy.deepcopy(ctx))
    assert result["decision"] == "BUY"
    assert result["signal"] == "BUY"
