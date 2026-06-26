"""Market Status formatting when news hard-blocks trading."""

from __future__ import annotations

from scripts.run_analysis import _build_market_status_message


class _DB:
    def get_open_trades(self):
        return []


def _technical_context() -> dict:
    return {
        "current_price": 4027.06,
        "technical": {
            "technical": {
                "rsi": 50.33,
                "key_levels": {"nearest_support": 4022.85, "nearest_resistance": 4034.3},
            }
        },
        "news": {"can_trade": False, "market_status": "HIGH_VOLATILITY"},
    }


def test_market_status_news_block_does_not_show_zero_confidence_gate() -> None:
    decision = {
        "decision": "WAIT",
        "confidence": 0,
        "current_price": 4027.06,
        "classic": {"strongest_directional": {"agent": "technical", "confidence": 92.0}},
        "warnings": ["News blocked: No trading - Tier 1 FOMC Member Williams Speaks released 22 min ago"],
        "agent_min_confidence": 60,
    }

    msg = _build_market_status_message(decision, _technical_context(), _DB())

    assert "Gate: NEWS BLOCK" in msg
    assert "Consensus overridden" in msg
    assert "Groq" not in msg
    assert "returned 0%" not in msg
    assert "News hard block active" in msg
    assert "News blocked:" in msg
    assert "Strongest agent: technical" in msg


def test_market_status_normal_wait_shows_consensus_rules() -> None:
    decision = {
        "decision": "WAIT",
        "confidence": 45,
        "current_price": 4027.06,
        "classic": {
            "strongest_directional": {"agent": "technical", "confidence": 68.0},
            "rejection_reason": "Need at least 2 agreeing agents with weighted confidence >= 65%",
            "consensus": {
                "rules": {
                    "agent_min_confidence": 60,
                    "min_consensus_confidence": 65,
                    "strong_single_agent_confidence": 70,
                }
            },
        },
        "warnings": [],
    }
    ctx = _technical_context()
    ctx["news"] = {"can_trade": True, "market_status": "SAFE"}

    msg = _build_market_status_message(decision, ctx, _DB())

    assert "Gate: NEWS BLOCK" not in msg
    assert "Consensus: WAIT" in msg
    assert "Entry ≥65%" in msg
    assert "at least 2 agents with weighted confidence ≥65%" in msg
