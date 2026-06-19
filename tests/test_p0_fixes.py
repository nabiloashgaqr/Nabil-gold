"""Regression tests for P0 production-safety fixes."""

from __future__ import annotations

import pytest

from agents.decision_agent import DecisionAgent
from agents.risk_management_agent import RiskManagementAgent
from scripts.run_analysis import synthetic_timeframe_sources
from services.database import DatabaseService


def test_risk_agent_reads_nested_technical_atr_and_levels() -> None:
    """RiskManagementAgent must use TechnicalAgent's real nested ATR, not fallback 1.5."""
    config = {
        "risk_settings": {"max_open_trades": 3, "min_rr_ratio": 1.5},
        "filters": {"min_atr_for_entry": 1.0, "max_spread_points": 5, "max_consecutive_losses": 3},
        "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2},
    }
    results = {
        "current_price": 2350.0,
        "spread_points": 2.0,
        "technical": {
            "signal": "BUY",
            "confidence": 80,
            "technical": {
                "indicators_raw": {"atr": 4.0},
                "key_levels": {"nearest_support": 2344.0, "nearest_resistance": 2360.0},
            },
        },
        "classical": {"direction": "BUY", "confidence": 75, "support_levels": [2344.0], "resistance_levels": [2360.0]},
        "smc": {"direction": "BUY", "confidence": 75, "entry_suggestion": {"type": "BUY", "entry": 2350.0, "sl": 2343.0, "tp": 2360.0}, "order_blocks": []},
        "price_action": {"direction": "NEUTRAL", "confidence": 30},
        "multitimeframe": {"direction": "BUY", "confidence": 80},
        "portfolio": {"open_trades_count": 0, "today_signals_count": 0, "consecutive_losses": 0},
    }

    result = RiskManagementAgent(config).evaluate(results)

    assert result["risk_metrics"]["atr"] == 4.0
    assert result["stop_loss"]["price"] != 2347.75  # 1.5 ATR fallback + SMC buffer path


def test_decision_agent_counts_neutral_as_wait_vote() -> None:
    config = {
        "risk_settings": {"min_confidence": 60},
        "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2},
    }
    votes = DecisionAgent(config)._collect_votes(
        {
            "technical": {"signal": "WAIT", "confidence": 40},
            "classical": {"direction": "NEUTRAL", "confidence": 30},
            "smc": {"direction": "BUY", "confidence": 70},
        }
    )

    assert len(votes["WAIT"]) == 2
    assert votes["WAIT"][1]["agent"] == "classical"


def test_synthetic_timeframe_sources_detects_any_synthetic_frame() -> None:
    data = {
        "source": "twelve_data",
        "timeframe": "15m",
        "timeframes": {
            "5m": {"source": "twelve_data"},
            "15m": {"source": "twelve_data"},
            "1H": {"source": "synthetic_demo"},
            "4H": {"source": "twelve_data"},
        },
    }

    assert synthetic_timeframe_sources(data) == ["1H"]


def test_database_requires_supabase_when_env_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUIRE_SUPABASE", "true")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="Supabase credentials missing|Supabase is required"):
        DatabaseService({"database": {"local_fallback_file": "storage/test-p0-trades.json"}})


def test_database_does_not_fallback_after_supabase_operation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenTable:
        def select(self, *_args, **_kwargs):
            return self

        def in_(self, *_args, **_kwargs):
            return self

        def execute(self):
            raise RuntimeError("network down")

    class BrokenClient:
        def table(self, _name):
            return BrokenTable()

    monkeypatch.delenv("REQUIRE_SUPABASE", raising=False)
    service = DatabaseService({"database": {"local_fallback_file": "storage/test-p0-trades.json"}})
    service.use_supabase = True
    service.client = BrokenClient()

    monkeypatch.setenv("REQUIRE_SUPABASE", "true")
    with pytest.raises(RuntimeError, match="Failed to fetch open trades|get_open_trades failed"):
        service.get_open_trades()
