from services.telegram_bot import TelegramService
from services.weekly_report import WeeklyReportService, WeeklyStats


def test_signal_phase6_compact_context_and_rr_line():
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    captured = {}
    service.send_message = lambda text, **_k: captured.setdefault("text", text) or True  # type: ignore[assignment]

    service.send_signal({
        "decision": "BUY",
        "symbol": "XAU/USD",
        "confidence": 82,
        "current_price": 4000,
        "trade_id": "TRADE_PHASE6",
        "session_info": {"current_session": "London / Europe Midday", "session_quality": "HIGH"},
        "news_context": {"rule_based": {"market_status": "CAUTION", "risk_level": "HIGH"}},
        "signal": {"type": "BUY", "entry": {"price": 4000}, "stop_loss": 3980, "tp1": 4030, "tp2": 4060, "rr_ratio": 3.0},
        "reasons": ["one", "two", "three", "four", "five"],
    })

    text = captured["text"]
    assert "Planned RR:</b> 3.0R" in text
    assert "Session: London / Europe Midday · HIGH" in text
    assert "News: CAUTION / HIGH" in text
    assert "… 2 more internal confirmations" in text
    assert text.count("• one") == 1
    assert "• four" not in text


def test_weekly_phase6_executive_report_contains_edge_quality():
    stats = WeeklyStats(
        week_start="2026-06-29",
        week_end="2026-07-05",
        total_trades=4,
        closed_trades=4,
        wins=2,
        losses=2,
        win_rate=50.0,
        net_pnl_points=400.0,
        avg_win_points=400.0,
        avg_loss_points=-200.0,
        largest_win_points=600.0,
        largest_loss_points=-200.0,
        profit_factor=2.0,
        best_day="2026-07-01",
        best_day_pnl=600.0,
        worst_day="2026-07-02",
        worst_day_pnl=-200.0,
        by_agent={"technical": {"count": 2, "pnl": 500.0, "win_rate_pct": 100}, "smc": {"count": 2, "pnl": -100.0, "win_rate_pct": 0}},
        by_session={"London / Europe Midday": {"count": 2, "pnl": 600.0, "win_rate_pct": 100}, "New York Evening": {"count": 2, "pnl": -200.0, "win_rate_pct": 0}},
        by_day={"2026-07-01": {"count": 2, "wins": 2, "losses": 0, "pnl": 600.0}, "2026-07-02": {"count": 2, "wins": 0, "losses": 2, "pnl": -200.0}},
        rr_efficiency={"sample": 4, "avg_actual_r": 1.0, "avg_planned_rr": 2.5, "rr_capture_pct": 40.0},
        regime_fit={"HIGH": {"count": 2, "pnl": 600.0, "win_rate_pct": 100}},
        news_proximity={"CAUTION": {"count": 2, "pnl": -200.0, "win_rate_pct": 0}},
    )
    service = WeeklyReportService({"weekly_report": {}}, database=object(), telegram=None)
    text = service._fallback_message(stats)

    assert "Weekly Executive Report" in text
    assert "EXECUTIVE SUMMARY" in text
    assert "EDGE QUALITY" in text
    assert "RR Capture: +1.00R vs planned 2.50R (40.0%)" in text
    assert "Best session: London / Europe Midday +600 pts" in text
    assert "NEXT WEEK ACTIONS" in text
