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


def test_manager_suppresses_informational_only_events():
    """End-to-end through OpenTradesManager.update_trades.

    LONG_RUNNING / EXIT_WARNING are useful internal markers, but they are not
    material trade-state changes. Production Telegram messages should be sent
    only for real changes such as SL moved, trailing moved, TP, SL, BE, or fill.
    """
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
    evaluations = mgr.update_trades([trade], current_price=4140.0, telegram=tg, now=datetime.now(timezone.utc))
    assert set(evaluations[0]["events"]) == {"LONG_RUNNING", "EXIT_WARNING"}
    assert evaluations[0]["notification_events"] == []
    assert tg.messages == []


def test_manager_sends_material_state_change_event():
    """A real state change (TP1 + BE) must still send a Telegram message."""
    tg = _CapturingTelegram()
    mgr = OpenTradesManager(
        {
            "trade_management": {"auto_move_sl_to_entry_after_tp1": True, "expire_after_hours": 0},
            "trailing_stop": {"enabled": False},
        }
    )
    from datetime import datetime, timezone
    trade = {
        "id": "TRADE_TEST_TP1", "type": "BUY", "status": "OPEN",
        "entry_price": 4000.0, "stop_loss": 3980.0, "tp1": 4010.0, "tp2": 4020.0,
        "entry_time": datetime.now(timezone.utc).isoformat(), "updates_sent": [],
    }
    evaluations = mgr.update_trades([trade], current_price=4010.0, telegram=tg, now=datetime.now(timezone.utc))
    assert evaluations[0]["notification_events"] == ["TP1_HIT", "MOVE_SL_TO_BE"]
    assert len(tg.messages) == 1
    assert "Take Profit 1 Hit" in tg.messages[0]


# ── Status line dedup (no "A → A") ─────────────────────────────────────────
def test_status_no_arrow_when_unchanged():
    tg = _CapturingTelegram()
    tg.send_trade_event(_trade(), "TRAILING_SL_UPDATED", 4080.8, 202.5,
                        {"old_status": "TP1_HIT", "new_status": "TP1_HIT"})
    msg = tg.messages[0]
    assert "TP1_HIT → TP1_HIT" not in msg
    assert "Status:</b> TP1_HIT" in msg or "Status: TP1_HIT" in msg


def test_trailing_message_caps_tp1_progress_and_shows_locked_profit():
    tg = _CapturingTelegram()
    trade = {"id": "TBUY", "type": "BUY", "entry_price": 4000.0, "stop_loss": 3980.0, "tp1": 4010.0, "tp2": 4020.0}
    tg.send_trade_event(
        trade,
        "TRAILING_SL_UPDATED",
        4013.0,
        130.0,
        {"old_status": "OPEN", "new_status": "OPEN", "progress_to_tp1": 1.3, "updates": {"stop_loss": 4003.0}},
    )
    msg = tg.messages[0]
    assert "130%" not in msg
    assert "TP1 Progress:</b> completed" in msg
    assert "locking about +30 pts" in msg
    assert "100-point gap / 30-point step" in msg


def test_status_shows_arrow_when_changed():
    tg = _CapturingTelegram()
    tg.send_trade_event(_trade(), "TP1_HIT", 4074.0, 268.0,
                        {"old_status": "OPEN", "new_status": "TP1_HIT"})
    assert "OPEN → TP1_HIT" in tg.messages[0]


def test_closing_event_shows_actual_pnl_and_exit_price_not_floating_pnl():
    tg = _CapturingTelegram()
    trade = {
        "id": "TSELL", "type": "SELL", "entry_price": 4002.03,
        "stop_loss": 3971.15, "tp1": 3962.03, "tp2": 3932.03,
    }
    tg.send_trade_event(
        trade,
        "TRAILING_SL_HIT",
        current_price=3967.01,
        pnl_points=350.2,  # floating at current quote; must NOT be displayed as result
        evaluation={
            "old_status": "TP1_HIT",
            "new_status": "SL_HIT",
            "updates": {"close_price": 3953.37, "stop_loss": 3953.37, "final_pnl": 486.6},
        },
    )
    msg = tg.messages[0]
    assert "Current Price:</b> 3967.01" in msg
    assert "Exit Price:</b> 3953.37" in msg
    assert "Actual PnL:</b> +486.6 pts" in msg
    assert "Current PnL:</b> +350.2 pts" not in msg


def test_non_closing_event_keeps_current_pnl_label():
    tg = _CapturingTelegram()
    tg.send_trade_event(
        _trade(),
        "TRAILING_SL_UPDATED",
        current_price=4080.8,
        pnl_points=440.2,
        evaluation={"old_status": "OPEN", "new_status": "OPEN", "updates": {"stop_loss": 4090.8}},
    )
    msg = tg.messages[0]
    assert "Current Price:</b> 4080.80" in msg
    assert "Current PnL:</b> +440.2 pts" in msg
    assert "Actual PnL" not in msg
    assert "Exit Price" not in msg
