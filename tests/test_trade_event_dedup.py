"""Tests that multiple trade-management events fired in the same evaluation
cycle are delivered as a SINGLE combined Telegram message, not one per event.

Regression: a SELL trade that triggered LONG_RUNNING + EXIT_WARNING together
produced two near-identical messages at the same time.
"""

from __future__ import annotations

from typing import Any, Dict, List

from agents.open_trades_manager import OpenTradesManager
from services.telegram_bot import TelegramService


class _CapturingTelegram(TelegramService):
    def __init__(self):
        super().__init__({"telegram": {"bot_token": None, "chat_id": None}})
        self.messages: List[str] = []

    def send_message(self, text: str, chat_id: str | None = None, urgent: bool = False) -> bool:  # type: ignore[override]
        self.messages.append(text)
        return True


def _trade() -> Dict[str, Any]:
    return {
        "id": "TRADE_TEST_DEDUP", "type": "SELL", "entry_price": 4124.82,
        "stop_loss": 4144.82, "tp1": 4098.15, "tp2": 4078.15,
    }


def test_two_events_send_single_message():
    tg = _CapturingTelegram()
    tg.send_trade_events(
        _trade(), ["LONG_RUNNING", "EXIT_WARNING"], 4136.12, -113.0,
        {"old_status": "OPEN", "new_status": "OPEN", "progress_to_tp1": 0.0, "hours_open": 4.4},
    )
    assert len(tg.messages) == 1
    msg = tg.messages[0]
    # Both event notes appear in the one message.
    assert "Exit/risk warning" in msg
    assert "open for a long time" in msg
    # Highest-priority event leads the title.
    assert "Exit / Risk Warning" in msg.split("\n")[0]


def test_single_event_still_one_message():
    tg = _CapturingTelegram()
    tg.send_trade_events(_trade(), ["LONG_RUNNING"], 4136.12, -113.0, {"old_status": "OPEN", "new_status": "OPEN"})
    assert len(tg.messages) == 1
    assert "Long-running Trade" in tg.messages[0]


def test_no_events_sends_nothing():
    tg = _CapturingTelegram()
    assert tg.send_trade_events(_trade(), [], 4136.12, -113.0, {}) is False
    assert tg.messages == []


def test_status_change_event_leads_title():
    tg = _CapturingTelegram()
    # TP1_HIT must outrank MOVE_SL_TO_BE in the title.
    tg.send_trade_events(_trade(), ["MOVE_SL_TO_BE", "TP1_HIT"], 4098.0, 268.0,
                         {"old_status": "OPEN", "new_status": "TP1_HIT"})
    assert len(tg.messages) == 1
    assert "Take Profit 1 Hit" in tg.messages[0].split("\n")[0]


def test_manager_sends_one_message_for_multi_event_trade():
    """End-to-end through OpenTradesManager.update_trades."""
    tg = _CapturingTelegram()
    # Config: long-running after 4h, expire after 8h -> at 4.4h open we get
    # LONG_RUNNING (and EXIT_WARNING if adverse). Use a deep adverse SELL.
    config = {
        "trade_management": {"time_warning_hours": 4, "expire_after_hours": 8,
                             "near_tp1_progress": 0.8, "auto_move_sl_to_entry_after_tp1": True},
        "trailing_stop": {"enabled": False},
    }
    mgr = OpenTradesManager(config)
    from datetime import datetime, timezone, timedelta
    opened = (datetime.now(timezone.utc) - timedelta(hours=4.4)).isoformat()
    trade = {
        "id": "TRADE_TEST_E2E", "type": "SELL", "status": "OPEN",
        "entry_price": 4124.82, "stop_loss": 4144.82, "tp1": 4098.15, "tp2": 4078.15,
        "entry_time": opened, "created_at": opened, "updates_sent": [],
    }
    # Adverse move (price up on a SELL) to also trigger EXIT_WARNING.
    mgr.update_trades([trade], current_price=4140.0, telegram=tg, now=datetime.now(timezone.utc))
    # At most ONE message even though multiple informational events fired.
    assert len(tg.messages) <= 1
