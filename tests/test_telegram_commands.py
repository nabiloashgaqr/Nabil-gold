"""Tests for the interactive Telegram command handler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import services.telegram_commands as tc


def _db():
    db = MagicMock()
    db.get_recent_trades.return_value = [
        {"id": "TRADE_x_5a620d77", "type": "SELL", "status": "PENDING",
         "entry_price": 4101.05, "confidence": 92, "current_pnl": 0}
    ]
    db.get_open_trades.return_value = [
        {"id": "TRADE_a_7cf3", "type": "SELL", "status": "TP1_HIT", "entry_price": 4101.05, "current_pnl": 270},
        {"id": "TRADE_b_9f0a", "type": "BUY", "status": "PENDING", "entry_price": 4000.0},
    ]
    db.get_today_trades.return_value = [
        {"type": "SELL", "status": "TP2_HIT", "final_pnl": 570},
        {"type": "BUY", "status": "SL_HIT", "final_pnl": -200},
    ]
    db.get_active_memory_rules.return_value = [
        {"category": "RISK", "applies_to": "BOTH", "rule_text": "Widen the stop in high volatility."}
    ]
    return db


def setup_function(_):
    tc._current_price = lambda c: 4077.56  # avoid network


def test_help():
    out = tc.handle_command("/help", _db(), {})
    assert "Available commands" in out and "/status" in out and "/open" in out


def test_status_has_price_and_latest():
    out = tc.handle_command("/status", _db(), {})
    assert "4,077.56" in out and "SELL" in out and "PENDING" in out


def test_open_separates_live_and_pending():
    out = tc.handle_command("/open", _db(), {})
    assert "TP1_HIT" in out
    assert "Pending" in out and "waiting for touch" in out
    assert "Floating Net" in out
    assert "+270 pts" in out


def test_today_net_correct():
    out = tc.handle_command("/today", _db(), {})
    # 570 - 200 = +370
    assert "+370 pts" in out
    assert "Win rate: 50.0%" in out


def test_stats_builds():
    out = tc.handle_command("/stats", _db(), {})
    assert "Overall Performance" in out


def test_rules():
    out = tc.handle_command("/rules", _db(), {})
    assert "RISK" in out and "Widen the stop" in out


def test_unknown_command_ignored():
    assert tc.handle_command("/nope", _db(), {}) is None
    assert tc.handle_command("just text", _db(), {}) is None


def test_command_with_botname_suffix():
    assert tc.handle_command("/status@MyGoldBot", _db(), {}) is not None


def test_poll_offset_and_dedup(tmp_path):
    tc._OFFSET_FILE = tmp_path / "off.json"
    tg = MagicMock()
    tg.get_updates.return_value = [
        {"update_id": 101, "message": {"message_id": 1, "chat": {"id": 5}, "text": "/status"}},
        {"update_id": 102, "message": {"message_id": 2, "chat": {"id": 5}, "text": "not a cmd"}},
        {"update_id": 103, "message": {"message_id": 3, "chat": {"id": 5}, "text": "/open"}},
    ]
    db = _db()
    handled = tc.poll_and_handle(tg, db, {})
    assert handled == 2
    assert tg.reply.call_count == 2
    assert tc._load_offset() == 104

    # second poll: no new updates, nothing handled, offset passed through
    tg2 = MagicMock()
    tg2.get_updates.return_value = []
    assert tc.poll_and_handle(tg2, db, {}) == 0
    tg2.get_updates.assert_called_once_with(offset=104, timeout=0)


def test_poll_ignores_non_command_chatless(tmp_path):
    tc._OFFSET_FILE = tmp_path / "off2.json"
    tg = MagicMock()
    tg.get_updates.return_value = [
        {"update_id": 201, "message": {"message_id": 1, "text": "/status"}},  # no chat id
    ]
    assert tc.poll_and_handle(tg, _db(), {}) == 0
    tg.reply.assert_not_called()
