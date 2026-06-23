"""Tests for the professional daily summary: closed-trade section + correct
points units (gold: 1 USD = 10 points)."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import scripts.run_daily_report as rd


def _run(today, open_t, monkeypatch):
    cap = {}

    class _Tg:
        def send_message(self, text, **k):
            cap["t"] = text
            return True

        def send_error_alert(self, *a, **k):
            return True

    monkeypatch.setattr(rd, "TelegramService", lambda *a, **k: _Tg())
    db = MagicMock()
    db.get_today_trades.return_value = today
    db.get_open_trades.return_value = open_t
    db.get_recent_trades.return_value = today
    monkeypatch.setattr(rd, "DatabaseService", lambda *a, **k: db)
    monkeypatch.setattr(rd, "_read_eod_section", lambda name: "")
    monkeypatch.setattr(rd, "_cleanup_eod_sections", lambda: None)
    rd.main()
    return re.sub(r"</?(b|i|code)>", "", cap["t"])


def test_open_trade_pnl_in_points_not_usd(monkeypatch):
    # SELL 4141.82 -> 4110.94 = 30.88$ = ~309 points. Must show points, not 30.9.
    open_t = [{"type": "SELL", "status": "OPEN", "entry_price": 4141.82,
               "current_price": 4110.94, "current_pnl": 308.8}]
    text = _run(open_t, open_t, monkeypatch)
    assert "+309 pts" in text
    assert "(+30.9$)" in text  # USD shown alongside


def test_closed_trades_section_present(monkeypatch):
    today = [
        {"type": "BUY", "status": "TP2_HIT", "entry_price": 4000.0, "final_pnl": 120.0, "signal_snapshot": {}},
        {"type": "SELL", "status": "SL_HIT", "entry_price": 4050.0, "final_pnl": -80.0, "signal_snapshot": {}},
    ]
    text = _run(today, [], monkeypatch)
    assert "Closed Trades:" in text
    assert "TP2_HIT" in text and "SL_HIT" in text
    # win/loss counts
    assert "✅ 1" in text and "❌ 1" in text
    # closed net = 120 - 80 = +40 pts
    assert "Closed Net: +40 pts" in text


def test_performance_block_has_winrate_and_net(monkeypatch):
    today = [
        {"type": "BUY", "status": "TP2_HIT", "entry_price": 4000.0, "final_pnl": 120.0, "signal_snapshot": {}},
        {"type": "SELL", "status": "SL_HIT", "entry_price": 4050.0, "final_pnl": -80.0, "signal_snapshot": {}},
    ]
    text = _run(today, [], monkeypatch)
    assert "Performance (today)" in text
    assert "Win rate:" in text
    assert "Net:" in text


def test_no_trades_today_graceful(monkeypatch):
    text = _run([], [], monkeypatch)
    assert "Closed Trades:</b> none today" in text or "Closed Trades: none today" in text
    assert "Open Trades:" in text
