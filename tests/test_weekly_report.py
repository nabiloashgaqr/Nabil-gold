"""Tests for WeeklyReportService."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.weekly_report import (
    TELEGRAM_MAX_CHARS,
    WeeklyReportService,
    WeeklyStats,
)


# -------------------- fixtures -------------------- #

def _make_config(**overrides) -> dict:
    base = {
        "weekly_report": {
            "enabled": True,
            "lookback_days": 7,
            "min_trades_for_report": 5,
            "max_chars": 3500,
            "send_telegram": False,  # don't actually send in tests
            "storage_path": "storage/test_weekly_report.json",
            "timezone": "UTC",
        }
    }
    base["weekly_report"].update(overrides)
    return base


def _trade(**kw) -> dict:
    base = {
        "id": kw.get("id", "t1"),
        "created_at": kw.get("created_at", "2026-06-20T10:00:00+00:00"),
        "status": kw.get("status", "TP2_HIT"),
        "final_pnl": kw.get("final_pnl", 5.0),
        "agents": kw.get("agents", ["technical", "smc"]),
        "session": kw.get("session", "London"),
        "type": "BUY",
    }
    base.update(kw)
    return base


@pytest.fixture
def database_mock():
    """Mock with three closed trades + one open trade."""
    db = MagicMock()
    trades = [
        _trade(id="t1", status="TP2_HIT", final_pnl=8.0,
               created_at="2026-06-20T10:00:00+00:00", session="London"),
        _trade(id="t2", status="SL_HIT", final_pnl=-4.0,
               created_at="2026-06-19T11:00:00+00:00", session="NY"),
        _trade(id="t3", status="TP2_HIT", final_pnl=2.0,
               created_at="2026-06-18T12:00:00+00:00", session="London"),
        _trade(id="t4", status="OPEN", final_pnl=0.0,
               created_at="2026-06-21T09:00:00+00:00", session="London"),
        # Old trade outside lookback window
        _trade(id="t5", status="TP2_HIT", final_pnl=20.0,
               created_at="2026-06-01T10:00:00+00:00", session="London"),
    ]
    db.get_recent_trades.return_value = trades
    db.use_supabase = False
    db.client = None
    return db


@pytest.fixture
def telegram_mock():
    tg = MagicMock()
    tg.send_message.return_value = True
    return tg


# -------------------- WeeklyStats -------------------- #

class TestWeeklyStats:
    def test_to_prompt_dict_has_all_keys(self):
        s = WeeklyStats(total_trades=10, win_rate=60.0, net_pnl_points=12.5)
        d = s.to_prompt_dict()
        for key in ("week", "total_trades", "win_rate_pct", "net_pnl_points",
                    "by_day", "by_agent", "by_session"):
            assert key in d

    def test_round_decimals(self):
        # avg_win_points rounds to 2 decimals, net_pnl_points rounds to 1 decimal.
        s = WeeklyStats(avg_win_points=12.34567, net_pnl_points=1.0 / 3.0,
                        best_day_pnl=1.23456)
        d = s.to_prompt_dict()
        assert d["avg_win_points"] == 12.35
        assert d["net_pnl_points"] == 0.3
        assert d["best_day_pnl"] == 1.23


# -------------------- collect_stats -------------------- #

class TestCollectStats:
    def test_counts_wins_losses_and_open(self, database_mock):
        # Fixture: t1 win (+8), t2 loss (-4), t3 win (+2), t4 open, t5 outside window
        svc = WeeklyReportService(_make_config(), database_mock, telegram=None)
        now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
        stats = svc.collect_stats(now=now)

        assert stats.total_trades == 4  # excludes t5 (outside window)
        assert stats.closed_trades == 3
        assert stats.open_trades == 1
        assert stats.wins == 2
        assert stats.losses == 1
        assert stats.break_even == 0
        assert stats.net_pnl_points == pytest.approx(8.0 - 4.0 + 2.0)

    def test_excludes_old_trades(self, database_mock):
        svc = WeeklyReportService(_make_config(), database_mock, telegram=None)
        now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
        stats = svc.collect_stats(now=now)
        # Old trade (t5) should be filtered out
        assert stats.total_trades == 4

    def test_per_day_buckets(self, database_mock):
        svc = WeeklyReportService(_make_config(), database_mock, telegram=None)
        now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
        stats = svc.collect_stats(now=now)
        assert "2026-06-20" in stats.by_day
        assert stats.best_day in stats.by_day

    def test_per_agent_buckets(self, database_mock):
        svc = WeeklyReportService(_make_config(), database_mock, telegram=None)
        now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
        stats = svc.collect_stats(now=now)
        assert "technical" in stats.by_agent
        assert "smc" in stats.by_agent
        assert "London" in stats.by_session

    def test_handles_empty_recent_trades(self, database_mock):
        database_mock.get_recent_trades.return_value = []
        svc = WeeklyReportService(_make_config(), database_mock, telegram=None)
        stats = svc.collect_stats(now=datetime(2026, 6, 21, tzinfo=timezone.utc))
        assert stats.total_trades == 0
        assert stats.win_rate == 0.0


# -------------------- build_prompt -------------------- #

class TestBuildPrompt:
    def test_includes_data_block_and_constraints(self, database_mock):
        svc = WeeklyReportService(_make_config(), database_mock, telegram=None)
        stats = svc.collect_stats(now=datetime(2026, 6, 21, tzinfo=timezone.utc))
        prompt = svc.build_prompt(stats)
        assert "weekly performance analyst" in prompt
        assert "```json" in prompt
        assert "3500 characters" in prompt
        assert "config.json" in prompt

    def test_respects_max_chars_config(self):
        cfg = _make_config(max_chars=1000)
        svc = WeeklyReportService(cfg, MagicMock(get_recent_trades=MagicMock(return_value=[])),
                                  telegram=None)
        prompt = svc.build_prompt(WeeklyStats())
        assert "1000 characters" in prompt


# -------------------- split_message -------------------- #

class TestSplitMessage:
    def test_short_message_returns_single_chunk(self):
        chunks = WeeklyReportService.split_message("hello")
        assert chunks == ["hello"]

    def test_exact_limit_returns_single_chunk(self):
        text = "a" * TELEGRAM_MAX_CHARS
        chunks = WeeklyReportService.split_message(text)
        assert len(chunks) == 1

    def test_long_message_splits_correctly(self):
        text = ("line\n" * 5000).strip()
        chunks = WeeklyReportService.split_message(text)
        for c in chunks:
            assert len(c) <= TELEGRAM_MAX_CHARS
        # Reassembled should contain all lines
        rejoined = "\n".join(chunks)
        assert rejoined.count("line") == 5000

    def test_no_line_break_falls_back_to_hard_cut(self):
        text = "x" * (TELEGRAM_MAX_CHARS * 2)
        chunks = WeeklyReportService.split_message(text)
        for c in chunks:
            assert len(c) <= TELEGRAM_MAX_CHARS
        assert sum(len(c) for c in chunks) == len(text)


# -------------------- extract_recommendations -------------------- #

class TestExtractRecommendations:
    def test_extracts_numbered_recs(self):
        text = (
            "🎯 التوصيات\n"
            "1) قلّل weight classical_agent من 0.20 إلى 0.15\n"
            "2) زِد نافذة news_risk\n"
            "3) أضف فلتر جديد\n"
        )
        recs = WeeklyReportService._extract_recommendations(text)
        assert len(recs) >= 2
        assert any("classical_agent" in r for r in recs)

    def test_returns_empty_when_no_section(self):
        text = "لا توجد توصيات هنا"
        assert WeeklyReportService._extract_recommendations(text) == []


# -------------------- generate_report (async) -------------------- #

class TestGenerateReport:
    @pytest.mark.asyncio
    async def test_returns_few_trades_message(self, database_mock, telegram_mock):
        cfg = _make_config(min_trades_for_report=10)
        svc = WeeklyReportService(cfg, database_mock, telegram=None,
                                  ai_service=None)
        result = await svc.generate_report(now=datetime(2026, 6, 21, tzinfo=timezone.utc))
        assert result["status"] == "ok_too_few_trades"
        assert "Quiet week" in result["report_text"]

    @pytest.mark.asyncio
    async def test_uses_fallback_when_no_ai_service(self, database_mock, telegram_mock):
        cfg = _make_config(min_trades_for_report=0)
        svc = WeeklyReportService(cfg, database_mock, telegram=telegram_mock,
                                  ai_service=None)
        result = await svc.generate_report(now=datetime(2026, 6, 21, tzinfo=timezone.utc))
        assert result["status"] == "ok_no_ai"
        assert "Total trades" in result["report_text"]

    @pytest.mark.asyncio
    async def test_calls_groq_and_uses_response(self, database_mock, telegram_mock):
        # Mock AI service with AsyncMock for _call_ai
        ai = MagicMock()
        response = MagicMock(success=True, content="📊 تقرير\n🎯 التوصيات\n1) قلّل weight X\n",
                              tokens_used=123, cost=0.001, error=None)
        ai._call_ai = AsyncMock(return_value=response)

        cfg = _make_config(min_trades_for_report=0)
        svc = WeeklyReportService(cfg, database_mock, telegram=telegram_mock, ai_service=ai)
        result = await svc.generate_report(now=datetime(2026, 6, 21, tzinfo=timezone.utc))
        ai._call_ai.assert_called_once()
        assert result["status"] == "ok"
        assert result["tokens_used"] == 123
        assert any("weight" in r for r in result["recommendations"])

    @pytest.mark.asyncio
    async def test_handles_groq_failure_gracefully(self, database_mock, telegram_mock):
        ai = MagicMock()
        response = MagicMock(success=False, content="", error="rate limited")
        ai._call_ai = AsyncMock(return_value=response)

        cfg = _make_config(min_trades_for_report=0)
        svc = WeeklyReportService(cfg, database_mock, telegram=telegram_mock, ai_service=ai)
        result = await svc.generate_report(now=datetime(2026, 6, 21, tzinfo=timezone.utc))
        assert result["status"] == "ok_groq_failed"
        assert "rate limited" in result["error"]


# -------------------- send_to_telegram -------------------- #

class TestSendToTelegram:
    def test_sends_single_message_when_short(self, telegram_mock):
        cfg = _make_config(send_telegram=True)
        svc = WeeklyReportService(cfg, MagicMock(), telegram=telegram_mock,
                                  ai_service=None)
        ok = svc.send_to_telegram("Short message")
        assert ok is True
        assert telegram_mock.send_message.call_count == 1

    def test_splits_long_message_into_parts(self, telegram_mock):
        cfg = _make_config(send_telegram=True)
        svc = WeeklyReportService(cfg, MagicMock(), telegram=telegram_mock,
                                  ai_service=None)
        text = ("line\n" * 1000)
        ok = svc.send_to_telegram(text)
        assert ok is True
        assert telegram_mock.send_message.call_count > 1
        # Every part should include a (Part x/y) prefix when > 1 part
        calls = telegram_mock.send_message.call_args_list
        for i, call in enumerate(calls, 1):
            assert f"Part {i}/{len(calls)}" in call.args[0]

    def test_returns_false_when_telegram_disabled(self, telegram_mock):
        cfg = _make_config(send_telegram=False)
        svc = WeeklyReportService(cfg, MagicMock(), telegram=telegram_mock, ai_service=None)
        assert svc.send_to_telegram("anything") is False
        telegram_mock.send_message.assert_not_called()

    def test_returns_false_when_telegram_none(self):
        cfg = _make_config()
        svc = WeeklyReportService(cfg, MagicMock(), telegram=None, ai_service=None)
        assert svc.send_to_telegram("anything") is False


# -------------------- save -------------------- #

class TestSave:
    def test_saves_to_storage_path(self, database_mock, tmp_path):
        cfg = _make_config(storage_path=str(tmp_path / "weekly.json"))
        svc = WeeklyReportService(cfg, database_mock, telegram=None, ai_service=None)
        svc._save({"status": "ok_no_ai", "report_text": "x", "recommendations": []})
        saved = json.loads((tmp_path / "weekly.json").read_text(encoding="utf-8"))
        assert saved["status"] == "ok_no_ai"
        assert "saved_at" in saved
