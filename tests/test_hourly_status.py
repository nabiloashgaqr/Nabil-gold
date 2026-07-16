"""Tests for the short hourly trade-status heartbeat in run_trade_updates.py."""

from __future__ import annotations

import re

import scripts.run_trade_updates as ru


def _plain(text: str) -> str:
    return re.sub(r"</?(b|i|code)>", "", text)


def test_short_id_formats():
    # Canonical format: keep the last underscore-separated chunk.
    assert ru._short_id("TRADE_20260623_120035_726925_7cf3f415") == "#7cf3f415"
    # No underscores: fall back to the last 8 characters.
    assert ru._short_id("weirdformat12345678") == "#12345678"
    assert ru._short_id("") == "#?"


def test_status_message_per_trade_lines_and_net():
    open_trades = [
        {"id": "TRADE_A_7cf3f415", "type": "SELL", "status": "OPEN"},
        {"id": "TRADE_B_a1b2c3d4", "type": "BUY", "status": "TP1_HIT"},
    ]
    evals = [
        {"trade_id": "TRADE_A_7cf3f415", "pnl_points": -113.0, "new_status": "OPEN", "progress_to_tp1": 0.0},
        {"trade_id": "TRADE_B_a1b2c3d4", "pnl_points": 48.0, "new_status": "TP1_HIT", "progress_to_tp1": 0.36},
    ]
    msg = _plain(ru._build_status_message(open_trades, evals, 4136.12))
    # One line per trade with direction, short id, points and USD.
    assert "SELL #7cf3f415" in msg
    assert "-113 pts (-11.3$)" in msg
    assert "BUY  #a1b2c3d4" in msg or "BUY #a1b2c3d4" in msg
    assert "+48 pts (+4.8$)" in msg
    # Progress and status surfaced.
    assert "36%➜TP1" in msg
    assert "[TP1_HIT]" in msg
    # Net total: -113 + 48 = -65 pts.
    assert "Net:" in msg and "-65 pts (-6.5$)" in msg


def test_status_message_no_open_trades():
    msg = _plain(ru._build_status_message([], [], 4136.12))
    assert "No open trades" in msg


def test_status_message_separates_pending_orders_from_open_trades():
    trades = [
        {"id": "TRADE_LIVE_11111111", "type": "BUY", "status": "OPEN"},
        {"id": "TRADE_PENDING_22222222", "type": "SELL", "status": "PENDING", "order_type": "SELL_LIMIT", "entry_price": 4040.6},
    ]
    evals = [
        {"trade_id": "TRADE_LIVE_11111111", "pnl_points": 25.0, "new_status": "OPEN", "progress_to_tp1": 0.2},
        {"trade_id": "TRADE_PENDING_22222222", "pnl_points": 0.0, "new_status": "PENDING"},
    ]
    msg = _plain(ru._build_status_message(trades, evals, 4035.75))
    assert "1 open / 1 pending" in msg
    assert "Pending Orders (1)" in msg
    assert "SELL_LIMIT" in msg
    assert "pts to fill" in msg
    assert "waiting" in msg
    assert "+25 pts (+2.5$)" in msg
    assert "+0 pts" not in msg


def test_status_message_caps_long_lists():
    trades = [{"id": f"TRADE_{i}_abcd{i:04d}", "type": "BUY", "status": "OPEN"} for i in range(25)]
    evals = [{"trade_id": t["id"], "pnl_points": 1.0, "new_status": "OPEN"} for t in trades]
    msg = _plain(ru._build_status_message(trades, evals, 4000.0))
    assert "and 5 more" in msg


def test_update_trades_main_skips_market_data_when_no_active_trades(monkeypatch):
    """No active trade means no price fetch / no heavy update work."""
    monkeypatch.setattr(ru, "load_config", lambda: {"trade_management": {"update_outside_trading_hours": True}})

    class _Session:
        def check(self):
            return {"trading_allowed": True, "current_session": "Test", "session_quality": "HIGH"}

    class _DB:
        def get_open_trades(self):
            return []

    monkeypatch.setattr(ru, "TradingSessionAgent", lambda *_a, **_k: _Session())
    monkeypatch.setattr(ru, "DatabaseService", lambda *_a, **_k: _DB())
    monkeypatch.setattr(ru, "TelegramService", lambda *_a, **_k: object())

    def _market_data_should_not_be_called(*_a, **_k):
        raise AssertionError("MarketDataService should not be initialized when no active trades exist")

    monkeypatch.setattr(ru, "MarketDataService", _market_data_should_not_be_called)

    ru.main()
