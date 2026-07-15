from __future__ import annotations

from agents.decision_agent import DecisionAgent


def _base_config() -> dict:
    return {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {"min_agents_agree": 2, "min_consensus_confidence": 70, "agent_min_confidence": 68},
        "agent_weights": {"technical": 0.20, "classical": 0.25, "smc": 0.20, "price_action": 0.20, "multitimeframe": 0.15},
    }


def test_liquidity_reversal_requires_trigger_confirmation() -> None:
    agent = DecisionAgent(_base_config())
    result = agent.analyze(
        {
            "smc": {
                "signal": "SELL",
                "confidence": 84,
                "market_structure": {"trend": "BEARISH", "structure_quality": "MODERATE"},
                "liquidity": {"recent_sweep": {"occurred": True, "type": "buy_side", "confirmation": "STRONG"}},
                "setup_structure": {
                    "setup_type": "LIQUIDITY_REVERSAL",
                    "lead_agent": "smc",
                    "poi_rank_score": 48,
                    "trigger_state": "TOUCH_NO_REJECTION",
                    "trigger_score": 58,
                },
            },
            "price_action": {"signal": "SELL", "confidence": 78},
            "technical": {"signal": "WAIT", "confidence": 40},
            "classical": {"signal": "WAIT", "confidence": 40},
            "multitimeframe": {"signal": "WAIT", "confidence": 55},
            "session": {"trading_allowed": True, "allow_signals": True},
            "news": {"can_trade": True, "market_status": "SAFE"},
            "daily_bias": {"bias": "BEARISH", "enabled": True},
            "risk": {"approved": True},
        }
    )
    assert result["signal"] == "WAIT"
    assert any("trigger state" in warning.lower() for warning in result.get("warnings", []))


def test_liquidity_reversal_passes_when_trigger_and_sweep_are_strong() -> None:
    agent = DecisionAgent(_base_config())
    result = agent.analyze(
        {
            "smc": {
                "signal": "SELL",
                "confidence": 84,
                "market_structure": {"trend": "BEARISH", "structure_quality": "STRONG"},
                "liquidity": {"recent_sweep": {"occurred": True, "type": "buy_side", "confirmation": "STRONG"}},
                "setup_structure": {
                    "setup_type": "LIQUIDITY_REVERSAL",
                    "lead_agent": "smc",
                    "poi_rank_score": 52,
                    "trigger_state": "REJECTION_CONFIRMED",
                    "trigger_score": 82,
                },
            },
            "price_action": {"signal": "SELL", "confidence": 78},
            "technical": {"signal": "WAIT", "confidence": 40},
            "classical": {"signal": "WAIT", "confidence": 40},
            "multitimeframe": {"signal": "WAIT", "confidence": 55},
            "session": {"trading_allowed": True, "allow_signals": True},
            "news": {"can_trade": True, "market_status": "SAFE"},
            "daily_bias": {"bias": "BEARISH", "enabled": True},
            "risk": {"approved": True},
        }
    )
    assert result["signal"] == "SELL"
    assert not result.get("warnings")


def test_trend_pullback_blocks_weak_structure_even_with_consensus() -> None:
    agent = DecisionAgent(_base_config())
    result = agent.analyze(
        {
            "smc": {
                "signal": "BUY",
                "confidence": 75,
                "market_structure": {"trend": "BULLISH", "structure_quality": "WEAK"},
                "setup_structure": {
                    "setup_type": "TREND_CONTINUATION",
                    "poi_rank_score": 18,
                    "trigger_state": "AT_POI_WAIT_TRIGGER",
                    "trigger_score": 45,
                },
            },
            "price_action": {"signal": "BUY", "confidence": 76},
            "technical": {"signal": "WAIT", "confidence": 45},
            "classical": {"signal": "BUY", "confidence": 79},
            "multitimeframe": {"signal": "BUY", "confidence": 82, "setup_type": "TREND_CONTINUATION"},
            "session": {"trading_allowed": True, "allow_signals": True},
            "news": {"can_trade": True, "market_status": "SAFE"},
            "daily_bias": {"bias": "BULLISH", "enabled": True},
            "risk": {"approved": True},
        }
    )
    assert result["signal"] == "WAIT"
    assert any("structure quality" in warning.lower() or "poi rank" in warning.lower() for warning in result.get("warnings", []))
