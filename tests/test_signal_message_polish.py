from services.telegram_bot import TelegramService


def _capture(decision):
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    captured = {}
    service.send_message = lambda text, **_k: captured.setdefault("text", text) or True  # type: ignore[assignment]
    service.send_signal(decision)
    return captured["text"]


def test_signal_message_separates_votes_context_and_attribution():
    text = _capture({
        "decision": "BUY",
        "symbol": "XAU/USD",
        "confidence": 78.3,
        "current_price": 4067.01,
        "quality": {"grade": "A", "score": 83.0},
        "trade_id": "TRADE_POLISH",
        "classic": {"consensus": {"selected": {"support_count": 2, "opposition_count": 0}}},
        "votes": {"BUY": [{"agent": "price_action", "confidence": 72.0}, {"agent": "multitimeframe", "confidence": 83.0}]},
        "agent_details": {
            "technical": {"label": "Technical", "direction": "WAIT", "signals": ["EMA50 above EMA200", "MACD bearish and weakening"]},
            "classical": {"label": "Classical", "direction": "WAIT", "signals": ["Ascending trendline respected"]},
        },
        "entry_attribution": {
            "primary_entry_driver": "multitimeframe",
            "timing_state": "VALID",
            "entry_permission": "ALLOWED",
            "macro_direction": {"bias": "BULLISH_GOLD", "confidence": 64},
        },
        "daily_bias": {"bias": "BULLISH", "confidence": 70},
        "session_info": {"current_session": "Main Trading Session", "session_quality": "HIGH"},
        "news_context": {"rule_based": {"market_status": "CAUTION", "risk_level": "LOW", "can_trade": True}},
        "risk": {"stop_loss": {"distance_points": 300}},
        "signal": {"type": "BUY", "entry": {"price": 4067.01}, "stop_loss": 4037.01, "tp1": 4107.01, "tp2": 4137.01, "rr_ratio": 2.33},
        "reasons": ["Classic consensus", "Trade approved"],
    })

    assert "Strength: Good — 2/5 qualified agents, no opposition" in text
    assert "🟢" in text
    assert "🟡" in text
    assert "Daily bias: BULLISH (70%)" in text
    assert "Macro: Bullish Gold (64%)" in text
    assert "News: CAUTION / LOW — no hard block" in text
    assert "Protection:</b> SL → entry after +200 pts before TP1" in text
