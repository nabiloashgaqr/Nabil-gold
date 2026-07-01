from scripts.backfill_trade_enrichment import compute_enrichment_updates


def test_backfill_computes_only_missing_enrichment_from_snapshot():
    trade = {
        "id": "TRADE_OLD",
        "type": "SELL",
        "symbol": "XAU/USD",
        "entry_price": 4020.0,
        "stop_loss": 4040.0,
        "tp2": 3960.0,
        "final_pnl": 300.0,
        "entry_time": "2026-07-01T11:30:00+00:00",
        "planned_rr": 9.9,  # existing value must not be overwritten
        "signal_snapshot": {
            "signal": {"rr_ratio": 3.0},
            "session_info": {"current_session": "London / Europe Midday", "session_quality": "HIGH"},
            "news_context": {"rule_based": {"market_status": "CAUTION", "risk_level": "HIGH"}},
            "market_context": {"technical_regime": {"volatility_regime": "HIGH", "trend_strength": "STRONG"}},
            "daily_bias": {"bias": "BEARISH"},
        },
    }

    updates = compute_enrichment_updates(trade, "Asia/Hebron")
    assert updates["planned_risk_points"] == 200.0
    assert updates["planned_tp2_points"] == 600.0
    assert "planned_rr" not in updates
    assert updates["session_label"] == "London / Europe Midday"
    assert updates["session_quality"] == "HIGH"
    assert updates["entry_day_of_week"] == "Wednesday"
    assert updates["entry_hour_local"] == 14
    assert updates["news_status_at_entry"] == "CAUTION"
    assert updates["news_risk_at_entry"] == "HIGH"
    assert updates["volatility_regime"] == "HIGH"
    assert updates["trend_strength"] == "STRONG"
    assert updates["daily_bias_at_entry"] == "BEARISH"
    assert updates["final_pnl_points"] == 300.0
