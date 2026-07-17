from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.backtesting import BacktestEngine, benchmark_backtests


def sample_candles(count: int = 260) -> list[dict]:
    start = datetime.now(timezone.utc) - timedelta(minutes=15 * count)
    price = 2320.0
    candles = []
    for i in range(count):
        drift = 0.25 if i < count // 2 else 0.18
        pullback = -0.65 if i % 19 == 0 else 0.0
        open_price = price
        close = price + drift + pullback
        high = max(open_price, close) + 1.2
        low = min(open_price, close) - 1.1
        candles.append(
            {
                "time": (start + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z"),
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": 1000 + i,
            }
        )
        price = close
    return candles


def base_config() -> dict:
    return {
        "symbol": "XAU/USD",
        "primary_timeframe": "15m",
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5, "min_sl_distance_points": 50},
        "signal_requirements": {"min_agents_agree": 2, "min_consensus_confidence": 70, "agent_min_confidence": 68},
        "agent_weights": {"technical": 0.20, "classical": 0.25, "smc": 0.20, "price_action": 0.20, "multitimeframe": 0.15},
        "order_execution": {
            "entry_style": "hybrid",
            "market_threshold_points": 30,
            "smart_entry": {"enabled": True, "fill_at": "mid", "zone_width_points": 50, "min_pullback_points": 60, "max_pullback_points": 350},
        },
    }


def test_backtest_report_has_setup_profile_breakdowns() -> None:
    engine = BacktestEngine(base_config(), sample_candles())
    report = engine.run(window=160, step=18, horizon=24, max_trades=12)
    summary = report["summary"]
    assert "by_setup_type" in summary
    assert "by_profile" in summary
    assert "by_management_profile" in summary
    assert "by_trigger_state" in summary
    assert "by_session" in summary
    assert "by_entry_kind" in summary
    assert "by_selection_role" in summary
    assert "pending_governance" in summary
    assert "planning" in summary
    assert "plan_ready_rate_pct" in summary
    assert "standby_ready_rate_pct" in summary
    assert "avg_return_probability_score" in summary
    assert "avg_thesis_dominance_score" in summary


def test_backtest_handles_unfilled_pending_orders() -> None:
    engine = BacktestEngine(base_config(), sample_candles())
    report = engine.run(window=160, step=18, horizon=3, max_trades=8)
    summary = report["summary"]
    assert "not_filled" in summary
    assert summary["total_candidates"] >= summary["total_trades"]


def test_benchmark_backtests_returns_variant_comparison() -> None:
    report = benchmark_backtests(base_config(), sample_candles(), window=160, step=18, horizon=20, max_trades=10)
    assert "variants" in report
    assert "current_engine" in report["variants"]
    assert "baseline_classic_market" in report["variants"]
    assert "comparison" in report
    assert "win_rate_delta" in report["comparison"]
    assert "primary_fill_rate_delta" in report["comparison"]
    assert "avg_dominance_delta" in report["comparison"]
    assert "plan_ready_rate_delta" in report["comparison"]
    assert "standby_ready_rate_delta" in report["comparison"]
    assert report["variants"]["current_engine"]["variant"] == "current_engine"
    assert report["variants"]["baseline_classic_market"]["variant"] == "baseline_classic_market"
