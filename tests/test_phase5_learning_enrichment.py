"""Phase 5 learning enrichment tests."""

from __future__ import annotations

import pytest

from services.learning_service import LearningReport, LearningService


class _DB:
    async def execute_query(self, *_args, **_kwargs):
        return []


def _service() -> LearningService:
    return LearningService(_DB(), {"learning": {"enabled": True}})


def test_learning_enrichment_breakdowns_include_rr_news_session_regime() -> None:
    svc = _service()
    trades = [
        {
            "id": "L1",
            "final_pnl": 300,
            "planned_risk_points": 100,
            "planned_rr": 3,
            "session_label": "London / Europe Midday",
            "entry_day_of_week": "Tuesday",
            "news_status_at_entry": "SAFE",
            "volatility_regime": "HIGH",
        },
        {
            "id": "L2",
            "final_pnl": -100,
            "planned_risk_points": 100,
            "planned_rr": 2,
            "session_label": "New York Evening",
            "entry_day_of_week": "Wednesday",
            "news_status_at_entry": "CAUTION",
            "volatility_regime": "LOW",
        },
    ]

    enriched = svc._enrichment_breakdowns(trades)

    assert enriched["rr_efficiency"]["sample"] == 1
    assert enriched["rr_efficiency"]["avg_actual_r"] == pytest.approx(3.0)
    assert enriched["rr_efficiency"]["avg_planned_rr"] == pytest.approx(2.5)
    assert enriched["session_breakdown"]["London / Europe Midday"]["pnl"] == pytest.approx(300)
    assert enriched["day_of_week_breakdown"]["Wednesday"]["pnl"] == pytest.approx(-100)
    assert enriched["news_proximity"]["CAUTION"]["losses"] == 1
    assert enriched["regime_fit"]["HIGH"]["win_rate_pct"] == pytest.approx(100)


def test_learning_summary_surfaces_enrichment_metrics() -> None:
    svc = _service()
    svc.learning_history.append(
        LearningReport(
            report_date="2026-07-01T00:00:00+00:00",
            agents_performance={},
            adjusted_weights={},
            total_trades_analyzed=2,
            overall_win_rate=50,
            recommendations=[],
            previous_weights={},
            changes_summary="No major changes",
            session_breakdown={"London / Europe Midday": {"pnl": 300, "count": 1, "wins": 1}},
            rr_efficiency={"sample": 2, "avg_actual_r": 1.0, "avg_planned_rr": 2.5, "rr_capture_pct": 40.0},
            news_proximity={"CAUTION": {"pnl": -100, "count": 1, "losses": 1}},
            regime_fit={"HIGH": {"pnl": 300, "count": 1}},
        )
    )

    text = svc.get_learning_summary()

    assert "RR efficiency" in text
    assert "actual +1.00R" in text
    assert "Best session" in text
    assert "Weak news bucket" in text
