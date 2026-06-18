"""Tests for phase-three filters and protection agents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.news_risk_agent import NewsRiskAgent
from agents.risk_management_agent import RiskManagementAgent


def test_news_agent_blocks_high_impact_event(tmp_path: Path) -> None:
    event_time = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
    path = tmp_path / "news_events.json"
    path.write_text(
        json.dumps([
            {"event": "US CPI", "time": event_time, "impact": "HIGH", "currency": "USD"}
        ]),
        encoding="utf-8",
    )
    agent = NewsRiskAgent({"filters": {"no_signal_before_news_minutes": 30, "no_signal_after_news_minutes": 15}})
    agent.events_path = path
    result = agent.check()
    assert result["market_status"] == "DANGER"
    assert result["can_trade"] is False
    assert result["active_restrictions"]


def test_news_agent_caution_for_medium_event(tmp_path: Path) -> None:
    event_time = (datetime.now(timezone.utc) + timedelta(minutes=25)).isoformat().replace("+00:00", "Z")
    path = tmp_path / "news_events.json"
    path.write_text(json.dumps([{"event": "Retail Sales", "time": event_time, "impact": "MEDIUM", "currency": "USD"}]), encoding="utf-8")
    agent = NewsRiskAgent({"filters": {"no_signal_before_news_minutes": 30, "no_signal_after_news_minutes": 15}})
    agent.events_path = path
    result = agent.check()
    assert result["market_status"] == "CAUTION"
    assert result["can_trade"] is True


def base_risk_results() -> dict:
    return {
        "current_price": 2350.0,
        "spread_points": 2.0,
        "technical": {
            "direction": "BUY",
            "confidence": 80,
            "indicators_raw": {"atr": 4.0},
            "key_levels": {"nearest_support": 2344.0, "nearest_resistance": 2360.0},
        },
        "classical": {"direction": "BUY", "confidence": 75, "support_levels": [2344.0, 2338.0], "resistance_levels": [2360.0, 2372.0]},
        "smc": {"direction": "BUY", "confidence": 75, "entry_suggestion": {"type": "BUY", "entry": 2350.0, "sl": 2343.0, "tp": 2360.0}, "order_blocks": []},
        "price_action": {"direction": "NEUTRAL", "confidence": 30},
        "multitimeframe": {"direction": "BUY", "confidence": 80},
        "portfolio": {"open_trades_count": 0, "today_signals_count": 0, "consecutive_losses": 0},
    }


def test_risk_agent_rejects_max_open_trades() -> None:
    config = {"risk_settings": {"max_open_trades": 3, "min_rr_ratio": 1.5}, "filters": {"min_atr_for_entry": 1.0, "max_spread_points": 5, "max_consecutive_losses": 3}}
    results = base_risk_results()
    results["portfolio"]["open_trades_count"] = 3
    result = RiskManagementAgent(config).evaluate(results)
    assert result["approved"] is False
    assert result["rejection_reason"] == "Max trades reached"


def test_risk_agent_rejects_consecutive_losses() -> None:
    config = {"risk_settings": {"max_open_trades": 3, "min_rr_ratio": 1.5}, "filters": {"min_atr_for_entry": 1.0, "max_spread_points": 5, "max_consecutive_losses": 3}}
    results = base_risk_results()
    results["portfolio"]["consecutive_losses"] = 3
    result = RiskManagementAgent(config).evaluate(results)
    assert result["approved"] is False
    assert result["rejection_reason"] == "Cooling after consecutive losses"
