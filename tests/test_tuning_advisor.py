from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.tuning_advisor import TuningAdvisor


def _config() -> dict:
    return {
        "symbol": "XAU/USD",
        "order_execution": {"market_threshold_points": 30},
        "setup_memory": {"expire_after_hours": 12},
        "smc_engine": {"poi_preference": {"order_block_bonus": 10}},
        "strategy_profiles": {"liquidity_reversal": {"min_trigger_score": 70}},
        "learning": {"contextual_blend": 0.35},
        "session_planner": {"min_primary_dominance": 50, "min_plan_score": 62, "min_trigger_score": 40, "min_authority_alignment_count": 2},
        "split_execution": {"starter_risk_share": 0.4, "add_on_risk_share": 0.6},
    }


def test_tuning_advisor_recommends_fill_and_entry_lag_adjustments() -> None:
    report = {
        "verdict": "REQUIRES_MORE_REFINEMENT",
        "scorecard": {"benchmark_score": 40, "overlap_score": 30, "execution_score": 35, "not_filled_ratio": 0.52},
        "benchmark": {
            "comparison": {"win_rate_delta": -5.0, "net_points_delta": -120.0, "profit_factor_delta": -0.4},
            "variants": {"current_engine": {"summary": {"total_candidates": 20, "total_trades": 8, "not_filled": 12}}},
        },
        "analyst_overlap": {
            "labels_considered": 12,
            "match_rate_pct": 35.0,
            "coverage_rate_pct": 45.0,
            "avg_entry_distance_points": 88.0,
            "top_missed_reasons": [{"reason_code": "MISSED_ENTRY_TOO_FAR", "count": 4}],
        },
    }
    advice = TuningAdvisor(_config()).build_advice(report)
    patch = advice["config_patch"]
    assert patch["order_execution"]["market_threshold_points"] == 40
    assert patch["learning"]["contextual_blend"] < 0.35
    assert advice["operator_memo"]["priority"] in {"HIGH", "MEDIUM"}
    assert any("entry lag" in rec.lower() or "not-filled" in rec.lower() for rec in advice["recommendations"])


def test_tuning_advisor_recommends_timing_extension_for_timing_misses() -> None:
    report = {
        "verdict": "PROMISING_BUT_NEEDS_TUNING",
        "scorecard": {"benchmark_score": 62, "overlap_score": 50, "execution_score": 80, "not_filled_ratio": 0.15},
        "benchmark": {"comparison": {"win_rate_delta": 1.0, "net_points_delta": 40.0, "profit_factor_delta": 0.1}},
        "analyst_overlap": {
            "labels_considered": 8,
            "match_rate_pct": 48.0,
            "coverage_rate_pct": 52.0,
            "avg_entry_distance_points": 42.0,
            "top_missed_reasons": [{"reason_code": "MISSED_TIMING_WINDOW", "count": 3}],
        },
    }
    advice = TuningAdvisor(_config()).build_advice(report)
    assert advice["config_patch"]["setup_memory"]["expire_after_hours"] == 16


def test_tuning_advisor_can_hold_config_when_no_urgent_issue() -> None:
    report = {
        "verdict": "READY_FOR_STRUCTURED_TRIAL",
        "scorecard": {"benchmark_score": 80, "overlap_score": 75, "execution_score": 85, "not_filled_ratio": 0.10},
        "benchmark": {"comparison": {"win_rate_delta": 8.0, "net_points_delta": 150.0, "profit_factor_delta": 0.6}},
        "analyst_overlap": {
            "labels_considered": 10,
            "match_rate_pct": 70.0,
            "coverage_rate_pct": 90.0,
            "avg_entry_distance_points": 32.0,
            "top_missed_reasons": [{"reason_code": "PARTIAL_POI_MISMATCH", "count": 1}],
        },
    }
    advice = TuningAdvisor(_config()).build_advice(report)
    assert advice["config_patch"]
    # POI mismatch still triggers a small POI tuning hint even in good state.
    assert advice["config_patch"]["smc_engine"]["poi_preference"]["order_block_bonus"] == 12


def test_tuning_advisor_recommends_day_map_hierarchy_adjustments() -> None:
    report = {
        "verdict": "REQUIRES_MORE_REFINEMENT",
        "scorecard": {"benchmark_score": 48, "overlap_score": 55, "execution_score": 62, "governance_score": 58, "day_map_execution_score": 42, "not_filled_ratio": 0.18},
        "benchmark": {"comparison": {"win_rate_delta": -2.0, "net_points_delta": -40.0, "profit_factor_delta": -0.1}},
        "analyst_overlap": {"labels_considered": 8, "match_rate_pct": 58.0, "coverage_rate_pct": 75.0, "avg_entry_distance_points": 42.0, "top_missed_reasons": []},
        "day_map_execution": {
            "tracked_trade_count": 9,
            "scenario_metrics": {
                "main_worked_count": 2,
                "add_needed_count": 4,
                "starter_survived_alone_count": 5,
                "day_map_failed_count": 3,
                "map_changed_cancelled_count": 2,
            },
        },
    }
    advisor = TuningAdvisor(_config())
    advice = advisor.build_advice(report)
    patch = advice["config_patch"]
    assert patch["session_planner"]["min_primary_dominance"] > 50
    assert patch["split_execution"]["add_on_risk_share"] < 0.6
    assert patch["session_planner"]["min_plan_score"] > 62
    assert patch["session_planner"]["min_authority_alignment_count"] >= 2
    assert "operator_memo" in advice
    assert "main area weak" in advice["operator_memo"]["headline"]
    brief_text = advisor.format_management_brief(advice)
    memo_text = advisor.format_operator_memo(advice)
    assert "Management Brief" in brief_text
    assert "Operator Memo" in memo_text
    assert any("add areas" in rec.lower() or "starter" in rec.lower() or "day-map" in rec.lower() for rec in advice["recommendations"])
