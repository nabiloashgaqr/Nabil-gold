"""Regression tests for fixed-risk scale-in Telegram delivery.

A previous bug built the scale-in Telegram text but never sent it, then checked an
undefined variable named ``delivered``. In production this made the scale-in path
fail silently inside the analysis wrapper.
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


def test_scale_in_sends_message_then_saves_trade() -> None:
    config = {
        "order_execution": {
            "entry_style": "fixed_risk",
            "fixed_risk": {
                "scale_in_enabled": True,
                "scale_in_trigger_points": 50,
                "scale_in_max": 1,
                "scale_in_size_ratio": 1.0,
            },
        }
    }
    all_results = {
        "current_price": 4000.0,
        "news": {"can_trade": True, "market_status": "SAFE"},
        "news_ai": {"available": False},
        "classical": {"resistance_levels": [4005.0]},  # 50 points above price
    }
    open_trades = [
        {
            "id": "TRADE_PARENT",
            "type": "SELL",
            "entry_price": 3989.7,
            "stop_loss": 4009.7,
            "tp1": 3963.03,
            "tp2": 3943.03,
        }
    ]
    db = _FakeDB()
    tg = _FakeTelegram()

    asyncio.run(_check_scale_in(config, all_results, open_trades, db, tg))

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

    config = {
        "order_execution": {
            "entry_style": "fixed_risk",
            "fixed_risk": {"scale_in_enabled": True, "scale_in_trigger_points": 50, "scale_in_max": 1},
        }
    }
    all_results = {
        "current_price": 4000.0,
        "news": {"can_trade": True, "market_status": "SAFE"},
        "news_ai": {"available": False},
        "classical": {"resistance_levels": [4005.0]},
    }
    open_trades = [
        {"id": "TRADE_PARENT", "type": "SELL", "entry_price": 3989.7, "stop_loss": 4009.7, "tp1": 3963.03, "tp2": 3943.03}
    ]
    db = _FakeDB()
    tg = _FailingTelegram()

    asyncio.run(_check_scale_in(config, all_results, open_trades, db, tg))

    assert len(tg.messages) == 1
    assert db.saved == []
