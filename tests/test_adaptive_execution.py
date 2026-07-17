from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.adaptive_execution import AdaptiveExecutionService


def _pending_trade(
    *,
    trade_id: str = "P1",
    scenario_id: str = "SCENARIO::A",
    role: str = "PRIMARY",
    entry_price: float = 4020.0,
    stop_loss: float = 4044.0,
    tp1: float = 4000.0,
    tp2: float = 3965.0,
    setup_type: str = "STRUCTURE_CONTINUATION",
    trigger_state: str = "AT_POI_WAIT_TRIGGER",
    trigger_score: float = 58.0,
    dominance: float = 68.0,
    return_probability: float = 60.0,
) -> dict:
    return {
        "id": trade_id,
        "symbol": "XAU/USD",
        "type": "SELL",
        "status": "PENDING",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "tp1": tp1,
        "tp2": tp2,
        "signal_snapshot": {
            "session_plan": {
                "scenario_id": scenario_id,
                "planner_confidence": 78.0,
            },
            "setup_context": {
                "scenario_id": scenario_id,
                "pending_plan_role": role,
                "selection_role": role,
                "setup_type": setup_type,
                "trigger_state": trigger_state,
                "trigger_score": trigger_score,
                "thesis_dominance_score": dominance,
                "return_probability_score": return_probability,
                "poi_quality_score": 80.0,
                "poi_type": "order_block",
                "poi_zone": {"top": entry_price + 2.0, "bottom": entry_price - 2.0},
                "details": {"recent_sweep": {"time": "2026-07-17T08:00:00+00:00"}},
            },
        },
    }


def _decision(
    *,
    current_price: float,
    scenario_id: str = "SCENARIO::A",
    setup_type: str = "STRUCTURE_CONTINUATION",
    trigger_state: str = "REJECTION_CONFIRMED",
    trigger_score: float = 74.0,
    dominance: float = 74.0,
    tp2: float = 3950.0,
    stop_loss: float = 4030.0,
    session_label: str = "London + New York Afternoon",
    execution_preference: str = "LADDER_PENDING",
) -> dict:
    return {
        "decision": "SELL",
        "symbol": "XAU/USD",
        "current_price": current_price,
        "session_info": {"current_session": session_label},
        "session_plan": {
            "scenario_id": scenario_id,
            "planner_confidence": 82.0,
            "standby_poi": {"entry_price": 4009.0},
            "session_label": session_label,
            "execution_preference": execution_preference,
        },
        "setup_context": {
            "scenario_id": scenario_id,
            "setup_type": setup_type,
            "setup_state": "ENTRY_ARMED",
            "pending_plan_role": "PRIMARY",
            "selection_role": "PRIMARY",
            "trigger_state": trigger_state,
            "trigger_score": trigger_score,
            "thesis_dominance_score": dominance,
            "return_probability_score": 66.0,
            "poi_quality_score": 86.0,
            "poi_type": "order_block",
            "poi_zone": {"top": current_price + 2.0, "bottom": current_price - 2.0},
            "details": {"recent_sweep": {"time": "2026-07-17T10:00:00+00:00"}},
        },
        "signal": {
            "type": "SELL",
            "entry": {
                "price": current_price,
                "current_price": current_price,
                "kind": "MARKET",
                "order_type": "SELL_MARKET",
                "distance_points": 0.0,
            },
            "stop_loss": stop_loss,
            "tp1": current_price - 20.0,
            "tp2": tp2,
            "rr_ratio": 2.0,
            "entry_kind": "MARKET",
            "order_type": "SELL_MARKET",
        },
        "reasons": ["confirmed by later path"],
    }


def _config() -> dict:
    return {
        "symbol": "XAU/USD",
        "risk_settings": {"min_rr_ratio": 1.5},
        "adaptive_execution": {
            "enabled": True,
            "keep_pending_max_move_points": 120,
            "keep_pending_max_target_progress_pct": 30,
            "promote_to_market_min_move_points": 60,
            "promote_to_market_max_move_points": 220,
            "max_target_progress_for_market_promotion_pct": 55,
            "min_remaining_rr_for_market_promotion": 1.5,
            "profiles": {
                "continuation": {
                    "keep_pending_max_move_points": 140,
                    "keep_pending_max_target_progress_pct": 35,
                    "promote_to_market_min_move_points": 70,
                    "promote_to_market_max_move_points": 240,
                    "max_target_progress_for_market_promotion_pct": 60,
                    "min_remaining_rr_for_market_promotion": 1.35,
                },
                "reversal": {
                    "keep_pending_max_move_points": 90,
                    "keep_pending_max_target_progress_pct": 20,
                    "promote_to_market_min_move_points": 45,
                    "promote_to_market_max_move_points": 150,
                    "max_target_progress_for_market_promotion_pct": 38,
                    "min_remaining_rr_for_market_promotion": 1.8,
                },
                "range": {
                    "keep_pending_max_move_points": 80,
                    "keep_pending_max_target_progress_pct": 18,
                    "promote_to_market_min_move_points": 35,
                    "promote_to_market_max_move_points": 120,
                    "max_target_progress_for_market_promotion_pct": 35,
                    "min_remaining_rr_for_market_promotion": 1.8,
                },
            },
            "session_adjustments": {
                "LONDON + NEW YORK AFTERNOON": {
                    "promote_to_market_max_move_points": 250,
                    "max_target_progress_for_market_promotion_pct": 62,
                },
                "LATE NEW YORK NIGHT": {
                    "promote_to_market_max_move_points": 180,
                    "max_target_progress_for_market_promotion_pct": 40,
                    "min_remaining_rr_for_market_promotion": 1.7,
                },
            },
        },
        "post_exit_revalidation": {
            "enabled": True,
            "new_poi_min_distance_points": 80,
            "min_state_progress_steps": 1,
            "min_trigger_score_improvement": 8,
            "min_displacement_improvement": 5,
            "min_dominance_improvement": 6,
        },
    }


def test_adaptive_execution_keeps_pending_when_move_is_small() -> None:
    service = AdaptiveExecutionService(_config())
    decision = _decision(current_price=4014.0, setup_type="STRUCTURE_CONTINUATION")
    review = service.review(decision, [_pending_trade(setup_type="STRUCTURE_CONTINUATION")])
    assert review["action"] == "KEEP_PENDING"
    assert review["calibration"]["profile"] == "continuation"


def test_adaptive_execution_promotes_to_market_when_move_is_confirmed_and_rr_remains_good() -> None:
    service = AdaptiveExecutionService(_config())
    pending = _pending_trade(entry_price=4009.0, stop_loss=4022.0, tp1=3990.0, tp2=3950.0, setup_type="STRUCTURE_CONTINUATION")
    decision = _decision(current_price=3992.0, tp2=3950.0, stop_loss=4022.0, setup_type="STRUCTURE_CONTINUATION")
    review = service.review(decision, [pending])
    assert review["action"] == "PROMOTE_TO_MARKET"
    assert review["calibration"]["profile"] == "continuation"
    adapted = review["decision"]
    assert adapted["signal"]["entry"]["kind"] == "MARKET"
    assert adapted["entry_mode"] == "adaptive_market_promotion"


def test_adaptive_execution_reversal_profile_is_stricter_than_continuation() -> None:
    service = AdaptiveExecutionService(_config())
    pending = _pending_trade(entry_price=4009.0, stop_loss=4022.0, tp1=3990.0, tp2=3950.0, setup_type="LIQUIDITY_REVERSAL")
    decision = _decision(current_price=3996.0, tp2=3950.0, stop_loss=4022.0, setup_type="LIQUIDITY_REVERSAL")
    review = service.review(decision, [pending])
    assert review["calibration"]["profile"] == "reversal"
    assert review["action"] in {"KEEP_PENDING", "NO_TRADE_MISSED_MOVE"}
    assert review["action"] != "PROMOTE_TO_MARKET"


def test_adaptive_execution_session_adjustment_changes_market_promotion_window() -> None:
    service = AdaptiveExecutionService(_config())
    pending = _pending_trade(entry_price=4018.0, stop_loss=4032.0, tp1=4000.0, tp2=3968.0, setup_type="STRUCTURE_CONTINUATION")
    decision_london = _decision(current_price=3998.0, tp2=3968.0, stop_loss=4032.0, setup_type="STRUCTURE_CONTINUATION", session_label="London + New York Afternoon")
    decision_late = _decision(current_price=3998.0, tp2=3968.0, stop_loss=4032.0, setup_type="STRUCTURE_CONTINUATION", session_label="Late New York Night")
    review_london = service.review(decision_london, [pending])
    review_late = service.review(decision_late, [pending])
    assert review_london["calibration"]["session_label"] == "London + New York Afternoon"
    assert review_late["calibration"]["session_label"] == "Late New York Night"
    assert review_london["calibration"]["promote_to_market_max_move_points"] > review_late["calibration"]["promote_to_market_max_move_points"]


def test_adaptive_execution_marks_missed_move_when_price_has_traveled_too_far() -> None:
    service = AdaptiveExecutionService(_config())
    decision = _decision(current_price=3970.0, tp2=3950.0, stop_loss=4030.0, setup_type="STRUCTURE_CONTINUATION")
    review = service.review(decision, [_pending_trade(entry_price=4020.0, stop_loss=4044.0, tp1=4000.0, tp2=3950.0, setup_type="STRUCTURE_CONTINUATION")])
    assert review["action"] == "NO_TRADE_MISSED_MOVE"


def test_adaptive_execution_replaces_with_continuation_when_new_thesis_is_materially_new() -> None:
    service = AdaptiveExecutionService(_config())
    old_pending = _pending_trade(
        scenario_id="SCENARIO::OLD",
        setup_type="LIQUIDITY_REVERSAL",
        trigger_state="AWAY_FROM_POI",
        trigger_score=42.0,
        dominance=58.0,
        return_probability=40.0,
        entry_price=4020.0,
        stop_loss=4044.0,
        tp2=3965.0,
    )
    decision = _decision(
        current_price=4000.0,
        scenario_id="SCENARIO::NEW",
        setup_type="STRUCTURE_CONTINUATION",
        trigger_state="REJECTION_CONFIRMED",
        trigger_score=74.0,
        dominance=72.0,
        tp2=3950.0,
        stop_loss=4030.0,
    )
    review = service.review(decision, [old_pending])
    assert review["action"] == "REPLACE_WITH_CONTINUATION"
