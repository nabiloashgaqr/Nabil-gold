from __future__ import annotations

from datetime import datetime, timezone

from agents.decision_agent import DecisionAgent
from services.learning_service import LearningReport, LearningService


def _base_config() -> dict:
    return {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {
            "min_agents_agree": 2,
            "min_consensus_confidence": 70,
            "agent_min_confidence": 68,
        },
        "learning": {
            "enabled": True,
            "contextual_weights_enabled": True,
            "contextual_min_sample": 3,
            "contextual_blend": 0.35,
        },
        "agent_weights": {
            "technical": 0.20,
            "classical": 0.25,
            "smc": 0.20,
            "price_action": 0.20,
            "multitimeframe": 0.15,
        },
        "strategy_profiles": {
            "liquidity_reversal": {
                "min_agents_agree": 2,
                "min_consensus_confidence": 70,
                "agent_min_confidence": 68,
                "lead_agent": "smc",
                "require_lead_alignment": True,
                "weight_overrides": {
                    "smc": 0.35,
                    "price_action": 0.25,
                    "multitimeframe": 0.20,
                    "classical": 0.10,
                    "technical": 0.10,
                },
            }
        },
    }


def test_learning_service_returns_contextual_overrides_when_sample_is_enough() -> None:
    service = LearningService(database_service=object(), config=_base_config())
    service.learning_history.append(
        LearningReport(
            report_date=datetime.now(timezone.utc).isoformat(),
            agents_performance={},
            adjusted_weights=service.default_weights.copy(),
            total_trades_analyzed=12,
            overall_win_rate=58.0,
            recommendations=[],
            previous_weights=service.default_weights.copy(),
            changes_summary="",
            contextual_weight_hints={
                "setup_type": {
                    "LIQUIDITY_REVERSAL": {
                        "sample": 4,
                        "recommended_weights": {
                            "technical": 0.55,
                            "classical": 0.10,
                            "smc": 0.08,
                            "price_action": 0.12,
                            "multitimeframe": 0.15,
                        },
                    }
                }
            },
        )
    )

    weights = service.get_contextual_weight_overrides(setup_type="LIQUIDITY_REVERSAL")
    assert weights
    assert weights["technical"] > weights["smc"]


def test_learning_service_ignores_small_contextual_samples() -> None:
    service = LearningService(database_service=object(), config=_base_config())
    service.learning_history.append(
        LearningReport(
            report_date=datetime.now(timezone.utc).isoformat(),
            agents_performance={},
            adjusted_weights=service.default_weights.copy(),
            total_trades_analyzed=2,
            overall_win_rate=50.0,
            recommendations=[],
            previous_weights=service.default_weights.copy(),
            changes_summary="",
            contextual_weight_hints={
                "setup_type": {
                    "LIQUIDITY_REVERSAL": {
                        "sample": 1,
                        "recommended_weights": {"technical": 0.8, "smc": 0.05},
                    }
                }
            },
        )
    )
    assert service.get_contextual_weight_overrides(setup_type="LIQUIDITY_REVERSAL") == {}


def test_decision_agent_blends_contextual_weights_with_strategy_profile() -> None:
    config = _base_config()
    service = LearningService(database_service=object(), config=config)
    service.learning_history.append(
        LearningReport(
            report_date=datetime.now(timezone.utc).isoformat(),
            agents_performance={},
            adjusted_weights=service.default_weights.copy(),
            total_trades_analyzed=20,
            overall_win_rate=61.0,
            recommendations=[],
            previous_weights=service.default_weights.copy(),
            changes_summary="",
            contextual_weight_hints={
                "setup_type": {
                    "LIQUIDITY_REVERSAL": {
                        "sample": 6,
                        "recommended_weights": {
                            "technical": 0.80,
                            "classical": 0.05,
                            "smc": 0.02,
                            "price_action": 0.08,
                            "multitimeframe": 0.05,
                        },
                    }
                }
            },
        )
    )
    agent = DecisionAgent(config, learning_service=service)
    result = agent.analyze(
        {
            "smc": {
                "signal": "SELL",
                "confidence": 84,
                "setup_structure": {"setup_type": "LIQUIDITY_REVERSAL", "lead_agent": "smc"},
            },
            "price_action": {"signal": "SELL", "confidence": 78},
            "technical": {"signal": "WAIT", "confidence": 75, "market_regime": {"volatility_regime": "HIGH"}},
            "classical": {"signal": "WAIT", "confidence": 40},
            "multitimeframe": {"signal": "WAIT", "confidence": 55},
            "session": {"trading_allowed": True, "allow_signals": True, "current_session": "London / Europe Midday"},
            "news": {"can_trade": True, "market_status": "SAFE"},
            "daily_bias": {"bias": "BEARISH", "enabled": True},
            "risk": {"approved": True},
        }
    )
    assert result["strategy_profile"]["name"] == "liquidity_reversal"
    assert result["weights"]["technical"] > 0.10  # contextual learning boosted it above profile default
    assert result["weights"]["smc"] < 0.35       # contextual learning softened the profile's SMC emphasis
