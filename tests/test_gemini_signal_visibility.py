from services.telegram_bot import TelegramService


def _capture(decision):
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    captured = {}
    service.send_message = lambda text, **_k: captured.setdefault("text", text) or True  # type: ignore[assignment]
    service.send_signal(decision)
    return captured["text"]


def _decision(**extra):
    base = {
        "decision": "BUY",
        "symbol": "XAU/USD",
        "confidence": 75,
        "current_price": 4000,
        "trade_id": "TRADE_GEMINI_VISIBILITY",
        "signal": {"type": "BUY", "entry": {"price": 4000}, "stop_loss": 3980, "tp1": 4030, "tp2": 4060, "rr_ratio": 3},
        "reasons": ["Trade approved"],
    }
    base.update(extra)
    return base


def test_signal_always_shows_independent_review_when_missing():
    text = _capture(_decision())
    assert "GEMINI INDEPENDENT REVIEW" in text
    assert "Offline this run" in text


def test_signal_shows_suppressed_gemini_as_skipped_not_hidden():
    text = _capture(_decision(gemini_review={"available": False, "suppressed": True, "suppress_reason": "generic_or_insufficient_output"}))
    assert "GEMINI INDEPENDENT REVIEW" in text
    assert "Skipped — no useful extra insight" in text


def test_signal_shows_unavailable_gemini_without_secret_details():
    text = _capture(_decision(gemini_review={"available": False, "summary": "API key not configured"}))
    assert "GEMINI INDEPENDENT REVIEW" in text
    assert "Offline this run" in text
    assert "API key" not in text
    assert "not configured" not in text
