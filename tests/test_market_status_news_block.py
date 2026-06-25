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


def test_market_status_news_block_does_not_show_groq_zero() -> None:
    decision = {
        "decision": "WAIT",
        "confidence": 0,
        "current_price": 4027.06,
        "ai": {"available": False, "confidence": 0},
        "classic": {"strongest_directional": {"agent": "technical", "confidence": 92.0}},
        "warnings": ["News blocked: No trading - Tier 1 FOMC Member Williams Speaks released 22 min ago"],
        "agent_min_confidence": 60,
        "groq_min_confidence": 51,
    }

    msg = _build_market_status_message(decision, _technical_context(), _DB())

    assert "Gate: NEWS BLOCK" in msg
    assert "Groq: skipped/overridden" in msg
    assert "Groq: 0%" not in msg
    assert "Groq returned 0%" not in msg
    assert "News hard block active" in msg
    assert "News blocked:" in msg
    assert "Strongest agent: technical" in msg


def test_market_status_normal_wait_still_shows_groq_confidence() -> None:
    decision = {
        "decision": "WAIT",
        "confidence": 45,
        "current_price": 4027.06,
        "ai": {"available": True, "confidence": 45, "reasoning": "mixed evidence"},
        "classic": {"strongest_directional": {"agent": "technical", "confidence": 68.0}},
        "warnings": [],
        "agent_min_confidence": 60,
        "groq_min_confidence": 51,
    }
    ctx = _technical_context()
    ctx["news"] = {"can_trade": True, "market_status": "SAFE"}

    msg = _build_market_status_message(decision, ctx, _DB())

    assert "Gate: NEWS BLOCK" not in msg
    assert "📊 Groq: 45%" in msg
    assert "Groq returned 45%" in msg
