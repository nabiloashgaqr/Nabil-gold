from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

import scripts.run_analysis as ra
from services.database import DatabaseService
from utils.helpers import load_trades


class _Telegram:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_signal(self, decision: dict) -> bool:
        self.sent.append(decision)
        return True


def _db(tmp_path: Path) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None, "local_fallback_file": str(tmp_path / 'trades.json')}})
    db.local_path = tmp_path / 'trades.json'
    return db


def _candidate(role: str, entry: float, stop: float, target: float) -> dict:
    return {
        "id": f"CAND::{role}",
        "state_key": f"STATE::{role}",
        "direction": "SELL",
        "setup_type": "STRUCTURE_CONTINUATION",
        "setup_state": "POI_MARKED",
        "lead_agent": "smc",
        "selection_role": role,
        "selection_rank": 1 if role == "PRIMARY" else 2,
        "entry_price": entry,
        "stop_loss": stop,
        "target_price": target,
        "target_liquidity": target,
        "poi_type": "order_block",
        "poi_zone": {"top": entry + 2.0, "bottom": entry - 2.0},
        "poi_low": entry - 2.0,
        "poi_high": entry + 2.0,
        "poi_quality_score": 78,
        "return_probability_score": 60 if role == "PRIMARY" else 54,
        "thesis_dominance_score": 68 if role == "PRIMARY" else 60,
        "trigger_state": "AT_POI_WAIT_TRIGGER",
        "trigger_score": 58,
        "trigger_ready": False,
        "expected_revisit_window": "NEAR",
        "displacement_score": 12.0,
        "quality_score": 76,
        "quality_grade": "B",
    }


def _base_decision() -> dict:
    return {
        "decision": "SELL",
        "symbol": "XAU/USD",
        "current_price": 3992.76,
        "confidence": 78,
        "agent_details": {
            "technical": {"label": "Technical", "direction": "SELL", "confidence": 82},
            "classical": {"label": "Classical", "direction": "SELL", "confidence": 80},
            "smc": {"label": "SMC", "direction": "SELL", "confidence": 90},
            "price_action": {"label": "Price Action", "direction": "WAIT", "confidence": 40},
            "multitimeframe": {"label": "Multi-Timeframe", "direction": "WAIT", "confidence": 35},
        },
        "daily_bias": {"bias": "BEARISH", "confidence": 95},
        "news_context": {"rule_based": {"can_trade": True, "market_status": "SAFE"}, "macro": {"macro_direction": {"bias": "BEARISH_GOLD", "confidence": 64}}},
        "market_context": {"macro_direction": {"bias": "BEARISH_GOLD", "confidence": 64}},
        "session_info": {"current_session": "London + New York Afternoon", "session_quality": "HIGH"},
        "session_plan": {
            "plan_ready": True,
            "plan_id": "PLAN::SCENARIO::XAU/USD::20260717::LONDON::SELL::STRUCTURE_CONTINUATION",
            "scenario_id": "SCENARIO::XAU/USD::20260717::LONDON::SELL::STRUCTURE_CONTINUATION",
            "symbol": "XAU/USD",
            "session_bias": "SELL",
            "scenario_type": "STRUCTURE_CONTINUATION",
            "planner_confidence": 78,
            "planner_grade": "B",
            "poi_classification": "HIGH_PROBABILITY_POI",
            "extreme_poi": False,
            "execution_preference": "LADDER_PENDING",
            "primary_poi": _candidate("PRIMARY", 4020.0, 4044.0, 3965.0),
            "standby_poi": _candidate("STANDBY", 4009.0, 4030.0, 3950.0),
        },
    }


def _config() -> dict:
    return {
        "symbol": "XAU/USD",
        "database": {"url": None, "key": None},
        "order_execution": {"entry_style": "hybrid", "market_threshold_points": 30},
        "duplicate_signal_filter": {
            "enabled": True,
            "price_zone_points": 200,
            "open_trade": {"block_same_direction_in_zone": True, "block_same_direction_any_price": False, "max_open_same_direction": 3},
            "cooldown": {"lookback_hours": 6, "after_loss_minutes": 90, "after_breakeven_minutes": 45, "after_win_minutes": 30},
        },
        "session_planner": {"create_pending_orders_from_plan": True},
        "split_execution": {"enabled": True, "starter_risk_share": 0.4, "add_on_risk_share": 0.6, "starter_max_zone_progress_pct": 45},
    }


def test_session_plan_ladder_creates_primary_and_standby_pending_orders(tmp_path: Path) -> None:
    db = _db(tmp_path)
    telegram = _Telegram()
    decision = _base_decision()
    created = ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, telegram, _config())
    assert created == 2
    trades = load_trades(db.local_path)
    assert len(trades) == 2
    assert all(t["status"] == "PENDING" for t in trades)
    roles = sorted(str(((t.get("signal_snapshot") or {}).get("setup_context") or {}).get("pending_plan_role")) for t in trades)
    assert roles == ["PRIMARY", "STANDBY"]
    leg_labels = sorted(str(((t.get("signal_snapshot") or {}).get("setup_context") or {}).get("execution_leg_label")) for t in trades)
    assert leg_labels == ["ADD SELL AREA", "MAIN SELL AREA"]
    assert len(telegram.sent) == 2


def test_session_plan_ladder_skips_when_same_symbol_active_trade_exists(tmp_path: Path) -> None:
    db = _db(tmp_path)
    telegram = _Telegram()
    decision = _base_decision()
    existing = [{"id": "OPEN1", "symbol": "XAU/USD", "type": "SELL", "status": "OPEN", "entry_price": 4015.0}]
    created = ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, existing, db, telegram, _config())
    assert created == 0
    assert load_trades(db.local_path) == []
    assert telegram.sent == []


def test_session_plan_ladder_replaces_older_pending_family_when_new_plan_is_stronger(tmp_path: Path) -> None:
    db = _db(tmp_path)
    telegram = _Telegram()
    decision = _base_decision()
    old_trades = [
        {
            "id": "OLD1",
            "symbol": "XAU/USD",
            "type": "SELL",
            "status": "PENDING",
            "entry_price": 4018.0,
            "signal_snapshot": {
                "session_plan": {
                    "scenario_id": "SCENARIO::OLD",
                    "planner_confidence": 70,
                    "symbol": "XAU/USD",
                    "session_bias": "SELL",
                },
                "setup_context": {
                    "scenario_id": "SCENARIO::OLD",
                    "pending_plan_role": "PRIMARY",
                    "thesis_dominance_score": 58,
                },
                "pending_runtime": {"freshness_state": "STALE"},
            },
        }
    ]
    from utils.helpers import save_trades
    save_trades(old_trades, db.local_path)
    created = ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, old_trades, db, telegram, _config())
    assert created == 2
    trades = load_trades(db.local_path)
    assert any(t["id"] == "OLD1" and t["status"] == "CANCELLED" for t in trades)
    assert len([t for t in trades if t["status"] == "PENDING"]) == 2
    assert len(telegram.sent) == 2


def test_session_plan_extreme_poi_split_execution_creates_market_starter_and_pending_addon(tmp_path: Path) -> None:
    db = _db(tmp_path)
    telegram = _Telegram()
    decision = _base_decision()
    decision["current_price"] = 4021.0
    decision["session_plan"]["poi_classification"] = "EXTREME_POI"
    decision["session_plan"]["extreme_poi"] = True
    decision["session_plan"]["execution_preference"] = "SPLIT_EXECUTION_WATCH"
    decision["session_plan"]["primary_poi"]["poi_zone"] = {"top": 4045.0, "bottom": 4020.0}
    decision["session_plan"]["primary_poi"]["poi_low"] = 4020.0
    decision["session_plan"]["primary_poi"]["poi_high"] = 4045.0
    decision["session_plan"]["primary_poi"]["entry_price"] = 4032.5
    # The add-on entry must sit clear of its own stop (4030.0). Overriding it
    # to exactly the stop produced a zero-risk SELL leg -- any tick against it
    # would be an instant exit -- which the pre-send validator now refuses.
    decision["session_plan"]["standby_poi"]["entry_price"] = 4025.0
    created = ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, telegram, _config())
    assert created == 2
    trades = load_trades(db.local_path)
    statuses = sorted(str(t.get("status")) for t in trades)
    assert statuses == ["OPEN", "PENDING"]
    roles = sorted(str(((t.get("signal_snapshot") or {}).get("setup_context") or {}).get("pending_plan_role")) for t in trades)
    assert roles == ["ADD_ON", "STARTER"]
    leg_labels = sorted(str(((t.get("signal_snapshot") or {}).get("setup_context") or {}).get("execution_leg_label")) for t in trades)
    assert leg_labels == ["ADD-ON from ADD SELL AREA", "STARTER inside MAIN SELL AREA"]


def test_session_plan_ladder_applies_minimum_sl_floor_to_pending_orders(tmp_path: Path) -> None:
    db = _db(tmp_path)
    telegram = _Telegram()
    decision = _base_decision()
    decision["session_plan"]["session_bias"] = "BUY"
    decision["session_plan"]["primary_poi"]["direction"] = "BUY"
    decision["session_plan"]["primary_poi"]["entry_price"] = 4042.43
    decision["session_plan"]["primary_poi"]["stop_loss"] = 4039.64
    decision["session_plan"]["primary_poi"]["target_price"] = 4085.13
    decision["session_plan"]["primary_poi"]["target_liquidity"] = 4085.13
    cfg = _config()
    cfg["session_planner"]["create_pending_orders_from_plan"] = True
    cfg["risk_settings"] = {
        "min_sl_distance_points": 400,
        "atr_multiplier_sl": 2.0,
        "atr_multiplier_tp1": 2.5,
        "atr_multiplier_tp2": 4.5,
        "max_rr_ratio": 4.0,
    }
    # The floor widens risk from $2.79 to $40 while the mapped liquidity is
    # only $42.70 away, leaving 1.07R against a 1.5R minimum. No further
    # liquidity is mapped, so the leg must be rejected rather than shipped
    # with an invented target that manufactures an acceptable ratio.
    created = ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, telegram, cfg)
    assert created == 0
    assert load_trades(db.local_path) == []


def test_session_plan_ladder_extends_to_further_liquidity_when_mapped_target_is_too_close(tmp_path: Path) -> None:
    """A close target must not be inflated; a real further level may be used.

    UPDATED after stop entries were removed. The BUY here is mapped at 4042.43
    with a 4039.64 stop while price sits at 3992.76 -- roughly 500 points
    below. That used to rest above the market as a BUY_STOP.

    Stop entries are gone, and this plan cannot be converted either: its stop
    is above the live price, so a market BUY would open past its own
    invalidation. The leg is therefore refused, which is the correct outcome.

    The target-extension logic this test was written for is unchanged and is
    still covered by _resolve_reward_target's own tests; what is asserted here
    now is that the ladder refuses rather than ships an unprotected entry.
    """
    db = _db(tmp_path)
    telegram = _Telegram()
    decision = _base_decision()
    decision["session_plan"]["session_bias"] = "BUY"
    primary = decision["session_plan"]["primary_poi"]
    primary["direction"] = "BUY"
    primary["entry_price"] = 4042.43
    primary["stop_loss"] = 4039.64
    primary["target_price"] = 4085.13
    primary["target_liquidity"] = 4085.13
    # A genuine higher liquidity pool exists at 4110.00 -> 1.69R after the floor.
    primary["details"] = {"liquidity": {"buy_side": [4085.13, 4110.00]}}
    cfg = _config()
    cfg["session_planner"]["create_pending_orders_from_plan"] = True
    cfg["risk_settings"] = {
        "min_sl_distance_points": 400,
        "atr_multiplier_sl": 2.0,
        "atr_multiplier_tp1": 2.5,
        "atr_multiplier_tp2": 4.5,
        "max_rr_ratio": 4.0,
        "min_rr_ratio": 1.5,
    }
    created = ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, telegram, cfg)
    assert created == 0, (
        "a BUY whose stop sits above the live price cannot be filled at the "
        "market, and stop entries are no longer permitted"
    )
    assert load_trades(db.local_path) == []


def _confidence_with(classical, multitimeframe, macro=(None, 0)):
    decision = _base_decision()
    decision["decision"] = "BUY"
    decision["session_plan"]["session_bias"] = "BUY"
    decision["agent_details"] = {
        "technical": {"direction": "BUY", "confidence": 92},
        "smc": {"direction": "BUY", "confidence": 76},
        "price_action": {"direction": "BUY", "confidence": 84},
        "classical": classical,
        "multitimeframe": multitimeframe,
    }
    decision["news_context"] = {
        "rule_based": {"can_trade": True, "market_status": "SAFE"},
        "macro": {"macro_direction": {"bias": macro[0], "confidence": macro[1]}},
    }
    candidate = {"thesis_dominance_score": 64.0, "quality_score": 92.0}
    return ra._planner_display_confidence(decision, {}, candidate, _config(), direction="BUY")


def test_display_confidence_penalises_qualified_opposing_agents() -> None:
    """Disagreement must move the number, not just agreement.

    Only supporting votes were summed, so a qualified agent voting the other
    way at 95% produced the same confidence as an unqualified one at 27%.
    """
    neutral = _confidence_with({"direction": "WAIT", "confidence": 27}, {"direction": "WAIT", "confidence": 67})
    one_weak = _confidence_with({"direction": "SELL", "confidence": 72}, {"direction": "WAIT", "confidence": 67})
    one_strong = _confidence_with({"direction": "SELL", "confidence": 95}, {"direction": "WAIT", "confidence": 67})
    two_strong = _confidence_with({"direction": "SELL", "confidence": 95}, {"direction": "SELL", "confidence": 93})

    assert one_weak < neutral, "a qualified opponent must cost confidence"
    assert one_strong < one_weak, "a stronger opponent must cost more"
    assert two_strong < one_strong, "two opponents must cost more than one"


def test_display_confidence_ignores_unqualified_opposition() -> None:
    """Below-threshold votes are noise on both sides, not evidence."""
    neutral = _confidence_with({"direction": "WAIT", "confidence": 27}, {"direction": "WAIT", "confidence": 67})
    unqualified_opponent = _confidence_with({"direction": "SELL", "confidence": 40}, {"direction": "WAIT", "confidence": 67})
    assert unqualified_opponent == neutral


def test_display_confidence_is_symmetric_for_macro() -> None:
    """Supporting macro added 2.0 while opposing macro cost nothing."""
    neutral = _confidence_with({"direction": "WAIT", "confidence": 27}, {"direction": "WAIT", "confidence": 67})
    supporting = _confidence_with({"direction": "WAIT", "confidence": 27}, {"direction": "WAIT", "confidence": 67}, macro=("BULLISH_GOLD", 70))
    opposing = _confidence_with({"direction": "WAIT", "confidence": 27}, {"direction": "WAIT", "confidence": 67}, macro=("BEARISH_GOLD", 69))
    strongly_opposing = _confidence_with({"direction": "WAIT", "confidence": 27}, {"direction": "WAIT", "confidence": 67}, macro=("BEARISH_GOLD", 92))

    assert supporting > neutral
    assert opposing < neutral
    assert strongly_opposing < opposing


def test_session_plan_ladder_display_confidence_is_capped_below_100(tmp_path: Path) -> None:
    decision = _base_decision()
    decision["session_plan"]["planner_confidence"] = 100
    candidate = decision["session_plan"]["primary_poi"]
    ladder = ra._build_plan_ladder_decision(decision, decision["session_plan"], candidate, _config())
    assert ladder is not None
    assert ladder["confidence"] < 100
    assert ladder["confidence"] <= 95
    assert ladder["quality"]["grade"] == "B"
    assert ladder["quality"]["score"] == 76
    assert ladder["planner_quality"]["grade"] == "B"
    assert ladder["planner_quality"]["score"] == 100


def test_session_plan_ladder_blocks_invalidated_primary_leg(tmp_path: Path) -> None:
    db = _db(tmp_path)
    telegram = _Telegram()
    decision = _base_decision()
    decision["session_plan"]["primary_poi"]["setup_state"] = "INVALIDATED"
    created = ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, telegram, _config())
    assert created == 0
    assert load_trades(db.local_path) == []


def test_session_plan_ladder_skips_low_revisit_very_far_add_leg(tmp_path: Path) -> None:
    db = _db(tmp_path)
    telegram = _Telegram()
    decision = _base_decision()
    decision["current_price"] = 4086.89
    decision["session_plan"]["primary_poi"]["direction"] = "BUY"
    decision["session_plan"]["standby_poi"] = {
        **_candidate("STANDBY", 4055.92, 4015.92, 4145.92),
        "direction": "BUY",
        "selection_role": "STANDBY",
        "expected_revisit_window": "LOW",
        "return_probability_score": 8.0,
        "quality_grade": "A+",
        "quality_score": 100.0,
    }
    decision["session_plan"]["primary_poi"] = {
        **_candidate("PRIMARY", 4100.28, 4060.28, 4190.28),
        "direction": "BUY",
        "selection_role": "PRIMARY",
    }
    decision["session_plan"]["session_bias"] = "BUY"
    decision["decision"] = "BUY"
    decision["agent_details"] = {
        "technical": {"label": "Technical", "direction": "BUY", "confidence": 92},
        "classical": {"label": "Classical", "direction": "WAIT", "confidence": 27},
        "smc": {"label": "SMC", "direction": "BUY", "confidence": 76},
        "price_action": {"label": "Price Action", "direction": "BUY", "confidence": 84},
        "multitimeframe": {"label": "Multi-Timeframe", "direction": "BUY", "confidence": 67},
    }
    decision["news_context"] = {"rule_based": {"can_trade": True, "market_status": "SAFE"}, "macro": {"macro_direction": {"bias": "BULLISH_GOLD", "confidence": 64}}}
    created = ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, telegram, _config())
    trades = load_trades(db.local_path)
    assert created == 1
    roles = [str(((t.get("signal_snapshot") or {}).get("setup_context") or {}).get("pending_plan_role")) for t in trades]
    assert roles == ["PRIMARY"]


def test_session_plan_ladder_blocked_without_admission_gate(tmp_path: Path) -> None:
    db = _db(tmp_path)
    telegram = _Telegram()
    decision = _base_decision()
    decision["decision"] = "WAIT"
    decision["news_context"] = {"rule_based": {"can_trade": True, "market_status": "SAFE"}, "macro": {"macro_direction": {"bias": "NEUTRAL", "confidence": 40}}}
    decision["gemini_macro_review"] = {"available": False}
    decision["gemini_review"] = {"available": False}
    decision["gemini_analysis"] = {"available": False}
    decision["agent_details"] = {
        "technical": {"label": "Technical", "direction": "SELL", "confidence": 82},
        "classical": {"label": "Classical", "direction": "WAIT", "confidence": 30},
        "smc": {"label": "SMC", "direction": "SELL", "confidence": 90},
        "price_action": {"label": "Price Action", "direction": "BUY", "confidence": 79},
        "multitimeframe": {"label": "Multi-Timeframe", "direction": "BUY", "confidence": 86},
    }
    created = ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, telegram, _config())
    assert created == 0
    assert load_trades(db.local_path) == []
    assert telegram.sent == []


# ─── Liquidity-based targets and the dynamic stop floor ────────────────────


def _levels(stop_loss: float, target: float, liquidity=None, cfg_overrides=None):
    cfg = _config()
    cfg["risk_settings"] = {
        "min_sl_distance_points": 400,
        "min_rr_ratio": 1.5,
        "atr_multiplier_sl": 2.0,
        "atr_multiplier_tp1": 2.5,
        "atr_multiplier_tp2": 4.5,
        "max_rr_ratio": 4.0,
        **(cfg_overrides or {}),
    }
    candidate = {"details": {"liquidity": {"sell_side": liquidity}}} if liquidity else {}
    return ra._planner_trade_levels(
        cfg, direction="SELL", entry_price=4075.15, stop_loss=stop_loss,
        target_price=target, symbol="XAU/USD", candidate=candidate,
    )


def test_near_liquidity_becomes_tp1_while_tp2_carries_the_reward_test() -> None:
    """A close first target must not disqualify a structurally sound plan.

    Judging viability on TP1 rejected real setups whose stop had been widened
    by the floor, even though a further mapped pool made the trade worthwhile.

    The plan still qualifies on TP2, but the near pool is no longer accepted as
    TP1: at 0.69R it is close enough that touching it arms the breakeven stop
    almost immediately, which is exactly how a correct BUY was closed flat.
    A pool short of min_tp1_rr is skipped, not fatal.
    """
    # Directive 2026-08-06: only the nearest two pools are considered. Here the
    # 2nd pool (4015.0) clears min_rr (1.5), so it becomes TP2; the 3rd (3985.15)
    # also clears 1.5R but is the 3rd-nearest and is never looked at.
    levels = _levels(4079.0, 4064.74, liquidity=[4064.74, 4015.0, 3985.15])
    assert not levels.get("reject_reason")
    assert levels["tp2"] == 4015.0, "TP2 must come from the nearest two pools only"


def test_a_far_third_pool_does_not_rescue_a_failing_near_pair() -> None:
    """Nearest two pools fail min_rr; the far 3rd clears it but is ignored, so
    the leg is rejected honestly instead of being stretched far away."""
    levels = _levels(4079.0, 4064.74, liquidity=[4064.74, 4030.00, 3985.15])
    assert levels.get("reject_reason"), "nearest two fail 1.5R; far pool must not rescue"


def test_targets_are_never_invented_when_no_further_liquidity_exists() -> None:
    levels = _levels(4079.0, 4064.74, liquidity=None)
    assert levels.get("reject_reason")
    assert "no further qualifying liquidity" in levels["reject_reason"]


def test_dynamic_floor_scales_risk_with_structure() -> None:
    """A tight POI should not inherit the full fixed floor."""
    dynamic = _levels(
        4079.0, 4064.74, liquidity=[4064.74, 4030.00],
        cfg_overrides={"dynamic_sl_floor": {"enabled": True, "structural_multiplier": 3.0,
                                            "min_points": 150, "max_points": 400}},
    )
    risk = round((dynamic["stop_loss"] - 4075.15) * 10)
    assert risk == 150, "structural 38 pts x3 is below the 150 pt lower bound"
    assert dynamic["rr"] > 2.0


def test_dynamic_floor_is_bounded_by_max_points() -> None:
    wide = _levels(
        4090.0, 4064.74, liquidity=[4064.74, 3985.15],
        cfg_overrides={"dynamic_sl_floor": {"enabled": True, "structural_multiplier": 3.0,
                                            "min_points": 150, "max_points": 400}},
    )
    risk = round((wide["stop_loss"] - 4075.15) * 10)
    assert risk == 400, "148 pts x3 exceeds the ceiling and must be capped"


def test_dynamic_floor_disabled_keeps_the_fixed_behaviour() -> None:
    fixed = _levels(
        4079.0, 4064.74, liquidity=[4064.74, 3985.15],
        cfg_overrides={"dynamic_sl_floor": {"enabled": False}},
    )
    assert round((fixed["stop_loss"] - 4075.15) * 10) == 400


# ─── Reviving an unexpired day map ─────────────────────────────────────────


def _saved_plan_row(plan: dict, *, age_minutes: float = 25.0, expired: bool = False) -> dict:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    payload = dict(plan)
    payload["plan_expires_at"] = (
        (now - timedelta(hours=1)) if expired else (now + timedelta(hours=4))
    ).isoformat()
    return {"analysis_run_at": (now - timedelta(minutes=age_minutes)).isoformat(), "payload": payload}


class _DBWithPlans:
    """Wraps the local test database and serves saved plan snapshots."""

    def __init__(self, rows, tmp_path):
        self._rows = rows
        self._db = _db(tmp_path)
        self.local_path = self._db.local_path

    def get_recent_session_plans(self, **_kw):
        return self._rows

    def __getattr__(self, name):
        return getattr(self._db, name)


def test_unexpired_plan_is_revived_when_this_cycle_cannot_rebuild(tmp_path: Path) -> None:
    """A day map is a standing thesis, not a one-cycle opportunity.

    Plans are rebuilt each cycle and never reused, so a map only ever had the
    single cycle that produced it in which to place orders. If agents briefly
    disagreed on the next run, a confirmed and unexpired map was silently
    dropped.
    """
    ready = _base_decision()["session_plan"]
    db = _DBWithPlans([_saved_plan_row(ready)], tmp_path)
    telegram = _Telegram()

    decision = _base_decision()
    decision["session_plan"] = {
        "plan_ready": False,
        "plan_status": "WATCH_ONLY",
        "plan_reason": "only 1 supporting agents for the mapped direction",
    }
    created = ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, telegram, _config())
    assert created >= 1, "the unexpired map should still be actionable"


def test_expired_plan_is_not_revived(tmp_path: Path) -> None:
    ready = _base_decision()["session_plan"]
    db = _DBWithPlans([_saved_plan_row(ready, expired=True)], tmp_path)
    decision = _base_decision()
    decision["session_plan"] = {"plan_ready": False, "plan_status": "WATCH_ONLY", "plan_reason": "agents disagreed"}
    assert ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, _Telegram(), _config()) == 0


def test_revival_can_be_disabled(tmp_path: Path) -> None:
    ready = _base_decision()["session_plan"]
    db = _DBWithPlans([_saved_plan_row(ready)], tmp_path)
    cfg = _config()
    cfg["session_planner"]["revive_unexpired_plans"] = False
    decision = _base_decision()
    decision["session_plan"] = {"plan_ready": False, "plan_status": "WATCH_ONLY", "plan_reason": "agents disagreed"}
    assert ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, _Telegram(), cfg) == 0


def test_a_ready_plan_this_cycle_is_never_replaced_by_an_older_one(tmp_path: Path) -> None:
    """Revival is a fallback, not a preference."""
    stale = _base_decision()["session_plan"]
    stale["scenario_id"] = "SCENARIO::OLD"
    db = _DBWithPlans([_saved_plan_row(stale)], tmp_path)
    decision = _base_decision()  # carries a ready plan already
    ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, _Telegram(), _config())
    trades = load_trades(db.local_path)
    assert trades, "current plan should have produced orders"
    for trade in trades:
        sid = ((trade.get("signal_snapshot") or {}).get("session_plan") or {}).get("scenario_id")
        assert sid != "SCENARIO::OLD"
