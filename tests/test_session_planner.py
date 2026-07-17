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
    assert plan["session_bias"] == "SELL"
    assert plan["scenario_type"] == "STRUCTURE_CONTINUATION"
    assert plan["primary_entry_price"] == 4020.0
    assert plan["standby_entry_price"] == 4009.0
    assert plan["max_pending_orders_allowed"] == 2
    assert plan["planner_confidence"] >= 62
    assert plan["bias_sources"]
    assert plan["directional_alignment_count"] >= 2
    assert plan["expected_path"]
    assert plan["execution_preference"] in {"LADDER_PENDING", "SINGLE_PENDING", "NEAR_MARKET_WATCH"}
    assert plan["plan_narrative"]
    assert plan["primary_rationale"]
    assert service.latest_plan("XAU/USD")["plan_id"] == plan["plan_id"]


def test_session_planner_blocks_when_news_is_hard_block(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    results["news"] = {"can_trade": False, "market_status": "DANGER"}
    plan = service.build_plan(results)
    assert plan["plan_ready"] is False
    assert plan["plan_status"] == "BLOCKED"
    assert "news blocked" in str(plan["plan_reason"]).lower()


def test_session_planner_rejects_weak_primary_thesis(tmp_path: Path) -> None:
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
    assert plan["plan_ready"] is False
    assert "too weak" in str(plan["plan_reason"]).lower() or "below planner floor" in str(plan["plan_reason"]).lower()


def test_session_planner_blocks_when_bias_alignment_is_missing(tmp_path: Path) -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    results = _results()
    results["daily_bias"] = {"bias": "BULLISH", "confidence": 91}
    results["news"]["macro_direction"] = {"bias": "BULLISH_GOLD", "confidence": 70}
    results["macro_fundamental"]["macro_direction"] = {"bias": "BULLISH_GOLD", "confidence": 70}
    results["smc"]["market_structure"] = {"trend": "BULLISH", "structure_quality": "STRONG"}
    results["smc"]["liquidity"]["recent_sweep"] = {"occurred": False, "type": None}
    plan = service.build_plan(results)
    assert plan["plan_ready"] is False
    assert "alignment" in str(plan["plan_reason"]).lower()
