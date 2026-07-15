from __future__ import annotations

from agents.decision_agent import DecisionAgent


BASE_CONFIG = {
    "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
    "signal_requirements": {
        "min_agents_agree": 3,
        "min_consensus_confidence": 72,
        "agent_min_confidence": 70,
    },
    "agent_weights": {
        "technical": 0.20,
        "classical": 0.25,
        "smc": 0.20,
        "price_action": 0.20,
        "multitimeframe": 0.15,
    },
}


def test_liquidity_reversal_profile_allows_two_agent_entry_when_smc_leads() -> None:
    agent = DecisionAgent(BASE_CONFIG)
    result = agent.decide(
        {
            "smc": {
                "signal": "SELL",
                "confidence": 84,
                "setup_structure": {"setup_type": "LIQUIDITY_REVERSAL", "lead_agent": "smc"},
            },
            "price_action": {"signal": "SELL", "confidence": 78},
            "technical": {"signal": "WAIT", "confidence": 40},
            "classical": {"signal": "WAIT", "confidence": 40},
            "multitimeframe": {"signal": "WAIT", "confidence": 55},
            "risk": {
                "entry": {"price": 4065.0, "zone": {"low": 4063.5, "high": 4066.2}, "kind": "LIMIT", "order_type": "SELL_LIMIT"},
                "stop_loss": {"price": 4073.7},
                "take_profit": {"tp1": {"price": 4057.0, "rr_ratio": 1.2}, "tp2": {"price": 4021.4, "rr_ratio": 3.0}},
                "position_size": {},
                "summary": "ok",
                "approved": True,
            },
            "current_price": 4065.0,
            "session": {"trading_allowed": True, "allow_signals": True},
            "news": {"can_trade": True, "market_status": "SAFE"},
            "daily_bias": {"bias": "BEARISH", "enabled": True},
        }
    )
    assert result["decision"] == "SELL"
    assert result["strategy_profile"]["name"] == "liquidity_reversal"
    assert result["classic"]["profile"]["lead_agent"] == "smc"


def test_liquidity_reversal_profile_blocks_when_lead_agent_not_aligned() -> None:
    agent = DecisionAgent(BASE_CONFIG)
    result = agent.analyze(
        {
            "smc": {
                "signal": "WAIT",
                "confidence": 75,
                "setup_structure": {"setup_type": "LIQUIDITY_REVERSAL", "lead_agent": "smc"},
            },
            "price_action": {"signal": "SELL", "confidence": 79},
            "technical": {"signal": "SELL", "confidence": 76},
            "classical": {"signal": "WAIT", "confidence": 40},
            "multitimeframe": {"signal": "WAIT", "confidence": 55},
            "session": {"trading_allowed": True, "allow_signals": True},
            "news": {"can_trade": True, "market_status": "SAFE"},
            "daily_bias": {"bias": "NEUTRAL", "enabled": True},
            "risk": {"approved": True},
        }
    )
    assert result["signal"] == "WAIT"
    assert result["classic"]["profile"]["name"] == "liquidity_reversal"
    assert "requires lead agent smc" in str(result["classic"]["rejection_reason"]).lower()


def test_trend_pullback_profile_prefers_multitimeframe_lead() -> None:
    agent = DecisionAgent(BASE_CONFIG)
    result = agent.decide(
        {
            "smc": {"signal": "BUY", "confidence": 74},
            "price_action": {"signal": "BUY", "confidence": 77},
            "technical": {"signal": "WAIT", "confidence": 52},
            "classical": {"signal": "BUY", "confidence": 80},
            "multitimeframe": {"signal": "BUY", "confidence": 82, "setup_type": "TREND_CONTINUATION"},
            "risk": {
                "entry": {"price": 4032.0, "zone": {"low": 4030.5, "high": 4032.2}, "kind": "LIMIT", "order_type": "BUY_LIMIT"},
                "stop_loss": {"price": 4015.0},
                "take_profit": {"tp1": {"price": 4046.0, "rr_ratio": 1.1}, "tp2": {"price": 4070.0, "rr_ratio": 2.8}},
                "position_size": {},
                "summary": "ok",
                "approved": True,
            },
            "current_price": 4032.0,
            "session": {"trading_allowed": True, "allow_signals": True},
            "news": {"can_trade": True, "market_status": "SAFE"},
            "daily_bias": {"bias": "BULLISH", "enabled": True},
        }
    )
    assert result["decision"] == "BUY"
    assert result["strategy_profile"]["name"] == "trend_pullback"
    assert result["classic"]["profile"]["lead_agent"] == "multitimeframe"
