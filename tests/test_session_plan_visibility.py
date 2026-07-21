from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.database import DatabaseService
from utils.helpers import load_trades
import scripts.run_analysis as ra


def _db(tmp_path: Path) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None, "local_fallback_file": str(tmp_path / 'trades.json')}})
    db.local_path = tmp_path / 'trades.json'
    db.session_plans_path = tmp_path / 'session_plans.json'
    return db


def test_database_service_saves_and_reads_session_plan_locally(tmp_path: Path) -> None:
    db = _db(tmp_path)
    plan = {
        "plan_id": "PLAN::XAU::1",
        "scenario_id": "SCENARIO::XAU::1",
        "symbol": "XAU/USD",
        "session_label": "London / Europe Midday",
        "session_quality": "HIGH",
        "session_bias": "SELL",
        "scenario_type": "LIQUIDITY_REVERSAL",
        "planner_source": "fallback_day_map",
        "authority_state": "CONFIRMED",
        "authority_direction": "SELL",
        "plan_ready": True,
        "plan_status": "READY",
        "plan_reason": "fallback day map ready",
        "planner_confidence": 84.5,
        "planner_grade": "A",
        "poi_classification": "EXTREME_POI",
        "extreme_poi": True,
        "primary_entry_price": 4020.0,
        "standby_entry_price": 4030.0,
        "invalidation_level": 4045.0,
        "target_liquidity": 3965.0,
        "market_zone_context": "PREMIUM",
        "structure_trend": "BEARISH",
        "structure_quality": "STRONG",
        "execution_preference": "SPLIT_EXECUTION_WATCH",
        "expected_path": "reject premium then deliver lower",
        "plan_created_at": "2026-07-21T06:00:00Z",
        "plan_expires_at": "2026-07-21T14:00:00Z",
    }
    snapshot_id = db.save_session_plan(plan, {"current_price": 4010.0, "market_data_source": "twelvedata", "analysis_run_at": "2026-07-21T06:05:00Z"})
    rows = load_trades(db.session_plans_path)
    assert snapshot_id
    assert len(rows) == 1
    assert rows[0]["id"] == snapshot_id
    assert rows[0]["plan_id"] == "PLAN::XAU::1"
    assert rows[0]["plan_ready"] is True
    assert rows[0]["current_price"] == 4010.0
    latest = db.get_latest_session_plan("XAU/USD")
    assert latest is not None
    assert latest["scenario_id"] == "SCENARIO::XAU::1"
    assert db.get_latest_sent_session_plan("XAU/USD") is None
    db.mark_session_plan_telegram_sent(snapshot_id, "first_ready_plan_this_session")
    sent_latest = db.get_latest_sent_session_plan("XAU/USD")
    assert sent_latest is not None
    assert sent_latest["telegram_delivery_note"] == "first_ready_plan_this_session"


def test_run_analysis_persists_session_plan_snapshot(monkeypatch, tmp_path: Path) -> None:
    config = {
        "symbol": "XAU/USD",
        "risk_settings": {"max_daily_signals": 50, "max_open_trades": 50},
        "duplicate_signal_filter": {"enabled": False},
        "trading_hours": {"enabled": False},
        "notifications": {"send_no_signal_updates": False, "hourly_status": False},
        "session_planner": {"enabled": True, "create_pending_orders_from_plan": False},
        "session_plan_delivery": {"enabled": True, "only_when_ready": True, "min_change_points": 60},
        "trading_mode": "paper",
        "paper_trading": {"enabled": True},
        "operation_mode": "observation",
    }
    monkeypatch.setattr(ra, "load_config", lambda: config)

    telegram = MagicMock()
    telegram.send_message.return_value = True
    telegram.send_error_alert.return_value = True
    telegram.send_session_plan.return_value = True
    monkeypatch.setattr(ra, "TelegramService", lambda *_a, **_k: telegram)

    database = MagicMock()
    database.get_open_trades.return_value = []
    database.get_recent_trades.return_value = []
    database.get_macro_context.return_value = {}
    database.get_recent_session_plans.return_value = []
    database.save_session_plan.return_value = "SESSION_PLAN_TEST"
    monkeypatch.setattr(ra, "DatabaseService", lambda *_a, **_k: database)

    fake_session = MagicMock()
    fake_session.check.return_value = {
        "trading_allowed": True,
        "allow_signals": True,
        "current_session": "London / Europe Midday",
        "session_quality": "HIGH",
    }
    monkeypatch.setattr(ra, "TradingSessionAgent", lambda *_a, **_k: fake_session)

    fake_md = MagicMock()
    fake_md.get_gold_data.return_value = {
        "current_price": 4010.0,
        "source": "twelvedata",
        "source_integrity": {"supports_signal_generation": True, "supports_pending_activation": True, "source": "twelvedata"},
        "timeframes": {},
        "data": [],
    }
    monkeypatch.setattr(ra, "MarketDataService", lambda *_a, **_k: fake_md)

    monkeypatch.setattr(ra, "get_learning_service", lambda *_a, **_k: None)
    class _FakeGemini:
        enabled = True
        def analyze_market_context(self, *_a, **_k):
            return {"available": True, "market_bias": "SELL", "reason": "Premium rejection day map."}
        def interpret_news_context(self, *_a, **_k):
            return {"available": True, "risk_level": "LOW", "trading_advice": "No major blocker."}
        def interpret_macro_context(self, *_a, **_k):
            return {"available": True, "macro_verdict": "BEARISH_GOLD", "confidence": 67, "reason": "Higher yields pressure gold."}
        def review_signal(self, *_a, **_k):
            return {"available": False}
    monkeypatch.setattr(ra, "get_gemini_review_service", lambda *_a, **_k: _FakeGemini())

    monkeypatch.setattr(ra, "run_agent", lambda name, agent, data: {"agent": name, "signal": "WAIT", "confidence": 0})
    monkeypatch.setattr(ra, "RiskManagementAgent", lambda *_a, **_k: MagicMock(evaluate=lambda r: {}))
    monkeypatch.setattr(ra, "DynamicRiskManager", lambda *_a, **_k: MagicMock(evaluate=lambda db: {}))
    monkeypatch.setattr(
        ra,
        "SetupMemoryService",
        lambda *_a, **_k: MagicMock(process_candidates=lambda candidates, **kwargs: candidates, mark_entry_triggered=lambda **kwargs: None),
    )

    class _FakePlanner:
        def build_plan(self, *_a, **_k):
            return {
                "enabled": True,
                "symbol": "XAU/USD",
                "plan_ready": True,
                "plan_status": "READY",
                "plan_reason": "session plan ready",
                "plan_id": "PLAN::TEST",
                "scenario_id": "SCENARIO::TEST",
                "planner_source": "fallback_day_map",
                "authority_state": "CONFIRMED",
                "authority_direction": "SELL",
                "session_label": "London / Europe Midday",
                "session_quality": "HIGH",
                "session_bias": "SELL",
                "scenario_type": "LIQUIDITY_REVERSAL",
                "planner_confidence": 83.0,
                "planner_grade": "A",
                "primary_entry_price": 4020.0,
                "standby_entry_price": 4030.0,
                "invalidation_level": 4045.0,
                "target_liquidity": 3965.0,
                "market_zone_context": "PREMIUM",
                "structure_trend": "BEARISH",
                "structure_quality": "STRONG",
                "execution_preference": "LADDER_PENDING",
                "expected_path": "reject premium then deliver lower",
                "plan_created_at": "2026-07-21T06:00:00Z",
                "plan_expires_at": "2026-07-21T14:00:00Z",
            }
    monkeypatch.setattr(ra, "SessionPlannerService", lambda *_a, **_k: _FakePlanner())

    class _FakeDecisionAgent:
        def __init__(self, *_a, **_k):
            pass
        async def decide_async(self, _all_results):
            return {"decision": "WAIT", "reasons": [], "warnings": [], "classic": {}}
    monkeypatch.setattr(ra, "DecisionAgent", _FakeDecisionAgent)

    asyncio.run(ra.run_analysis_async())

    database.save_session_plan.assert_called_once()
    saved_plan = database.save_session_plan.call_args[0][0]
    saved_context = database.save_session_plan.call_args[0][1]
    assert saved_plan["plan_ready"] is True
    assert saved_plan["session_bias"] == "SELL"
    assert saved_context["current_price"] == 4010.0
    assert saved_context["market_data_source"] == "twelvedata"
    telegram.send_session_plan.assert_called_once()
    delivered_plan = telegram.send_session_plan.call_args[0][0]
    assert len(delivered_plan["agent_opinions"]) == 6
    assert delivered_plan["agent_opinions"][-1]["label"] == "Macro / Fundamental"
    assert delivered_plan["gemini_plan_review"]["available"] is True
    database.mark_session_plan_telegram_sent.assert_called_once_with("SESSION_PLAN_TEST", "first_ready_plan_this_session")


def test_run_analysis_does_not_resend_same_session_plan(monkeypatch, tmp_path: Path) -> None:
    config = {
        "symbol": "XAU/USD",
        "risk_settings": {"max_daily_signals": 50, "max_open_trades": 50},
        "duplicate_signal_filter": {"enabled": False},
        "trading_hours": {"enabled": False},
        "notifications": {"send_no_signal_updates": False, "hourly_status": False},
        "session_planner": {"enabled": True, "create_pending_orders_from_plan": False},
        "session_plan_delivery": {"enabled": True, "only_when_ready": True, "min_change_points": 60},
        "trading_mode": "paper",
        "paper_trading": {"enabled": True},
        "operation_mode": "observation",
    }
    monkeypatch.setattr(ra, "load_config", lambda: config)

    telegram = MagicMock()
    telegram.send_message.return_value = True
    telegram.send_error_alert.return_value = True
    telegram.send_session_plan.return_value = True
    monkeypatch.setattr(ra, "TelegramService", lambda *_a, **_k: telegram)

    database = MagicMock()
    database.get_open_trades.return_value = []
    database.get_recent_trades.return_value = []
    database.get_macro_context.return_value = {}
    database.get_recent_session_plans.return_value = [{
        "payload": {
            "symbol": "XAU/USD",
            "session_label": "London / Europe Midday",
            "plan_created_at": "2026-07-21T06:00:00Z",
            "plan_ready": True,
            "plan_status": "READY",
            "session_bias": "SELL",
            "scenario_type": "LIQUIDITY_REVERSAL",
            "planner_source": "fallback_day_map",
            "authority_state": "CONFIRMED",
            "authority_direction": "SELL",
            "execution_preference": "LADDER_PENDING",
            "poi_classification": None,
            "primary_entry_price": 4020.0,
            "standby_entry_price": 4030.0,
            "invalidation_level": 4045.0,
            "target_liquidity": 3965.0,
            "primary_entry_zone": {"low": 4018.0, "high": 4022.0},
        }
    }]
    database.save_session_plan.return_value = "SESSION_PLAN_TEST"
    monkeypatch.setattr(ra, "DatabaseService", lambda *_a, **_k: database)

    fake_session = MagicMock()
    fake_session.check.return_value = {
        "trading_allowed": True,
        "allow_signals": True,
        "current_session": "London / Europe Midday",
        "session_quality": "HIGH",
    }
    monkeypatch.setattr(ra, "TradingSessionAgent", lambda *_a, **_k: fake_session)

    fake_md = MagicMock()
    fake_md.get_gold_data.return_value = {
        "current_price": 4010.0,
        "source": "twelvedata",
        "source_integrity": {"supports_signal_generation": True, "supports_pending_activation": True, "source": "twelvedata"},
        "timeframes": {},
        "data": [],
    }
    monkeypatch.setattr(ra, "MarketDataService", lambda *_a, **_k: fake_md)
    monkeypatch.setattr(ra, "get_learning_service", lambda *_a, **_k: None)
    class _FakeGemini:
        enabled = True
        def analyze_market_context(self, *_a, **_k):
            return {"available": True, "market_bias": "SELL", "reason": "Premium rejection day map."}
        def interpret_news_context(self, *_a, **_k):
            return {"available": True, "risk_level": "LOW", "trading_advice": "No major blocker."}
        def interpret_macro_context(self, *_a, **_k):
            return {"available": True, "macro_verdict": "BEARISH_GOLD", "confidence": 67, "reason": "Higher yields pressure gold."}
        def review_signal(self, *_a, **_k):
            return {"available": False}
    monkeypatch.setattr(ra, "get_gemini_review_service", lambda *_a, **_k: _FakeGemini())

    monkeypatch.setattr(ra, "run_agent", lambda name, agent, data: {"agent": name, "signal": "WAIT", "confidence": 0})
    monkeypatch.setattr(ra, "RiskManagementAgent", lambda *_a, **_k: MagicMock(evaluate=lambda r: {}))
    monkeypatch.setattr(ra, "DynamicRiskManager", lambda *_a, **_k: MagicMock(evaluate=lambda db: {}))
    monkeypatch.setattr(
        ra,
        "SetupMemoryService",
        lambda *_a, **_k: MagicMock(process_candidates=lambda candidates, **kwargs: candidates, mark_entry_triggered=lambda **kwargs: None),
    )

    class _FakePlanner:
        def build_plan(self, *_a, **_k):
            return {
                "enabled": True,
                "symbol": "XAU/USD",
                "plan_ready": True,
                "plan_status": "READY",
                "plan_reason": "session plan ready",
                "plan_id": "PLAN::TEST",
                "scenario_id": "SCENARIO::TEST",
                "planner_source": "fallback_day_map",
                "authority_state": "CONFIRMED",
                "authority_direction": "SELL",
                "session_label": "London / Europe Midday",
                "session_quality": "HIGH",
                "session_bias": "SELL",
                "scenario_type": "LIQUIDITY_REVERSAL",
                "planner_confidence": 83.0,
                "planner_grade": "A",
                "primary_entry_price": 4020.0,
                "standby_entry_price": 4030.0,
                "invalidation_level": 4045.0,
                "target_liquidity": 3965.0,
                "market_zone_context": "PREMIUM",
                "structure_trend": "BEARISH",
                "structure_quality": "STRONG",
                "execution_preference": "LADDER_PENDING",
                "expected_path": "reject premium then deliver lower",
                "primary_entry_zone": {"low": 4018.0, "high": 4022.0},
                "plan_created_at": "2026-07-21T06:00:00Z",
                "plan_expires_at": "2026-07-21T14:00:00Z",
            }
    monkeypatch.setattr(ra, "SessionPlannerService", lambda *_a, **_k: _FakePlanner())

    class _FakeDecisionAgent:
        def __init__(self, *_a, **_k):
            pass
        async def decide_async(self, _all_results):
            return {"decision": "WAIT", "reasons": [], "warnings": [], "classic": {}}
    monkeypatch.setattr(ra, "DecisionAgent", _FakeDecisionAgent)

    asyncio.run(ra.run_analysis_async())
    telegram.send_session_plan.assert_not_called()
    database.mark_session_plan_telegram_sent.assert_not_called()


def test_run_analysis_resends_opening_plan_for_new_session(monkeypatch, tmp_path: Path) -> None:
    config = {
        "symbol": "XAU/USD",
        "risk_settings": {"max_daily_signals": 50, "max_open_trades": 50},
        "duplicate_signal_filter": {"enabled": False},
        "trading_hours": {"enabled": False},
        "schedule": {"timezone": "Asia/Hebron"},
        "notifications": {"send_no_signal_updates": False, "hourly_status": False},
        "session_planner": {"enabled": True, "create_pending_orders_from_plan": False},
        "session_plan_delivery": {"enabled": True, "only_when_ready": True, "min_change_points": 60, "min_update_interval_minutes": 25},
        "trading_mode": "paper",
        "paper_trading": {"enabled": True},
        "operation_mode": "observation",
    }
    monkeypatch.setattr(ra, "load_config", lambda: config)

    telegram = MagicMock()
    telegram.send_message.return_value = True
    telegram.send_error_alert.return_value = True
    telegram.send_session_plan.return_value = True
    monkeypatch.setattr(ra, "TelegramService", lambda *_a, **_k: telegram)

    database = MagicMock()
    database.get_open_trades.return_value = []
    database.get_recent_trades.return_value = []
    database.get_macro_context.return_value = {}
    database.get_recent_session_plans.return_value = [{
        "symbol": "XAU/USD",
        "session_label": "Asia Morning",
        "telegram_sent_at": "2026-07-21T06:10:00Z",
        "payload": {
            "symbol": "XAU/USD",
            "session_label": "Asia Morning",
            "plan_ready": True,
            "plan_status": "READY",
            "session_bias": "SELL",
            "scenario_type": "LIQUIDITY_REVERSAL",
            "planner_source": "fallback_day_map",
            "authority_state": "CONFIRMED",
            "authority_direction": "SELL",
            "execution_preference": "LADDER_PENDING",
            "primary_entry_price": 4020.0,
            "standby_entry_price": 4030.0,
            "invalidation_level": 4045.0,
            "target_liquidity": 3965.0,
            "primary_entry_zone": {"low": 4018.0, "high": 4022.0},
            "plan_created_at": "2026-07-21T06:00:00Z",
        },
    }]
    database.save_session_plan.return_value = "SESSION_PLAN_TEST_2"
    monkeypatch.setattr(ra, "DatabaseService", lambda *_a, **_k: database)

    fake_session = MagicMock()
    fake_session.check.return_value = {
        "trading_allowed": True,
        "allow_signals": True,
        "current_session": "London / Europe Midday",
        "session_quality": "HIGH",
    }
    monkeypatch.setattr(ra, "TradingSessionAgent", lambda *_a, **_k: fake_session)

    fake_md = MagicMock()
    fake_md.get_gold_data.return_value = {
        "current_price": 4010.0,
        "source": "twelvedata",
        "source_integrity": {"supports_signal_generation": True, "supports_pending_activation": True, "source": "twelvedata"},
        "timeframes": {},
        "data": [],
    }
    monkeypatch.setattr(ra, "MarketDataService", lambda *_a, **_k: fake_md)

    class _FakeGemini:
        enabled = True
        def analyze_market_context(self, *_a, **_k):
            return {"available": True, "market_bias": "SELL", "reason": "Premium rejection day map."}
        def interpret_news_context(self, *_a, **_k):
            return {"available": True, "risk_level": "LOW", "trading_advice": "No major blocker."}
        def interpret_macro_context(self, *_a, **_k):
            return {"available": True, "macro_verdict": "BEARISH_GOLD", "confidence": 67, "reason": "Higher yields pressure gold."}
        def review_signal(self, *_a, **_k):
            return {"available": False}
    monkeypatch.setattr(ra, "get_gemini_review_service", lambda *_a, **_k: _FakeGemini())
    monkeypatch.setattr(ra, "get_learning_service", lambda *_a, **_k: None)
    monkeypatch.setattr(ra, "run_agent", lambda name, agent, data: {"agent": name, "signal": "WAIT", "confidence": 0})
    monkeypatch.setattr(ra, "RiskManagementAgent", lambda *_a, **_k: MagicMock(evaluate=lambda r: {}))
    monkeypatch.setattr(ra, "DynamicRiskManager", lambda *_a, **_k: MagicMock(evaluate=lambda db: {}))
    monkeypatch.setattr(ra, "SetupMemoryService", lambda *_a, **_k: MagicMock(process_candidates=lambda candidates, **kwargs: candidates, mark_entry_triggered=lambda **kwargs: None))

    class _FakePlanner:
        def build_plan(self, *_a, **_k):
            return {
                "enabled": True,
                "symbol": "XAU/USD",
                "plan_ready": True,
                "plan_status": "READY",
                "plan_reason": "session plan ready",
                "plan_id": "PLAN::TEST2",
                "scenario_id": "SCENARIO::TEST2",
                "planner_source": "fallback_day_map",
                "authority_state": "CONFIRMED",
                "authority_direction": "SELL",
                "session_label": "London / Europe Midday",
                "session_quality": "HIGH",
                "session_bias": "SELL",
                "scenario_type": "LIQUIDITY_REVERSAL",
                "planner_confidence": 83.0,
                "planner_grade": "A",
                "primary_entry_price": 4020.0,
                "standby_entry_price": 4030.0,
                "invalidation_level": 4045.0,
                "target_liquidity": 3965.0,
                "market_zone_context": "PREMIUM",
                "structure_trend": "BEARISH",
                "structure_quality": "STRONG",
                "execution_preference": "LADDER_PENDING",
                "expected_path": "reject premium then deliver lower",
                "primary_entry_zone": {"low": 4018.0, "high": 4022.0},
                "plan_created_at": "2026-07-21T10:00:00Z",
                "plan_expires_at": "2026-07-21T14:00:00Z",
            }
    monkeypatch.setattr(ra, "SessionPlannerService", lambda *_a, **_k: _FakePlanner())

    class _FakeDecisionAgent:
        def __init__(self, *_a, **_k):
            pass
        async def decide_async(self, _all_results):
            return {"decision": "WAIT", "reasons": [], "warnings": [], "classic": {}}
    monkeypatch.setattr(ra, "DecisionAgent", _FakeDecisionAgent)

    asyncio.run(ra.run_analysis_async())
    telegram.send_session_plan.assert_called_once()
    delivered_plan = telegram.send_session_plan.call_args[0][0]
    assert delivered_plan["delivery_context"]["message_kind"] == "OPENING_PLAN"
    assert delivered_plan["delivery_context"]["delivery_reason"] == "first_ready_plan_this_session"

