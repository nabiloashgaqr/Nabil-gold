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
    db.get_trades_for_date.return_value = today
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


def test_report_date_env_regenerates_yesterday(monkeypatch):
    cap = {}

    class _Tg:
        def send_message(self, text, **k):
            cap["t"] = text
            return True
        def send_error_alert(self, *a, **k):
            return True

    db = MagicMock()
    db.get_trades_for_date.return_value = [
        {"type": "SELL", "status": "SL_HIT", "entry_price": 4031.8, "final_pnl": 186.0, "signal_snapshot": {}}
    ]
    db.get_open_trades.return_value = [{"type": "BUY", "status": "OPEN", "entry_price": 1, "current_price": 1}]
    monkeypatch.setenv("REPORT_DATE", "2026-06-29")
    monkeypatch.setattr(rd, "TelegramService", lambda *a, **k: _Tg())
    monkeypatch.setattr(rd, "DatabaseService", lambda *a, **k: db)
    monkeypatch.setattr(rd, "_read_eod_section", lambda name: "")
    monkeypatch.setattr(rd, "_cleanup_eod_sections", lambda: None)

    rd.main()

    db.get_trades_for_date.assert_called_once()
    assert db.get_trades_for_date.call_args.args[0] == "2026-06-29"
    assert "2026-06-29" in cap["t"]
    assert "SL+ / Profit Locked" in cap["t"]
    # Historical repair must not mix today's live open trades into yesterday.
    assert "Open Trades:</b> none" in cap["t"] or "Open Trades: none" in cap["t"]


def test_breakeven_excluded_from_win_rate_denominator(monkeypatch):
    today = [
        {"type": "SELL", "status": "TP2_HIT", "final_pnl": 700.0, "signal_snapshot": {}},
        {"type": "SELL", "status": "SL_HIT", "final_pnl": 548.0, "signal_snapshot": {}},
        {"type": "SELL", "status": "SL_HIT", "final_pnl": 487.0, "signal_snapshot": {}},
        {"type": "SELL", "status": "BE_HIT", "final_pnl": 0.0, "signal_snapshot": {}},
        {"type": "SELL", "status": "SL_HIT", "final_pnl": -300.0, "signal_snapshot": {}},
        {"type": "SELL", "status": "SL_HIT", "final_pnl": -300.0, "signal_snapshot": {}},
    ]
    text = _run(today, [], monkeypatch)
    assert "Trades: 6 (✅ 3 · ❌ 2 · ➖ 1" in text
    assert "Win rate: 60.0%" in text
    assert "Win rate: 50.0%" not in text


def test_daily_report_includes_analyst_overlap_section(monkeypatch):
    cap = {}

    class _Tg:
        def send_message(self, text, **k):
            cap["t"] = text
            return True
        def send_error_alert(self, *a, **k):
            return True

    class _Distill:
        enabled = True
        def __init__(self, *a, **k):
            pass
        def compare_recent(self, **_k):
            return {
                "labels_considered": 5,
                "matched_labels": 3,
                "partial_matches": 1,
                "missed_labels": 1,
                "coverage_rate_pct": 80.0,
                "match_rate_pct": 60.0,
                "avg_entry_distance_points": 42.0,
                "top_missed_reasons": [
                    {"reason_code": "MISSED_ENTRY_TOO_FAR", "count": 1},
                    {"reason_code": "MISSED_TIMING_WINDOW", "count": 1},
                ],
            }

    db = MagicMock()
    db.get_trades_for_date.return_value = []
    db.get_open_trades.return_value = []
    db.get_recent_trades.return_value = []
    monkeypatch.setattr(rd, "TelegramService", lambda *a, **k: _Tg())
    monkeypatch.setattr(rd, "DatabaseService", lambda *a, **k: db)
    monkeypatch.setattr(rd, "AnalystDistillationService", _Distill)
    monkeypatch.setattr(rd, "_read_eod_section", lambda name: "")
    monkeypatch.setattr(rd, "_cleanup_eod_sections", lambda: None)

    rd.main()

    text = re.sub(r"</?(b|i|code)>", "", cap["t"])
    assert "Analyst Overlap" in text
    assert "Coverage: 80.0% | Match: 60.0%" in text
    assert "MISSED_ENTRY_TOO_FAR" in text
