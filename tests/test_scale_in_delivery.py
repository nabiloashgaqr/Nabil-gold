"""Regression tests for fixed-risk scale-in Telegram delivery.

A previous bug built the scale-in Telegram text but never sent it, then checked an
undefined variable named ``delivered``. In production this made the scale-in path
fail silently inside the analysis wrapper.

Updated: scale-in now requires full agent consensus (3 agents, 72% confidence)
and risk filter checks before firing — just like a new signal.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List

from scripts.run_analysis import _check_scale_in


@dataclass
class _AIResponse:
    success: bool = True
    content: str = '{"scale_in": true, "confidence": 80, "reason": "near resistance retest"}'


class _FakeAI:
    async def _call_ai(self, prompt: str, agent_type: str) -> _AIResponse:
        return _AIResponse()


class _FakeDB:
    def __init__(self) -> None:
        self.saved: List[Dict[str, Any]] = []

    def new_trade_id(self) -> str:
        return "TRADE_TEST_SCALE_IN"

    def save_trade(self, decision: Dict[str, Any]) -> str:
        self.saved.append(decision)
        return str(decision["trade_id"])


class _FakeTelegram:
    def __init__(self) -> None:
        self.messages: List[str] = []

    def send_message(self, text: str, urgent: bool = False) -> bool:
        self.messages.append(text)
        return True


# Minimal config with all required sections for scale-in consensus check
_SCALE_IN_CONFIG = {
    "order_execution": {
        "entry_style": "fixed_risk",
        "fixed_risk": {
            "scale_in_enabled": True,
            "scale_in_trigger_points": 50,
            "scale_in_max": 1,
            "scale_in_size_ratio": 0.5,
        },
    },
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

# All 5 agents agree on SELL → passes consensus check
_SELL_CONSENSUS_RESULTS = {
    "current_price": 4000.0,
    "news": {"can_trade": True, "market_status": "SAFE"},
    "news_ai": {"available": False},
    "classical": {"resistance_levels": [4005.0]},
    "technical": {"signal": "SELL", "confidence": 85},
    "classical_signal": {"signal": "SELL", "confidence": 80},
    "smc": {"signal": "SELL", "confidence": 75},
    "price_action": {"signal": "SELL", "confidence": 82},
    "multitimeframe": {"signal": "SELL", "confidence": 78},
    "risk": {
        "approved": True,
        "checks": {
            "max_open_trades_filter": True,
            "max_daily_signals_filter": True,
            "atr_filter": True,
            "spread_filter": True,
            "consecutive_losses_filter": True,
        },
    },
}

_OPEN_TRADE_SELL = [
    {
        "id": "TRADE_PARENT",
        "type": "SELL",
        "entry_price": 3989.7,
        "stop_loss": 4009.7,
        "tp1": 3963.03,
        "tp2": 3943.03,
    }
]


def test_scale_in_sends_message_then_saves_trade() -> None:
    db = _FakeDB()
    tg = _FakeTelegram()

    asyncio.run(_check_scale_in(_SCALE_IN_CONFIG, _SELL_CONSENSUS_RESULTS, _OPEN_TRADE_SELL, db, tg))

    assert len(tg.messages) == 1
    assert "Scale-In" in tg.messages[0]
    assert "TRADE_PARENT" in tg.messages[0]
    assert len(db.saved) == 1
    assert db.saved[0]["trade_id"] == "TRADE_TEST_SCALE_IN"
    assert db.saved[0]["signal"]["scale_in"] is True


def test_scale_in_not_saved_if_telegram_delivery_fails() -> None:
    class _FailingTelegram(_FakeTelegram):
        def send_message(self, text: str, urgent: bool = False) -> bool:
            self.messages.append(text)
            return False

    db = _FakeDB()
    tg = _FailingTelegram()

    asyncio.run(_check_scale_in(_SCALE_IN_CONFIG, _SELL_CONSENSUS_RESULTS, _OPEN_TRADE_SELL, db, tg))

    assert len(tg.messages) == 1
    assert db.saved == []


def test_scale_in_blocked_when_insufficient_agents() -> None:
    """Scale-in requires 3+ agents agreeing — 2 is not enough."""
    weak_results = dict(_SELL_CONSENSUS_RESULTS)
    # Only 2 agents agree
    weak_results["technical"] = {"signal": "SELL", "confidence": 85}
    weak_results["smc"] = {"signal": "SELL", "confidence": 75}
    weak_results["price_action"] = {"signal": "BUY", "confidence": 80}  # oppose
    weak_results["multitimeframe"] = {"signal": "WAIT", "confidence": 40}  # not qualified

    db = _FakeDB()
    tg = _FakeTelegram()

    asyncio.run(_check_scale_in(_SCALE_IN_CONFIG, weak_results, _OPEN_TRADE_SELL, db, tg))

    assert len(tg.messages) == 0
    assert len(db.saved) == 0


def test_scale_in_blocked_when_risk_filter_fails() -> None:
    """Scale-in respects risk filters (max_open_trades, etc.)."""
    risk_failed = dict(_SELL_CONSENSUS_RESULTS)
    risk_failed["risk"] = {
        "approved": False,
        "checks": {
            "max_open_trades_filter": False,
            "max_daily_signals_filter": True,
            "atr_filter": True,
            "spread_filter": True,
            "consecutive_losses_filter": True,
        },
    }

    db = _FakeDB()
    tg = _FakeTelegram()

    asyncio.run(_check_scale_in(_SCALE_IN_CONFIG, risk_failed, _OPEN_TRADE_SELL, db, tg))

    assert len(tg.messages) == 0
    assert len(db.saved) == 0
