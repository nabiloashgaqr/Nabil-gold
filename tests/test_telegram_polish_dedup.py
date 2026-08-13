"""Telegram final-boundary polish and cross-process duplicate suppression."""
from __future__ import annotations

import sqlite3
import types
from pathlib import Path

from services.telegram_bot import TelegramService


class _Session:
    def __init__(self, statuses=None):
        self.statuses = list(statuses or [200])
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": dict(json or {}), "timeout": timeout})
        status = self.statuses.pop(0) if self.statuses else 200
        return types.SimpleNamespace(status_code=status)


def _service(tmp_path, statuses=None):
    service = TelegramService({
        "telegram": {"bot_token": "TOKEN", "chat_id": "CHAT"},
        "telegram_delivery": {"dedup_seconds": 180, "pending_dedup_seconds": 30},
    })
    service.session = _Session(statuses)
    service._dedup_path = tmp_path / "telegram_delivery_dedup.sqlite3"
    return service


def test_polish_removes_retired_paper_label_and_duplicate_prefix(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "mt5_demo")
    service = _service(tmp_path)
    assert service.send_message(
        "🧪 DEMO: Paper trading signal; not financial advice.\nSame\nSame")
    sent = service.session.calls[0]["json"]["text"]
    assert sent.startswith("🧪 DEMO · ")
    assert sent.count("🧪 DEMO") == 1
    assert "paper" not in sent.lower()
    assert "ورقي" not in sent
    assert sent.count("Same") == 1


def test_exact_duplicate_is_sent_once_and_reported_success(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "mt5_demo")
    service = _service(tmp_path)
    assert service.send_message("Status unchanged") is True
    assert service.send_message("Status unchanged") is True
    assert len(service.session.calls) == 1


def test_persistent_dedup_suppresses_same_message_across_services(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "mt5_demo")
    # Exercise the real persistent path rather than pytest's in-memory shortcut.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PYTEST_RUNNING", raising=False)
    first = _service(tmp_path)
    second = _service(tmp_path)
    assert first.send_message("One broker event") is True
    assert second.send_message("One broker event") is True
    assert len(first.session.calls) == 1
    assert len(second.session.calls) == 0
    with sqlite3.connect(tmp_path / "telegram_delivery_dedup.sqlite3") as conn:
        row = conn.execute("SELECT state FROM deliveries").fetchone()
    assert row == ("delivered",)


def test_failed_delivery_releases_reservation_for_retry(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "mt5_demo")
    service = _service(tmp_path, statuses=[500, 200])
    assert service.send_message("Retry me") is False
    assert service.send_message("Retry me") is True
    assert len(service.session.calls) == 2


def test_arabic_retired_label_is_removed() -> None:
    text = TelegramService._polish_message_text(
        "إشارة تداول ورقي\nالحساب الورقية", demo=True)
    assert "ورقي" not in text
    assert "ديمو" in text


def test_shipped_user_facing_templates_have_no_retired_wording() -> None:
    root = Path(__file__).resolve().parents[1]
    files = [
        "services/telegram_bot.py", "services/weekly_report.py",
        "agents/daily_report_agent.py", "scripts/run_daily_report.py",
        "dashboard/index.html",
    ]
    forbidden = ("Paper trading", "Paper-trading", "Paper Trading",
                 "paper trading only", "تداول ورقي")
    for rel in files:
        source = (root / rel).read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in source, f"{phrase!r} still present in {rel}"
