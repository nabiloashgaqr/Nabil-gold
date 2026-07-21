from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.tuning_advisor import TuningAdvisor


def _config() -> dict:
    return {
        "symbol": "XAU/USD",
        "strategy_profiles": {"liquidity_reversal": {"min_trigger_score": 60}},
        "order_execution": {"market_threshold_points": 30},
        "setup_memory": {"expire_after_hours": 12},
        "smc_engine": {"poi_preference": {"order_block_bonus": 10}},
        "learning": {"contextual_blend": 0.35},
        "session_planner": {"min_primary_dominance": 50, "min_plan_score": 62, "min_trigger_score": 40, "min_authority_alignment_count": 2},
        "split_execution": {"starter_risk_share": 0.4, "add_on_risk_share": 0.6},
    }


def test_tuning_advisor_does_not_emit_noop_trigger_recommendation() -> None:
    report = {
        "verdict": "REQUIRES_MORE_REFINEMENT",
        "scorecard": {"benchmark_score": 69.8, "overlap_score": 50.0, "execution_score": 81.8, "not_filled_ratio": 0.18},
        "benchmark": {
            "comparison": {"win_rate_delta": 30.15, "net_points_delta": 169.0, "profit_factor_delta": 0.29},
            "variants": {"current_engine": {"summary": {"total_candidates": 11, "total_trades": 9, "not_filled": 2}}},
        },
        "analyst_overlap": {
            "labels_considered": 10,
            "match_rate_pct": 30.0,
            "coverage_rate_pct": 40.0,
            "avg_entry_distance_points": 55.0,
            "top_missed_reasons": [{"reason_code": "MISSED_GENERIC", "count": 3}],
        },
    }
    advice = TuningAdvisor(_config()).build_advice(report)
    assert not any("from 60 to 60" in rec for rec in advice["recommendations"])
    assert not any(action.get("from") == 60 and action.get("to") == 60 for action in advice["actions"])
