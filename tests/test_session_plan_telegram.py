from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.telegram_bot import TelegramService


def test_send_session_plan_formats_manual_style_message(monkeypatch) -> None:
    sent = {}

    def _capture(text: str, urgent: bool = False, chat_id=None):
        sent["text"] = text
        sent["urgent"] = urgent
        return True

    service = TelegramService({"telegram": {}})
    monkeypatch.setattr(service, "send_message", _capture)

    plan = {
        "symbol": "XAU/USD",
        "session_bias": "SELL",
        "session_label": "London / Europe Midday",
        "session_quality": "HIGH",
        "planner_confidence": 84.5,
        "planner_grade": "A",
        "authority_state": "CONFIRMED",
        "authority_reason": "SELL alignment from daily_bias, macro, structure",
        "scenario_type": "LIQUIDITY_REVERSAL",
        "primary_entry_zone": {"low": 4020.0, "high": 4028.0},
        "standby_entry_zone": {"low": 4030.0, "high": 4038.0},
        "primary_entry_price": 4022.0,
        "standby_entry_price": 4032.0,
        "invalidation_level": 4045.0,
        "target_liquidity": 3965.0,
        "poi_classification": "EXTREME_POI",
        "execution_preference": "SPLIT_EXECUTION_WATCH",
        "expected_path": "Sweep highs then reject premium and deliver lower.",
        "plan_narrative": "Sell day map from premium into lower liquidity.",
        "primary_rationale": ["classified as EXTREME_POI", "liquidity sweep supports the path"],
        "standby_rationale": ["revisit window NEAR"],
        "agent_opinions": [
            {"label": "Technical", "direction": "SELL", "confidence": 88, "summary": "Momentum rolled over from premium."},
            {"label": "SMC", "direction": "SELL", "confidence": 91, "signals": ["Buy-side sweep detected at highs."]},
            {"label": "Macro / Fundamental", "direction": "SELL", "confidence": 64, "summary": "Dollar and yields support bearish gold."},
        ],
        "gemini_plan_review": {"available": True, "market_bias": "SELL", "reason": "Premium rejection day map."},
        "gemini_macro_review": {"available": True, "macro_verdict": "BEARISH_GOLD", "confidence": 67, "reason": "Higher yields pressure gold."},
        "gemini_news_review": {"available": True, "risk_level": "LOW", "trading_advice": "No major blocker to the bearish map."},
        "delivery_context": {"message_kind": "OPENING_PLAN", "delivery_reason": "first_ready_plan_this_session"},
        "plan_ready": True,
        "plan_status": "READY",
    }

    assert service.send_session_plan(plan) is True
    text = sent["text"]
    assert sent["urgent"] is True
    assert "SESSION OPENING PLAN" in text
    assert "DAY MAP" in text
    assert "PRIMARY SELL AREA" in text
    assert "SECONDARY SELL AREA" in text
    assert "INVALIDATION" in text
    assert "TARGETS" in text
    assert "TP1" in text
    assert "TP2" in text
    assert "EXECUTION PLAN" in text
    assert "THESIS" in text
    assert "AGENT READS" in text
    assert "Technical" in text
    assert "SMC" in text
    assert "Macro / Fundamental" in text
    assert "AI REVIEW" in text
    assert "Why now" in text
    assert "first ready plan this session" in text
    assert "Gemini" in text
    assert "Macro" in text
    assert "4020.00" in text
    assert "4045.00" in text
    assert "3965.00" in text
