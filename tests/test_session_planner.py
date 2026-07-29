from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.session_planner import SessionPlannerService


def _candidate(
    role: str,
    *,
    direction: str = "SELL",
    entry_price: float,
    stop_loss: float,
    target_price: float,
    setup_type: str = "STRUCTURE_CONTINUATION",
    setup_state: str = "POI_MARKED",
    poi_type: str = "order_block",
    dominance: float = 68,
    return_probability: float = 61,
    quality_score: float = 78,
    trigger_score: float = 58,
) -> dict:
    return {
        "id": f"CAND::{role}",
        "state_key": f"STATE::{role}",
        "direction": direction,
        "setup_type": setup_type,
        "setup_state": setup_state,
        "selection_role": role,
        "selection_rank": 1 if role == "PRIMARY" else 2,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "target_liquidity": target_price,
        "poi_type": poi_type,
        "poi_zone": {"top": entry_price + 2.0, "bottom": entry_price - 2.0},
        "poi_low": entry_price - 2.0,
        "poi_high": entry_price + 2.0,
        "poi_quality_score": quality_score,
        "return_probability_score": return_probability,
        "thesis_dominance_score": dominance,
        "trigger_state": "AT_POI_WAIT_TRIGGER",
        "trigger_score": trigger_score,
        "trigger_ready": False,
        "expected_revisit_window": "NEAR",
        "displacement_score": 12.0,
        "quality_score": quality_score,
        "quality_grade": "B",
        "details": {"poi": {"mitigation_status": "FRESH"}},
    }


def _results() -> dict:
    return {
        "symbol": "XAU/USD",
        "current_price": 4012.0,
        "session": {"trading_allowed": True, "allow_signals": True, "current_session": "London + New York Afternoon", "session_quality": "HIGH"},
        "news": {"can_trade": True, "market_status": "SAFE", "macro_direction": {"bias": "BEARISH_GOLD", "confidence": 64}},
        "macro_fundamental": {"macro_direction": {"bias": "BEARISH_GOLD", "confidence": 64}},
        "daily_bias": {"bias": "BEARISH", "confidence": 95},
        "technical": {"signal": "SELL", "confidence": 82},
        "classical": {"signal": "SELL", "confidence": 80},
        "price_action": {"signal": "WAIT", "confidence": 40},
        "multitimeframe": {"signal": "WAIT", "confidence": 35},
        "smc": {
            "signal": "SELL",
            "confidence": 84,
            "day_archetype": "CONTINUATION_AFTER_SWEEP_DAY",
            "day_archetype_confidence": 78,
            "day_archetype_reason": "bearish structure + buy-side sweep + premium mitigation",
            "preferred_execution_family": "MITIGATION_LADDER",
            "zone": "PREMIUM",
            "dealing_range": {"high": 4048.0, "low": 3970.0, "midpoint": 4009.0, "current_position_pct": 0.72},
            "market_structure": {"trend": "BEARISH", "structure_quality": "STRONG"},
            "liquidity": {
                "recent_sweep": {"occurred": True, "type": "buy_side", "reference_type": "session_high", "confirmation": "STRONG"},
                "previous_day_levels": {"high": 4046.0, "low": 3984.0},
                "session_liquidity": {"label": "London + New York Afternoon", "high": 4038.0, "low": 3992.0},
            },
            "setup_candidates": [
                _candidate("PRIMARY", entry_price=4020.0, stop_loss=4044.0, target_price=3965.0, setup_state="ENTRY_ARMED"),
                _candidate("STANDBY", entry_price=4009.0, stop_loss=4030.0, target_price=3950.0, dominance=60, return_probability=54, quality_score=74),
            ]
        },
    }


def test_session_planner_builds_ready_primary_and_standby_plan(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    plan = service.build_plan(_results())
    assert plan["plan_ready"] is True
    assert plan["plan_status"] == "READY"
    assert plan["planner_source"] == "setup_candidates"
    assert plan["authority_state"] == "CONFIRMED"
    assert plan["session_bias"] == "SELL"
    assert plan["scenario_type"] == "STRUCTURE_CONTINUATION"
    assert plan["primary_entry_price"] == 4020.0
    assert plan["standby_entry_price"] == 4009.0
    assert plan["max_pending_orders_allowed"] == 2
    assert plan["planner_confidence"] >= 62
    assert plan["bias_sources"]
    assert plan["directional_alignment_count"] >= 2
    assert plan["expected_path"]
    assert plan["day_objective"] in {
        "UPSIDE_CONTINUATION_AFTER_SWEEP",
        "DOWNSIDE_CONTINUATION_AFTER_SWEEP",
        "DISCOUNT_REVERSAL_LONG",
        "PREMIUM_REVERSAL_SHORT",
        "UPSIDE_SESSION_BIAS",
        "DOWNSIDE_SESSION_BIAS",
    }
    assert plan["day_objective_label"]
    assert plan["day_archetype"] is not None
    assert plan["preferred_execution_family"] is not None
    assert plan["poi_classification"] in {"EXTREME_POI", "HIGH_PROBABILITY_POI", "STANDARD_POI"}
    assert isinstance(plan["extreme_poi"], bool)
    assert plan["execution_preference"] in {"LADDER_PENDING", "SINGLE_PENDING", "NEAR_MARKET_WATCH", "SPLIT_EXECUTION_WATCH"}
    assert plan["plan_narrative"]
    assert plan["primary_rationale"]
    assert plan["manual_plan"]["headline"] in {"SELL DAY MAP", "BUY DAY MAP"}
    assert plan["manual_plan"]["objective_label"] == plan["day_objective_label"]
    assert plan["manual_plan"]["confirmation_items"]
    assert plan["manual_plan"]["missed_area_plan"]
    assert plan["manual_plan"]["map_change_plan"]
    assert service.latest_plan("XAU/USD")["plan_id"] == plan["plan_id"]


def test_session_planner_prefers_mitigation_continuation_buy_over_countertrend_sell(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = {
        "symbol": "XAU/USD",
        "current_price": 4115.0,
        "session": {"trading_allowed": True, "allow_signals": True, "current_session": "London / Europe Midday", "session_quality": "HIGH"},
        "news": {"can_trade": True, "market_status": "SAFE", "macro_direction": {"bias": "BULLISH_GOLD", "confidence": 68}},
        "macro_fundamental": {"macro_direction": {"bias": "BULLISH_GOLD", "confidence": 68}},
        "daily_bias": {"bias": "BULLISH", "confidence": 91},
        "technical": {"signal": "BUY", "confidence": 82},
        "classical": {"signal": "BUY", "confidence": 79},
        "smc": {
            "signal": "BUY",
            "confidence": 84,
            "zone": "DISCOUNT",
            "dealing_range": {"high": 4141.0, "low": 4066.0, "midpoint": 4103.5, "current_position_pct": 0.66},
            "market_structure": {"trend": "BULLISH", "structure_quality": "STRONG"},
            "liquidity": {
                "recent_sweep": {"occurred": True, "type": "sell_side", "reference_type": "session_low", "confirmation": "STRONG"},
                "previous_day_levels": {"high": 4141.0, "low": 4066.0},
                "session_liquidity": {"label": "London / Europe Midday", "high": 4130.0, "low": 4077.0},
            },
            "setup_candidates": [
                _candidate(
                    "PRIMARY",
                    direction="SELL",
                    entry_price=4134.0,
                    stop_loss=4144.0,
                    target_price=4090.0,
                    setup_type="LIQUIDITY_REVERSAL",
                    dominance=72,
                    return_probability=63,
                    quality_score=79,
                    trigger_score=58,
                ),
                _candidate(
                    "STANDBY",
                    direction="BUY",
                    entry_price=4087.0,
                    stop_loss=4066.0,
                    target_price=4130.0,
                    setup_type="STRUCTURE_CONTINUATION",
                    dominance=66,
                    return_probability=62,
                    quality_score=77,
                    trigger_score=56,
                ),
            ],
        },
        "price_action": {"signal": "BUY", "confidence": 78},
        "multitimeframe": {"signal": "BUY", "confidence": 86},
    }
    plan = service.build_plan(results)
    assert plan["plan_ready"] is True
    assert plan["session_bias"] == "BUY"
    assert plan["primary_entry_price"] == 4087.0
    assert plan["day_objective"] == "UPSIDE_CONTINUATION_AFTER_SWEEP"
    assert plan["objective_alignment"] == "ALIGNED_WITH_MARKET_OBJECTIVE"
    assert plan["manual_plan"]["execution_priority_label"] == "Single mapped execution"


def test_counter_objective_reversal_without_rejection_proof_becomes_watch_only(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = {
        "symbol": "XAU/USD",
        "current_price": 4115.0,
        "session": {"trading_allowed": True, "allow_signals": True, "current_session": "London / Europe Midday", "session_quality": "HIGH"},
        "news": {"can_trade": True, "market_status": "SAFE", "macro_direction": {"bias": "BULLISH_GOLD", "confidence": 68}},
        "macro_fundamental": {"macro_direction": {"bias": "BULLISH_GOLD", "confidence": 68}},
        "daily_bias": {"bias": "BULLISH", "confidence": 91},
        "technical": {"signal": "SELL", "confidence": 81},
        "classical": {"signal": "SELL", "confidence": 79},
        "price_action": {"signal": "SELL", "confidence": 78},
        "multitimeframe": {"signal": "BUY", "confidence": 86},
        "smc": {
            "signal": "SELL",
            "confidence": 84,
            "zone": "PREMIUM",
            "dealing_range": {"high": 4141.0, "low": 4066.0, "midpoint": 4103.5, "current_position_pct": 0.66},
            "market_structure": {"trend": "BULLISH", "structure_quality": "STRONG"},
            "liquidity": {
                "recent_sweep": {"occurred": True, "type": "sell_side", "reference_type": "session_low", "confirmation": "STRONG"},
                "previous_day_levels": {"high": 4141.0, "low": 4066.0},
                "session_liquidity": {"label": "London / Europe Midday", "high": 4130.0, "low": 4077.0},
            },
            "setup_candidates": [
                _candidate(
                    "PRIMARY",
                    direction="SELL",
                    entry_price=4134.0,
                    stop_loss=4144.0,
                    target_price=4090.0,
                    setup_type="LIQUIDITY_REVERSAL",
                    setup_state="POI_MARKED",
                    dominance=74,
                    return_probability=64,
                    quality_score=80,
                    trigger_score=58,
                ),
            ],
        },
    }
    plan = service.build_plan(results)
    assert plan["plan_ready"] is False
    assert plan["plan_status"] == "WATCH_ONLY"
    assert "reversal proof" in str(plan["plan_reason"]).lower()


def test_counter_objective_reversal_with_rejection_proof_can_stay_ready(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = {
        "symbol": "XAU/USD",
        "current_price": 4115.0,
        "session": {"trading_allowed": True, "allow_signals": True, "current_session": "London / Europe Midday", "session_quality": "HIGH"},
        "news": {"can_trade": True, "market_status": "SAFE", "macro_direction": {"bias": "NEUTRAL", "confidence": 50}},
        "macro_fundamental": {"macro_direction": {"bias": "NEUTRAL", "confidence": 50}},
        "daily_bias": {"bias": "NEUTRAL", "confidence": 50},
        "technical": {"signal": "SELL", "confidence": 81},
        "classical": {"signal": "SELL", "confidence": 79},
        "price_action": {"signal": "SELL", "confidence": 78},
        "multitimeframe": {"signal": "WAIT", "confidence": 41},
        "smc": {
            "signal": "SELL",
            "confidence": 84,
            "zone": "PREMIUM",
            "dealing_range": {"high": 4141.0, "low": 4066.0, "midpoint": 4103.5, "current_position_pct": 0.66},
            "market_structure": {"trend": "BULLISH", "structure_quality": "STRONG"},
            "liquidity": {
                "recent_sweep": {"occurred": True, "type": "sell_side", "reference_type": "session_low", "confirmation": "STRONG"},
                "previous_day_levels": {"high": 4141.0, "low": 4066.0},
                "session_liquidity": {"label": "London / Europe Midday", "high": 4130.0, "low": 4077.0},
            },
            "setup_candidates": [
                _candidate(
                    "PRIMARY",
                    direction="SELL",
                    entry_price=4134.0,
                    stop_loss=4144.0,
                    target_price=4090.0,
                    setup_type="LIQUIDITY_REVERSAL",
                    setup_state="ENTRY_ARMED",
                    dominance=74,
                    return_probability=64,
                    quality_score=80,
                    trigger_score=70,
                ),
            ],
        },
    }
    results["smc"]["setup_candidates"][0]["trigger_state"] = "REJECTION_CONFIRMED"
    results["smc"]["setup_candidates"][0]["trigger_ready"] = True
    plan = service.build_plan(results)
    assert plan["plan_ready"] is True
    assert plan["session_bias"] == "SELL"
    assert plan["objective_alignment"] == "COUNTER_OBJECTIVE_REVERSAL_CONFIRMED"
    assert plan["execution_preference"] == "SINGLE_PENDING"
    assert plan["standby_poi"] is None


def test_session_planner_blocks_when_news_is_hard_block(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    results["news"] = {"can_trade": False, "market_status": "DANGER"}
    plan = service.build_plan(results)
    assert plan["plan_ready"] is False
    assert plan["plan_status"] == "BLOCKED"
    assert "news blocked" in str(plan["plan_reason"]).lower()


def test_session_planner_falls_back_to_day_map_when_primary_candidate_is_too_weak(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    results["smc"]["setup_candidates"] = [
        _candidate(
            "PRIMARY",
            entry_price=4020.0,
            stop_loss=4044.0,
            target_price=3965.0,
            dominance=44,
            return_probability=36,
            quality_score=66,
            trigger_score=40,
            setup_state="DETECTED",
        )
    ]
    plan = service.build_plan(results)
    assert plan["plan_ready"] is True
    assert plan["planner_source"] == "fallback_day_map"
    assert plan["authority_direction"] == "SELL"


def test_session_planner_builds_fallback_day_map_when_structured_candidates_are_missing(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    results["smc"]["setup_candidates"] = []
    plan = service.build_plan(results)
    assert plan["plan_ready"] is True
    assert plan["planner_source"] == "fallback_day_map"
    assert plan["authority_state"] == "CONFIRMED"
    assert plan["authority_direction"] == "SELL"
    assert plan["primary_poi"]["poi_type"] == "extreme_day_map_zone"
    assert plan["poi_classification"] in {"EXTREME_POI", "HIGH_PROBABILITY_POI"}
    assert plan["primary_entry_zone"]["low"] < plan["primary_entry_zone"]["high"]
    assert plan["plan_narrative"]
    assert plan["expected_path"]


def test_session_planner_blocks_when_day_map_authority_is_conflicted(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    results["smc"]["setup_candidates"] = []
    results["daily_bias"] = {"bias": "BULLISH", "confidence": 91}
    results["news"]["macro_direction"] = {"bias": "BEARISH_GOLD", "confidence": 70}
    results["macro_fundamental"]["macro_direction"] = {"bias": "BEARISH_GOLD", "confidence": 70}
    results["smc"]["market_structure"] = {"trend": "RANGING", "structure_quality": "STRONG"}
    results["smc"]["liquidity"]["recent_sweep"] = {"occurred": False, "type": None}
    plan = service.build_plan(results)
    assert plan["plan_ready"] is False
    assert plan["authority_state"] == "CONFLICTED"
    assert "conflicted" in str(plan["plan_reason"]).lower()


def test_execution_readiness_constrains_fallback_when_support_is_missing() -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    primary = _candidate("PRIMARY", direction="BUY", entry_price=4022.0, stop_loss=3982.0, target_price=4112.0)
    readiness = service._execution_readiness(
        planner_source="fallback_day_map",
        direction="BUY",
        primary=primary,
        standby=None,
        all_results={
            "technical": {"signal": "WAIT", "confidence": 43},
            "classical": {"signal": "WAIT", "confidence": 30},
            "price_action": {"signal": "WAIT", "confidence": 29},
            "multitimeframe": {"signal": "WAIT", "confidence": 48},
            "smc": {"signal": "WAIT", "confidence": 34},
        },
        preferred_execution_family="SINGLE_PENDING",
        macro={"bias": "BEARISH_GOLD", "confidence": 66},
    )
    assert readiness["state"] == "MAP_ONLY"
    assert "fallback map has insufficient execution support" in readiness["reason"].lower()


def test_session_planner_breaks_authority_tie_with_structure_and_sweep_objective(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    results["smc"]["setup_candidates"] = []
    results["daily_bias"] = {"bias": "NEUTRAL", "confidence": 50}
    results["news"]["macro_direction"] = {"bias": "BEARISH_GOLD", "confidence": 70}
    results["macro_fundamental"]["macro_direction"] = {"bias": "BEARISH_GOLD", "confidence": 70}
    results["smc"]["zone"] = "DISCOUNT"
    results["smc"]["market_structure"] = {"trend": "BULLISH", "structure_quality": "STRONG"}
    results["smc"]["liquidity"]["recent_sweep"] = {"occurred": True, "type": "sell_side", "reference_type": "session_low", "confirmation": "STRONG"}
    plan = service.build_plan(results)
    assert plan["authority_state"] == "CONFIRMED"
    assert plan["authority_direction"] == "BUY"
    assert "market objective" in str(plan["authority_reason"]).lower()
    assert "conflicted" not in str(plan["plan_reason"]).lower()


def test_session_planner_can_use_reversal_watch_as_authority_source(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    results["smc"]["setup_candidates"] = []
    results["daily_bias"] = {"bias": "NEUTRAL", "confidence": 50}
    results["news"]["macro_direction"] = {"bias": "NEUTRAL", "confidence": 50}
    results["macro_fundamental"]["macro_direction"] = {"bias": "NEUTRAL", "confidence": 50}
    results["smc"]["zone"] = "DISCOUNT"
    results["smc"]["market_structure"] = {"trend": "RANGING", "structure_quality": "STRONG"}
    results["smc"]["liquidity"]["recent_sweep"] = {"occurred": False, "type": None}
    results["reversal_watch"] = {"direction": "BUY", "active": True}
    plan = service.build_plan(results)
    assert plan["authority_state"] == "WEAK" or plan["authority_state"] == "CONFIRMED"
    assert plan["authority_direction"] == "BUY"


def test_session_planner_classifies_extreme_poi_when_alignment_is_strong(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    plan = service.build_plan(results)
    assert plan["plan_ready"] is True
    assert plan["poi_classification"] in {"EXTREME_POI", "HIGH_PROBABILITY_POI"}
    if plan["poi_classification"] == "EXTREME_POI":
        assert plan["extreme_poi"] is True
        assert plan["execution_preference"] == "SPLIT_EXECUTION_WATCH"


def test_session_planner_blocks_when_main_zone_is_too_wide(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True, "max_primary_zone_width_points": 260}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    # A SELL needs its stop above entry and its target below it. The previous
    # values were inverted, so execution refused the leg outright and the
    # width check was never the thing being exercised.
    results["smc"]["setup_candidates"] = [
        _candidate("PRIMARY", entry_price=4051.18, stop_loss=4066.18, target_price=3960.0, poi_type="extreme_day_map_zone")
    ]
    results["smc"]["setup_candidates"][0]["poi_zone"] = {"top": 4051.18, "bottom": 3998.72}
    plan = service.build_plan(results)
    assert plan["plan_ready"] is False
    assert plan["plan_status"] == "WATCH_ONLY"
    assert "too wide" in str(plan["plan_reason"]).lower()


def test_session_planner_removes_add_area_when_it_overlaps_main(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    # A SELL needs its stop above entry and its target below it. The original
    # values were inverted; the old levels maths never read them because it
    # derived targets from the risk floor instead.
    results["smc"]["setup_candidates"] = [
        _candidate("PRIMARY", entry_price=4020.0, stop_loss=4060.0, target_price=3930.0),
        _candidate("STANDBY", entry_price=4021.0, stop_loss=4061.0, target_price=3929.0),
    ]
    results["smc"]["setup_candidates"][1]["poi_zone"] = {"top": 4022.0, "bottom": 4019.0}
    plan = service.build_plan(results)
    assert plan["plan_ready"] is True
    assert plan["standby_poi"] is None


def test_session_planner_keeps_same_box_ladder_standby_even_when_overlapping(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    primary = _candidate("PRIMARY", direction="BUY", entry_price=4087.0, stop_loss=4064.5, target_price=4130.0)
    standby = _candidate("STANDBY", direction="BUY", entry_price=4077.0, stop_loss=4064.5, target_price=4130.0)
    primary["poi_zone"] = {"top": 4087.43, "bottom": 4077.50}
    standby["poi_zone"] = {"top": 4077.49, "bottom": 4067.05}
    primary["details"] = {"poi": {"mitigation_status": "FRESH"}, "selection": {"same_box_ladder": True, "ladder_parent_id": "BOX::1", "ladder_leg": "PRIMARY"}}
    standby["details"] = {"poi": {"mitigation_status": "FRESH"}, "selection": {"same_box_ladder": True, "ladder_parent_id": "BOX::1", "ladder_leg": "STANDBY"}}
    results["daily_bias"] = {"bias": "BULLISH", "confidence": 91}
    results["news"]["macro_direction"] = {"bias": "BULLISH_GOLD", "confidence": 70}
    results["macro_fundamental"]["macro_direction"] = {"bias": "BULLISH_GOLD", "confidence": 70}
    results["technical"] = {"signal": "BUY", "confidence": 82}
    results["classical"] = {"signal": "BUY", "confidence": 80}
    results["price_action"] = {"signal": "WAIT", "confidence": 40}
    results["multitimeframe"] = {"signal": "WAIT", "confidence": 35}
    results["smc"]["signal"] = "BUY"
    results["smc"]["confidence"] = 84
    results["smc"]["zone"] = "DISCOUNT"
    results["smc"]["market_structure"] = {"trend": "BULLISH", "structure_quality": "STRONG"}
    results["smc"]["liquidity"]["recent_sweep"] = {"occurred": True, "type": "sell_side", "reference_type": "session_low", "confirmation": "STRONG"}
    results["smc"]["setup_candidates"] = [primary, standby]
    plan = service.build_plan(results)
    assert plan["plan_ready"] is True
    assert plan["standby_poi"] is not None
    assert plan["same_box_ladder"] is True
    assert plan["manual_plan"]["same_box_ladder"] is True
    assert "Same-box ladder" in plan["manual_plan"]["execution_priority_label"]


def test_session_planner_blocks_when_main_rr_is_too_low(tmp_path: Path) -> None:
    """The planner may demand more reward than execution's own floor.

    The leg below is priceable -- execution accepts it at 2.0R -- so this
    exercises the planner's own RR gate rather than reaching it by accident
    through a leg execution had already rejected with rr = 0.
    """
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True, "min_main_rr_for_ready": 2.5}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    results["smc"]["setup_candidates"] = [
        _candidate("PRIMARY", entry_price=4051.18, stop_loss=4066.18, target_price=4021.18, poi_type="extreme_day_map_zone")
    ]
    results["smc"]["setup_candidates"][0]["poi_zone"] = {"top": 4051.18, "bottom": 4048.72}
    plan = service.build_plan(results)
    assert plan["plan_ready"] is False
    assert plan["plan_status"] == "WATCH_ONLY"
    assert "rr" in str(plan["plan_reason"]).lower()


# ─── Archetype conviction ──────────────────────────────────────────────────


def _conviction(archetype: str, confidence: float, family: str, setup_type: str, **cfg):
    planner_cfg = {"enabled": True}
    if cfg:
        planner_cfg["archetype_conviction"] = cfg
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": planner_cfg})
    return service._archetype_conviction(
        archetype=archetype,
        archetype_confidence=confidence,
        preferred_execution_family=family,
        primary={"setup_type": setup_type},
    )


def test_high_conviction_archetype_earns_the_full_ladder() -> None:
    result = _conviction("CONTINUATION_AFTER_SWEEP_DAY", 90.0, "MITIGATION_LADDER", "LIQUIDITY_REVERSAL")
    assert result["level"] == "HIGH"
    assert result["allow_execution"] is True
    assert result["allow_add_leg"] is True


def test_low_conviction_archetype_blocks_execution_entirely() -> None:
    """A day the system cannot classify with conviction is a map, not a trade."""
    result = _conviction("CONTINUATION_AFTER_SWEEP_DAY", 58.0, "MITIGATION_LADDER", "LIQUIDITY_REVERSAL")
    assert result["level"] == "LOW"
    assert result["allow_execution"] is False
    assert result["allow_add_leg"] is False


def test_setup_contradicting_the_archetype_is_capped_at_main_leg() -> None:
    """High confidence is not enough when the setup argues with the archetype."""
    result = _conviction("CONTINUATION_AFTER_SWEEP_DAY", 80.0, "MITIGATION_LADDER", "RANGE_FADE")
    assert result["level"] == "MEDIUM"
    assert result["family_aligned"] is False
    assert result["allow_execution"] is True
    assert result["allow_add_leg"] is False


def test_unmapped_execution_family_is_not_held_against_the_plan() -> None:
    result = _conviction("SOME_NEW_DAY", 85.0, "BRAND_NEW_FAMILY", "LIQUIDITY_REVERSAL")
    assert result["family_aligned"] is True
    assert result["level"] == "HIGH"


def test_archetype_conviction_thresholds_are_configurable() -> None:
    strict = _conviction(
        "CONTINUATION_AFTER_SWEEP_DAY", 80.0, "MITIGATION_LADDER", "LIQUIDITY_REVERSAL",
        enabled=True, high_conviction_confidence=85, medium_conviction_confidence=70,
    )
    assert strict["level"] == "MEDIUM"


def test_archetype_conviction_can_be_disabled() -> None:
    off = _conviction(
        "CONTINUATION_AFTER_SWEEP_DAY", 10.0, "MITIGATION_LADDER", "RANGE_FADE",
        enabled=False,
    )
    assert off["level"] == "HIGH"
    assert off["allow_add_leg"] is True


def test_plan_execution_scales_with_archetype_conviction(tmp_path: Path) -> None:
    """End to end: the same map yields ladder, main-only, or nothing."""
    def _plan(confidence: float):
        service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
        service.storage_path = tmp_path / f"plans_{confidence}.json"
        results = _results()
        results["smc"]["day_archetype_confidence"] = confidence
        return service.build_plan(results, persist=False)

    high = _plan(90)
    assert high["plan_ready"] is True
    assert high["archetype_conviction"]["level"] == "HIGH"
    assert high["standby_poi"] is not None

    medium = _plan(65)
    assert medium["plan_ready"] is True
    assert medium["archetype_conviction"]["level"] == "MEDIUM"
    assert medium["standby_poi"] is None, "medium conviction must not earn an add leg"

    low = _plan(40)
    assert low["plan_ready"] is False
    assert low["plan_status"] == "WATCH_ONLY"
    assert "conviction" in str(low["plan_reason"]).lower()


def test_plan_carries_the_liquidity_map_to_execution(tmp_path: Path) -> None:
    """Execution reads TP1/TP2 from these pools, so the plan must ship them.

    _compact_candidate dropped `details` entirely, so every leg reached the
    target resolver with only the single nearest level. Plans published
    normally but produced no orders: with no qualifying second target the leg
    was rejected for insufficient reward.
    """
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "plans.json"
    results = _results()
    results["smc"]["liquidity"]["sell_side"] = [3990.0, 3965.0, 3940.0]
    for candidate in results["smc"]["setup_candidates"]:
        candidate.setdefault("details", {})["liquidity"] = results["smc"]["liquidity"]

    plan = service.build_plan(results, persist=False)
    assert plan["plan_ready"] is True
    liquidity = ((plan["primary_poi"].get("details") or {}).get("liquidity") or {})
    assert liquidity.get("sell_side") == [3990.0, 3965.0, 3940.0]


def test_compact_candidate_keeps_liquidity_but_drops_diagnostics() -> None:
    """Only the liquidity block travels; the rest of details stays behind."""
    service = SessionPlannerService({"symbol": "XAU/USD"})
    compact = service._compact_candidate({
        "id": "C1",
        "entry_price": 4000.0,
        "details": {
            "liquidity": {"sell_side": [3990.0], "buy_side": []},
            "poi": {"mitigation_status": "FRESH"},
            "dealing_range": {"high": 4050.0},
        },
    })
    assert compact["details"] == {"liquidity": {"sell_side": [3990.0], "buy_side": []}}
    assert "poi" not in compact["details"]


def test_planner_levels_match_execution_levels_exactly() -> None:
    """The published map must be the order that gets placed.

    These were two separate implementations. The planner widened the stop to
    the fixed floor and derived targets from ATR multiples, while execution had
    moved to liquidity-derived targets and a scaled floor. The map advertised
    READY with invented levels and execution then refused the same leg, so
    plans were published and no orders ever appeared.
    """
    import json as _json
    from pathlib import Path as _Path
    from scripts.run_analysis import _planner_trade_levels

    config = _json.loads((_Path(__file__).resolve().parents[1] / "config.json").read_text())
    service = SessionPlannerService(config)
    candidate = {"details": {"liquidity": {"sell_side": [4064.74, 4030.0, 3985.15]}}}
    args = dict(direction="SELL", entry_price=4075.15, stop_loss=4079.0,
                target_price=4064.74, symbol="XAU/USD")

    planned = service._execution_levels(candidate=candidate, **args)
    executed = _planner_trade_levels(config, candidate=candidate, **args)

    assert planned["stop_loss"] == executed["stop_loss"]
    assert planned["tp1"] == executed["tp1"]
    assert planned["tp2"] == executed["tp2"]
    assert planned["rr_ratio"] == executed["rr"]
    # TP2 stays a real pool rather than a ratio-derived number. TP1 no longer
    # takes the 4064.74 pool: at 0.69R against the floored stop it sits inside
    # normal noise, and touching it arms the breakeven stop before the trade
    # has travelled. It is skipped in favour of a target worth acting on.
    assert planned["tp2"] == 4030.0
    assert planned["tp1"] != 4064.74
    tp1_rr = abs(4075.15 - planned["tp1"]) / abs(planned["stop_loss"] - 4075.15)
    assert tp1_rr >= 0.8


def test_planner_marks_a_leg_execution_would_reject() -> None:
    """A leg execution refuses must not be advertised as a ready map."""
    import json as _json
    from pathlib import Path as _Path

    config = _json.loads((_Path(__file__).resolve().parents[1] / "config.json").read_text())
    service = SessionPlannerService(config)
    levels = service._execution_levels(
        direction="SELL", entry_price=4075.15, stop_loss=4079.0,
        target_price=4064.74, symbol="XAU/USD", candidate={},
    )
    assert levels["reject_reason"]
    assert levels["rr_ratio"] == 0.0
