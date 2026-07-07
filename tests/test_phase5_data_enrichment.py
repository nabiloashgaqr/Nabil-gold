"""Phase 5 data-enrichment regression tests."""

from __future__ import annotations

import pytest

from services.database import DatabaseService
from services.weekly_report import WeeklyReportService


def test_database_entry_enrichment_persists_context_fields() -> None:
    db = DatabaseService({"database": {"url": None, "key": None}, "schedule": {"timezone": "Asia/Hebron"}})
    decision = {
        "decision": "SELL",
        "confidence": 80,
        "session_info": {"current_session": "London / Europe Midday", "session_quality": "HIGH"},
        "daily_bias": {"bias": "BEARISH"},
        "news_context": {"rule_based": {"market_status": "CAUTION", "risk_level": "HIGH"}},
        "market_context": {"technical_regime": {"volatility_regime": "HIGH", "trend_strength": "STRONG"}},
        "signal": {"type": "SELL", "entry": {"price": 4000.0}, "stop_loss": 4020.0, "tp2": 3940.0, "rr_ratio": 3.0},
    }

    enriched = db._entry_enrichment(decision, decision["signal"], "XAU/USD", 4000.0, 4020.0)

    assert enriched["planned_risk_points"] == pytest.approx(200.0)
    assert enriched["planned_tp2_points"] == pytest.approx(600.0)
    assert enriched["planned_rr"] == pytest.approx(3.0)
    assert enriched["session_label"] == "London / Europe Midday"
    assert enriched["session_quality"] == "HIGH"
    assert enriched["news_status_at_entry"] == "CAUTION"
    assert enriched["news_risk_at_entry"] == "HIGH"
    assert enriched["volatility_regime"] == "HIGH"
    assert enriched["trend_strength"] == "STRONG"
    assert enriched["daily_bias_at_entry"] == "BEARISH"
    assert enriched["entry_day_of_week"]
    assert isinstance(enriched["entry_hour_local"], int)


def test_weekly_stats_include_phase5_enrichment_metrics() -> None:
    service = WeeklyReportService(
        config={"weekly_report": {"enabled": True, "lookback_days": 7, "min_trades_for_report": 0}},
        database=object(),
        telegram=None,
    )
    trades = [
        {
            "id": "T1",
            "type": "SELL",
            "status": "TP2_HIT",
            "entry_price": 4000.0,
            "stop_loss": 4020.0,
            "planned_risk_points": 200,
            "planned_rr": 3.0,
            "final_pnl": 600,
            "entry_time": "2026-06-30T10:00:00+00:00",
            "entry_day_of_week": "Tuesday",
            "volatility_regime": "HIGH",
            "news_status_at_entry": "SAFE",
        },
        {
            "id": "T2",
            "type": "BUY",
            "status": "SL_HIT",
            "entry_price": 4000.0,
            "stop_loss": 3980.0,
            "planned_risk_points": 200,
            "planned_rr": 2.0,
            "final_pnl": -200,
            "entry_time": "2026-07-01T18:00:00+00:00",
            "entry_day_of_week": "Wednesday",
            "volatility_regime": "LOW",
            "news_status_at_entry": "CAUTION",
        },
    ]

    rr = service._rr_efficiency(trades)
    tow = service._time_of_week_breakdown(trades)
    regime = service._regime_fit(trades)
    news = service._news_proximity(trades)

    assert rr["sample"] == 1
    assert rr["avg_actual_r"] == pytest.approx(3.0)
    assert rr["avg_planned_rr"] == pytest.approx(2.5)
    assert tow["Tuesday"]["pnl"] == pytest.approx(600)
    assert tow["Wednesday"]["pnl"] == pytest.approx(-200)
    assert regime["HIGH"]["win_rate_pct"] == pytest.approx(100)
    assert regime["LOW"]["win_rate_pct"] == pytest.approx(0)
    assert news["SAFE"]["pnl"] == pytest.approx(600)
    assert news["CAUTION"]["pnl"] == pytest.approx(-200)
