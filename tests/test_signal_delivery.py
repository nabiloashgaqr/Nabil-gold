"""Tests for the scheduled-signal delivery path in scripts/run_analysis.py.

These guard the regression where signals only showed up on manual runs:
a Telegram send failure must NOT result in a saved trade (which would then
poison the duplicate filter and silently block every later scheduled run).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

import scripts.run_analysis as ra


class _FakeTelegram:
    """Minimal Telegram stub recording what was sent."""

    def __init__(self, *, signal_ok: bool = True, raise_on_signal: bool = False):
        self.signal_ok = signal_ok
        self.raise_on_signal = raise_on_signal
        self.signals_sent: List[Dict[str, Any]] = []
        self.errors: List[str] = []

    def send_signal(self, decision: Dict[str, Any]) -> bool:
        if self.raise_on_signal:
            raise RuntimeError("network down")
        self.signals_sent.append(decision)
        return self.signal_ok

    def send_error_alert(self, message: str) -> bool:
        self.errors.append(message)
        return True

    def send_message(self, *a, **k) -> bool:
        return True


def _build_decision() -> Dict[str, Any]:
    return {
        "decision": "SELL",
        "confidence": 74,
        "current_price": 4130.14,
        "signal": {
            "type": "SELL",
            "entry": {"price": 4130.14, "low": 4129.49, "high": 4130.79},
            "stop_loss": 4150.14,
            "tp1": 4103.47,
            "tp2": 4083.47,
        },
    }


def _patch_common(monkeypatch, telegram, database):
    """Patch run_analysis dependencies so we can exercise only the send/save path."""
    config = {
        "risk_settings": {"max_daily_signals": 50, "max_open_trades": 50},
        "duplicate_signal_filter": {"enabled": False},
        "trading_hours": {"enabled": False},
        "trading_mode": "paper",
        "paper_trading": {"enabled": True},
        "operation_mode": "observation",
    }
    monkeypatch.setattr(ra, "load_config", lambda: config)
    monkeypatch.setattr(ra, "TelegramService", lambda *_a, **_k: telegram)
    monkeypatch.setattr(ra, "DatabaseService", lambda *_a, **_k: database)

    # Force trading allowed so we reach the decision/send block.
    fake_session = MagicMock()
    fake_session.check.return_value = {"trading_allowed": True, "current_session": "Test", "session_quality": "HIGH"}
    monkeypatch.setattr(ra, "TradingSessionAgent", lambda *_a, **_k: fake_session)

    # Market data present.
    fake_md = MagicMock()
    fake_md.get_gold_data.return_value = {
        "current_price": 4130.14,
        "source": "finnhub",
        "timeframes": {},
        "data": [],
    }
    monkeypatch.setattr(ra, "MarketDataService", lambda *_a, **_k: fake_md)

    # No AI service init.
    monkeypatch.setattr(ra, "get_learning_service", lambda *_a, **_k: None)

    # Stub the analysis agents (run_agent just returns WAIT-ish dicts).
    monkeypatch.setattr(ra, "run_agent", lambda name, agent, data: {"agent": name, "signal": "WAIT", "confidence": 0})
    for agent_name in (
        "TechnicalAgent", "ClassicalAgent", "SMCAgent", "PriceActionAgent",
        "MultiTimeframeAgent", "NewsRiskAgent", "RiskManagementAgent", "DailyBiasAgent",
    ):
        monkeypatch.setattr(ra, agent_name, lambda *_a, **_k: MagicMock(), raising=False)

    monkeypatch.setattr(ra, "RiskManagementAgent", lambda *_a, **_k: MagicMock(evaluate=lambda r: {}))
    monkeypatch.setattr(ra, "DynamicRiskManager", lambda *_a, **_k: MagicMock(evaluate=lambda db: {}))
    monkeypatch.setattr(ra, "should_block_signal", lambda *_a, **_k: None)

    # DecisionAgent returns our prebuilt SELL decision.
    decision = _build_decision()

    class _FakeDecisionAgent:
        def __init__(self, *_a, **_k):
            pass

        async def decide_async(self, _all_results):
            return dict(decision)

    monkeypatch.setattr(ra, "DecisionAgent", _FakeDecisionAgent)
    return config


def _make_db():
    db = MagicMock()
    db.get_open_trades.return_value = []
    db.get_today_signals_count.return_value = 0
    db.get_consecutive_losses.return_value = 0
    db.get_recent_trades.return_value = []
    db.get_active_memory_rules.return_value = []
    db.save_trade.return_value = "TRADE_TEST_123"
    return db


def test_failed_telegram_delivery_does_not_save_trade(monkeypatch):
    """If Telegram delivery fails, the trade must NOT be persisted."""
    telegram = _FakeTelegram(signal_ok=False)
    database = _make_db()
    _patch_common(monkeypatch, telegram, database)

    asyncio.run(ra.run_analysis_async())

    database.save_trade.assert_not_called()
    assert telegram.errors, "An error alert should be sent when delivery fails"


def test_telegram_exception_does_not_save_trade(monkeypatch):
    """If Telegram raises, the trade must NOT be persisted either."""
    telegram = _FakeTelegram(raise_on_signal=True)
    database = _make_db()
    _patch_common(monkeypatch, telegram, database)

    asyncio.run(ra.run_analysis_async())

    database.save_trade.assert_not_called()
    assert telegram.errors


def test_successful_delivery_saves_trade(monkeypatch):
    """Happy path: signal delivered, then trade saved exactly once."""
    telegram = _FakeTelegram(signal_ok=True)
    database = _make_db()
    _patch_common(monkeypatch, telegram, database)

    asyncio.run(ra.run_analysis_async())

    assert len(telegram.signals_sent) == 1
    database.save_trade.assert_called_once()
