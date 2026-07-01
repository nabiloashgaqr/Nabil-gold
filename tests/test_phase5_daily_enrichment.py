from scripts.run_daily_report import _daily_enrichment_summary, _quality_snapshot_lines
from services.llm_review import GeminiReviewService


def test_daily_enrichment_quality_snapshot_and_gemini_payload():
    trades = [
        {
            "type": "BUY",
            "symbol": "XAU/USD",
            "entry_price": 4000.0,
            "stop_loss": 3980.0,
            "tp2": 4060.0,
            "final_pnl": 600.0,
            "planned_risk_points": 200.0,
            "planned_rr": 3.0,
            "session_label": "London / Europe Midday",
            "news_status_at_entry": "SAFE",
            "volatility_regime": "HIGH",
            "status": "TP2_HIT",
        },
        {
            "type": "SELL",
            "symbol": "XAU/USD",
            "entry_price": 4050.0,
            "stop_loss": 4070.0,
            "tp2": 3990.0,
            "final_pnl": -200.0,
            "planned_risk_points": 200.0,
            "planned_rr": 2.0,
            "session_label": "New York Evening",
            "news_status_at_entry": "CAUTION",
            "volatility_regime": "LOW",
            "status": "SL_HIT",
        },
    ]

    enrichment = _daily_enrichment_summary(trades)
    assert enrichment["rr_efficiency"] == {
        "sample": 2,
        "avg_actual_r": 1.0,
        "avg_planned_rr": 2.5,
        "rr_capture_pct": 40.0,
    }
    assert enrichment["session_breakdown"]["London / Europe Midday"]["pnl"] == 600.0
    assert enrichment["session_breakdown"]["New York Evening"]["pnl"] == -200.0
    assert enrichment["news_proximity"]["CAUTION"]["losses"] == 1
    assert enrichment["regime_fit"]["HIGH"]["win_rate_pct"] == 100.0

    lines = _quality_snapshot_lines(enrichment)
    text = "\n".join(lines)
    assert "RR Capture" in text
    assert "+1.00R" in text
    assert "Best session: London / Europe Midday +600 pts" in text
    assert "News impact: CAUTION -200 pts" in text
    assert "Best regime: HIGH +600 pts" in text

    gemini = GeminiReviewService({})
    payload = gemini._compact_daily_report_payload({"report_date": "2026-07-01", **enrichment})
    assert payload["rr_efficiency"]["rr_capture_pct"] == 40.0
    assert payload["session_breakdown"]["London / Europe Midday"]["pnl"] == 600.0
    assert payload["news_proximity"]["CAUTION"]["pnl"] == -200.0
