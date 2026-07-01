"""Phase 4 verification tests for Gemini observability and display guardrails."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import MagicMock

from services.llm_review import GeminiReviewService
from services.telegram_bot import TelegramService


class _Resp:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Dict[str, Any]:
        return self._payload


class _FakeSession:
    def __init__(self, body: Dict[str, Any], status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code
        self.calls: List[Dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _Resp:
        self.calls.append({"url": url, **kwargs})
        text = json.dumps(self.body)
        return _Resp({"candidates": [{"content": {"parts": [{"text": text}]}}]}, self.status_code)


def _gemini_with_json(body: Dict[str, Any]) -> GeminiReviewService:
    svc = GeminiReviewService({})
    svc.api_key = "test-key"
    svc.enabled = True
    svc.session = _FakeSession(body)  # type: ignore[assignment]
    return svc


def test_gemini_guardrail_suppresses_insufficient_daily_output() -> None:
    svc = _gemini_with_json(
        {
            "verdict": "NEUTRAL",
            "summary": "Insufficient data to provide meaningful analysis.",
            "key_points": [],
        }
    )

    result = svc.summarize_daily_report({"report_date": "2026-07-01", "stats": {}})

    assert result["available"] is False
    assert result["suppressed"] is True
    assert result["suppress_reason"] == "generic_or_insufficient_output"
    assert result["kind"] == "daily"


def test_gemini_news_useful_output_remains_available() -> None:
    svc = _gemini_with_json(
        {
            "risk_level": "HIGH",
            "summary_bullets": ["FOMC speaker within the trading window can widen spreads."],
            "trading_advice": "Reduce size and avoid fresh entries around the event window.",
        }
    )

    result = svc.interpret_news_context({"symbol": "XAU/USD", "news": {"market_status": "CAUTION"}})

    assert result["available"] is True
    assert result["suppressed"] is False
    assert result["quality"] == "ok"
    assert result["kind"] == "news"


def test_buy_sell_signal_renders_gemini_news_check() -> None:
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    captured: Dict[str, str] = {}

    def _fake_send(text: str, urgent: bool = False, **_kwargs: Any) -> bool:
        captured["text"] = text
        return True

    service.send_message = _fake_send  # type: ignore[assignment]
    ok = service.send_signal(
        {
            "decision": "SELL",
            "symbol": "XAU/USD",
            "confidence": 81,
            "current_price": 4000.0,
            "trade_id": "TRADE_GEMINI_NEWS",
            "signal": {"entry": {"price": 4000.0}, "stop_loss": 4020.0, "tp1": 3970.0, "tp2": 3950.0},
            "gemini_review": {"available": True, "verdict": "SELL", "reason": "Momentum remains bearish."},
            "gemini_news_review": {
                "available": True,
                "risk_level": "HIGH",
                "summary_bullets": ["US macro event is close to the setup."],
                "trading_advice": "Use caution and avoid chasing after the spike.",
            },
        }
    )

    assert ok is True
    text = captured["text"]
    assert "GEMINI INDEPENDENT REVIEW" in text
    assert "GEMINI NEWS CHECK" in text
    assert "Risk:</b> HIGH" in text
    assert "US macro event" in text


def test_weekly_gemini_review_is_persisted_after_append(monkeypatch) -> None:
    import scripts.run_weekly_report as rw

    saved_payloads: List[Dict[str, Any]] = []
    sent_messages: List[str] = []

    class _FakeWeeklyService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def generate_report(self) -> Dict[str, Any]:
            return {
                "status": "ok",
                "stats": {"week": "2026-06-29 → 2026-07-05"},
                "report_text": "BASE WEEKLY REPORT",
                "recommendations": [],
            }

        def _save(self, payload: Dict[str, Any]) -> None:
            saved_payloads.append(dict(payload))

        def save_to_database(self, payload: Dict[str, Any]) -> None:
            saved_payloads.append(dict(payload))

        def send_to_telegram(self, report_text: str) -> bool:
            sent_messages.append(report_text)
            return True

    class _FakeGemini:
        enabled = True

        def summarize_weekly_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "available": True,
                "quality": "ok",
                "edge_efficiency": "Positive but concentrated",
                "market_regime": "Trend continuation",
                "strategic_points": ["Keep trailing-stop winners open when protected."],
                "strategic_pivot": "Prioritize confirmed trend continuation setups.",
            }

    monkeypatch.setattr(rw, "load_config", lambda: {"weekly_report": {"enabled": True, "send_telegram": True}})
    monkeypatch.setattr(rw, "_should_run_now", lambda _config: True)
    monkeypatch.setattr(rw, "TelegramService", lambda *_a, **_k: object())
    monkeypatch.setattr(rw, "DatabaseService", lambda *_a, **_k: object())
    monkeypatch.setattr(rw, "WeeklyReportService", _FakeWeeklyService)
    monkeypatch.setattr(rw, "get_gemini_review_service", lambda *_a, **_k: _FakeGemini())

    exit_code = asyncio.run(rw.main_async())

    assert exit_code == 0
    assert sent_messages and "Gemini Independent Weekly Strategic Review" in sent_messages[0]
    assert any("Gemini Independent Weekly Strategic Review" in p.get("report_text", "") for p in saved_payloads)
    assert any(p.get("gemini_weekly_review", {}).get("available") is True for p in saved_payloads)


def test_normal_wait_does_not_call_gemini(monkeypatch) -> None:
    import scripts.run_analysis as ra

    config = {
        "risk_settings": {"max_daily_signals": 50, "max_open_trades": 50},
        "duplicate_signal_filter": {"enabled": False},
        "trading_hours": {"enabled": False},
        "notifications": {"send_no_signal_updates": False, "hourly_status": False},
    }
    telegram = MagicMock()
    database = MagicMock()
    database.get_open_trades.return_value = []
    database.get_recent_trades.return_value = []

    monkeypatch.setattr(ra, "load_config", lambda: config)
    monkeypatch.setattr(ra, "TelegramService", lambda *_a, **_k: telegram)
    monkeypatch.setattr(ra, "DatabaseService", lambda *_a, **_k: database)
    monkeypatch.setattr(ra, "TradingSessionAgent", lambda *_a, **_k: MagicMock(check=lambda: {"trading_allowed": True}))
    monkeypatch.setattr(ra, "MarketDataService", lambda *_a, **_k: MagicMock(get_gold_data=lambda: {"current_price": 4000.0, "timeframes": {}, "data": []}))
    monkeypatch.setattr(ra, "run_agent", lambda name, agent, data: {"agent": name, "signal": "WAIT", "confidence": 0})
    monkeypatch.setattr(ra, "RiskManagementAgent", lambda *_a, **_k: MagicMock(evaluate=lambda r: {}))
    monkeypatch.setattr(ra, "DynamicRiskManager", lambda *_a, **_k: MagicMock(evaluate=lambda db: {}))
    monkeypatch.setattr(ra, "get_learning_service", lambda *_a, **_k: None)

    class _WaitDecisionAgent:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        async def decide_async(self, _all_results: Dict[str, Any]) -> Dict[str, Any]:
            return {"decision": "WAIT", "confidence": 0, "warnings": []}

    class _GeminiShouldNotRun:
        enabled = True

        def analyze_market_context(self, *_a: Any, **_k: Any) -> Dict[str, Any]:
            raise AssertionError("Gemini market context should not run on normal WAIT")

        def interpret_news_context(self, *_a: Any, **_k: Any) -> Dict[str, Any]:
            raise AssertionError("Gemini news should not run on normal WAIT")

    monkeypatch.setattr(ra, "DecisionAgent", _WaitDecisionAgent)
    monkeypatch.setattr(ra, "get_gemini_review_service", lambda *_a, **_k: _GeminiShouldNotRun())

    asyncio.run(ra.run_analysis_async())

    telegram.send_message.assert_not_called()
    telegram.send_signal.assert_not_called()
