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
        "smc": {
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
    results["smc"]["setup_candidates"] = [
        _candidate("PRIMARY", entry_price=4051.18, stop_loss=3998.72, target_price=4072.66, poi_type="extreme_day_map_zone")
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
    results["smc"]["setup_candidates"] = [
        _candidate("PRIMARY", entry_price=4020.0, stop_loss=3980.0, target_price=4110.0),
        _candidate("STANDBY", entry_price=4021.0, stop_loss=3981.0, target_price=4111.0),
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
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True, "min_main_rr_for_ready": 1.5}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    results["smc"]["setup_candidates"] = [
        _candidate("PRIMARY", entry_price=4051.18, stop_loss=3998.72, target_price=4072.66, poi_type="extreme_day_map_zone")
    ]
    results["smc"]["setup_candidates"][0]["poi_zone"] = {"top": 4051.18, "bottom": 4048.72}
    plan = service.build_plan(results)
    assert plan["plan_ready"] is False
    assert plan["plan_status"] == "WATCH_ONLY"
    assert "rr" in str(plan["plan_reason"]).lower()
