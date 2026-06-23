"""Tests for the consolidated end-of-day digest.

Goal: at end of day the user gets ONE message, not four. The learning and AI
trade-review sub-scripts run in EOD_QUIET mode (no standalone Telegram message)
and hand their summaries to the daily report via storage/eod_*.txt, which folds
them into a single consolidated message and cleans up the files.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import scripts.run_daily_report as rd


def _seed_eod(tmp_path: Path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "eod_learning.txt").write_text(
        "📊 Learning Update\n━━━━━━\nTrades analyzed: 12 | Win rate: 58%\nNew weights saved.\n",
        encoding="utf-8",
    )
    (storage / "eod_review.txt").write_text(
        "🧠 AI Trade Review (Losses)\n━━━━━━\nReviewed: 2 trade(s)\n🔻 Trade: TRADE_X\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rd, "_eod_dir", lambda: storage)
    return storage


def _run(monkeypatch) -> str:
    captured: dict = {}

    class _Tg:
        def send_message(self, text, **k):
            captured["t"] = text
            return True

        def send_error_alert(self, *a, **k):
            return True

    monkeypatch.setattr(rd, "TelegramService", lambda *a, **k: _Tg())
    db = MagicMock()
    db.get_today_trades.return_value = [{"type": "SELL", "entry_price": 4124.0, "current_price": 4130.0, "final_pnl": -60}]
    db.get_open_trades.return_value = [{"type": "SELL", "entry_price": 4124.0, "current_price": 4130.0}]
    db.get_recent_trades.return_value = [{"final_pnl": 5}]
    monkeypatch.setattr(rd, "DatabaseService", lambda *a, **k: db)
    monkeypatch.setattr(rd, "DailyReportAgent", lambda *a, **k: MagicMock(
        generate=lambda t: {"text": "📈 Performance\nTrades: 1 | Wins: 0 | Losses: 1"}))
    rd.main()
    return captured["t"]


def test_single_message_contains_all_sections(tmp_path, monkeypatch):
    _seed_eod(tmp_path, monkeypatch)
    text = _run(monkeypatch)
    # One message with performance + open trades + learning + review folded in.
    assert "Daily Summary" in text
    assert "Learning Update" in text
    assert "AI Trade Review" in text
    assert "Reviewed: 2 trade(s)" in text


def test_eod_files_cleaned_up(tmp_path, monkeypatch):
    storage = _seed_eod(tmp_path, monkeypatch)
    _run(monkeypatch)
    assert not (storage / "eod_learning.txt").exists()
    assert not (storage / "eod_review.txt").exists()


def test_no_doubled_section_titles(tmp_path, monkeypatch):
    _seed_eod(tmp_path, monkeypatch)
    text = re.sub(r"</?(b|i|code)>", "", _run(monkeypatch))
    # The learning block header should appear once, not twice in a row.
    assert text.count("Learning Update") == 1


def test_works_without_eod_files(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(rd, "_eod_dir", lambda: storage)
    text = _run(monkeypatch)
    # Still sends a single daily summary even when sub-sections are absent.
    assert "Daily Summary" in text


def test_compact_section_drops_title_and_dividers():
    raw = "📊 Learning Update\n━━━━━━\nLine A\n\nLine B"
    out = rd._compact_section(raw, max_lines=8)
    assert "Learning Update" not in out  # title dropped
    assert "━" not in out               # divider dropped
    assert "Line A" in out and "Line B" in out
