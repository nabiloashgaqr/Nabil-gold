from __future__ import annotations

from services.database import DatabaseService
from services.telegram_bot import TelegramService


def test_save_setup_candidate_local_fallback(tmp_path) -> None:
    db = DatabaseService({"database": {"url": None, "key": None}})
    db.setup_candidates_path = tmp_path / "setup_candidates.json"

    candidate = {
        "id": "SMC::XAU/USD::15m::SELL::2026-07-15T10:00:00Z::LIQUIDITY_REVERSAL",
        "symbol": "XAU/USD",
        "timeframe": "15m",
        "direction": "SELL",
        "setup_type": "LIQUIDITY_REVERSAL",
        "setup_state": "ENTRY_ARMED",
        "lead_agent": "smc",
        "setup_quality": {"grade": "A", "score": 86.0},
        "poi_type": "order_block",
        "poi_zone": {"top": 4066.2, "bottom": 4063.4},
        "entry_price": 4065.1,
        "stop_loss": 4073.7,
        "target_price": 4057.0,
        "sweep_side": "buy_side",
        "displacement_score": 24.5,
        "confidence": 82,
        "details": {"market_trend": "BEARISH"},
    }

    saved_id = db.save_setup_candidate(candidate)
    rows = db.get_recent_setup_candidates(limit=5, symbol="XAU/USD")

    assert saved_id == candidate["id"]
    assert rows
    assert rows[0]["setup_type"] == "LIQUIDITY_REVERSAL"
    assert rows[0]["lead_agent"] == "smc"
    assert rows[0]["setup_quality"] == "A"
    assert rows[0]["poi_type"] == "order_block"
    assert rows[0]["sweep_side"] == "buy_side"


def test_signal_message_shows_setup_context() -> None:
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    captured: dict[str, str] = {}

    def _fake_send(text: str, urgent: bool = False, **_k) -> bool:
        captured["text"] = text
        return True

    service.send_message = _fake_send  # type: ignore[assignment]
    decision = {
        "decision": "SELL",
        "symbol": "XAU/USD",
        "confidence": 81,
        "current_price": 4065.10,
        "quality": {"grade": "A", "score": 88},
        "signal": {
            "type": "SELL",
            "entry": {"price": 4065.10, "low": 4063.40, "high": 4066.20},
            "stop_loss": 4073.70,
            "tp1": 4057.00,
            "tp2": 4021.40,
            "rr_ratio": 3.2,
        },
        "setup_context": {
            "setup_type": "LIQUIDITY_REVERSAL",
            "setup_state": "ENTRY_ARMED",
            "lead_agent": "smc",
            "quality_grade": "A",
            "poi_type": "order_block",
            "sweep_side": "buy_side",
            "displacement_score": 24.5,
            "target_liquidity": 4057.0,
            "entry_reason": "Sell after liquidity sweep / bearish structure from Premium or Order Block",
        },
        "daily_bias": {"bias": "BEARISH", "confidence": 90},
        "trade_id": "TRADE_SETUP_FMT",
    }
    service.send_signal(decision)
    text = captured["text"]
    assert "Setup:" in text
    assert "Entry zone:" in text
    assert "Target liquidity:" in text
    assert "SMC context:" in text
