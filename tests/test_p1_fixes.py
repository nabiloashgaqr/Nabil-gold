"""Regression tests for P1 production-readiness fixes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.database import DatabaseService
from services.learning_service import LearningService
from services.news_feed import ForexFactoryScraper, ForexNews, NewsImpact, Sentiment
from services.performance_dashboard import PerformanceDashboard


def test_config_one_agent_groq_defaults() -> None:
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))

    assert cfg["schedule"]["timezone"] == "Asia/Hebron"
    assert cfg["trading_hours"]["timezone"] == "Asia/Hebron"
    assert int(cfg["ai_service"]["max_tokens"]) >= 800
    assert int(cfg["groq_observation_mode"]["min_groq_confidence"]) >= int(cfg["risk_settings"]["min_confidence"])
    assert cfg["groq_observation_mode"]["allow_single_agent_context"] is True
    assert int(cfg["signal_requirements"]["min_agents_agree"]) == 1
    assert int(cfg["signal_requirements"]["min_agreement_percentage"]) == 1
    assert cfg["news_feed"].get("allow_mock") is False


def test_learning_service_reads_modern_pnl_fields() -> None:
    service = LearningService(database_service=None, config={})

    assert service._trade_pnl({"final_pnl": -12.5, "pnl": 99}) == -12.5
    assert service._trade_pnl({"current_pnl_points": 8.0}) == 8.0
    assert service._analyze_failure_reason({"final_pnl": -25}) == "SL_hit_large_loss"


@pytest.mark.asyncio
async def test_news_feed_no_mock_by_default_and_uppercase_log() -> None:
    class DB:
        def __init__(self) -> None:
            self.params = None

        async def execute_query(self, _query, params):
            self.params = params
            return []

    db = DB()
    feed = ForexFactoryScraper(db, {"news_feed": {"enabled": True, "allow_mock": False}})

    assert await feed.fetch_calendar(days=1) == []

    news = ForexNews(
        title="US CPI",
        date=datetime.now(timezone.utc),
        time="14:30",
        currency="USD",
        impact=NewsImpact.HIGH,
        forecast=None,
        previous=None,
        actual=None,
        sentiment=Sentiment.NEUTRAL,
        trading_impact="NEUTRAL",
        confidence_adjustment=-20,
    )
    await feed._log_news(news)
    assert db.params[2] == "HIGH"


@pytest.mark.asyncio
async def test_performance_dashboard_uses_trade_snapshots(tmp_path: Path) -> None:
    db = DatabaseService({"database": {"local_fallback_file": str(tmp_path / "trades.json")}})
    decision = {
        "decision": "BUY",
        "signal": {"type": "BUY", "entry": {"price": 2350}, "stop_loss": 2340, "tp1": 2360, "tp2": 2370},
        "confidence": 80,
        "current_price": 2350,
        "votes": {"BUY": [{"agent": "technical", "confidence": 80}], "SELL": [], "WAIT": []},
        "session_info": {"current_session": "London-NY", "session_quality": "HIGH"},
    }
    trade_id = db.save_trade(decision)
    db.update_trade(trade_id, {"status": "TP2_HIT", "final_pnl": 20})

    dashboard = PerformanceDashboard(db, {"paper_trading": {"starting_balance": 10000}})
    agents = await dashboard.get_agent_performance(days=7)
    sessions = await dashboard.get_session_performance(days=7)
    portfolio = await dashboard.get_portfolio_summary()

    assert agents["technical"].winning_trades == 1
    assert agents["technical"].win_rate == 100.0
    assert sessions[0].session_name == "London-NY"
    assert portfolio["total_pnl"] == 20
