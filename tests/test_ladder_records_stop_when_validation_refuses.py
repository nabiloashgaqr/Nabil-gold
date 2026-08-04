"""The ladder must name the refusal when final validation kills the leg.

FAULT INJECTION: revert the `_ladder_stop(...)` calls added to the order loop
in `scripts/run_analysis.py` (duplicate filter / final validation / telegram
delivery / already-staged) and this test fails -- `_LAST_LADDER_STOP` stays
empty and the audit would read "gate allowed; stop not recorded" again.

Why this exact shape: the 2026-08-04 A+ 97.8% Asia Morning BUY map died here.
Reference entry 4060.95 with TP1 4075.00 is only 140.5 points to the first
target, and the +150-point early-breakeven trigger therefore can never arm
BEFORE TP1. The validator refuses the promise ("the promised protection
cannot apply"), the PRIMARY leg returns zero orders -- and before this fix
nothing recorded which step did it.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

import scripts.run_analysis as ra
from services.database import DatabaseService
from utils.helpers import load_config


class _Telegram:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_signal(self, decision: dict) -> bool:
        self.sent.append(decision)
        return True

    def send_scenario_governance(self, *args, **kwargs) -> bool:
        return True


def _db(tmp_path: Path) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None, "local_fallback_file": str(tmp_path / "trades.json")}})
    db.local_path = tmp_path / "trades.json"
    return db


def _config() -> dict:
    cfg = load_config()
    # The production values that matter for this refusal, pinned explicitly so
    # the test states its own geometry instead of drifting with the config.
    cfg["trade_management"] = dict(cfg.get("trade_management") or {})
    cfg["trade_management"]["early_breakeven_points"] = 150
    cfg["risk_settings"] = dict(cfg.get("risk_settings") or {})
    cfg["risk_settings"].update({
        "min_sl_distance_points": 400,
        "min_rr_ratio": 1.5,
        "min_tp1_rr": 0.8,
        "dynamic_sl_floor": {"enabled": True, "structural_multiplier": 3.0,
                             "min_points": 150, "max_points": 400},
    })
    cfg["order_execution"] = {"enabled": True, "entry_style": "hybrid",
                              "market_threshold_points": 30,
                              "pending_threshold_points": 20}
    cfg["session_planner"] = dict(cfg.get("session_planner") or {})
    cfg["session_planner"]["create_pending_orders_from_plan"] = True
    cfg["split_execution"] = {"enabled": False}
    return cfg


def _primary_candidate() -> dict:
    # The 2026-08-04 Asia Morning map, verbatim geometry.
    return {
        "id": "CAND::PRIMARY",
        "state_key": "STATE::PRIMARY",
        "direction": "BUY",
        "setup_type": "FAILED_RECLAIM_CONTINUATION",
        "setup_state": "ENTRY_ARMED",
        "lead_agent": "smc",
        "selection_role": "PRIMARY",
        "selection_rank": 1,
        "entry_price": 4060.95,
        # Structural (pre-floor) stop; the 150-point floor carries it to the
        # published invalidation 4045.95, exactly as the card printed it.
        "stop_loss": 4057.83,
        "target_price": 4090.00,
        "target_liquidity": 4090.00,
        "tp1": 4075.00,
        "poi_type": "order_block",
        "poi_zone": {"top": 4063.95, "bottom": 4057.95},
        "poi_low": 4057.95,
        "poi_high": 4063.95,
        "poi_quality_score": 97.8,
        "quality_score": 97.8,
        "quality_grade": "A+",
        "trigger_state": "FAILED_RECLAIM_CONFIRMED",
        "trigger_ready": True,
    }


def _decision() -> dict:
    return {
        # Live consensus read WAIT that morning; the gate falls back to the
        # mapped bias and must still count the agents on their own.
        "decision": "WAIT",
        "symbol": "XAU/USD",
        "current_price": 4061.0,
        "confidence": 0.0,
        "agent_details": {
            "technical": {"label": "Technical", "direction": "WAIT", "confidence": 41},
            "classical": {"label": "Classical", "direction": "BUY", "confidence": 74},
            "smc": {"label": "SMC", "direction": "BUY", "confidence": 76},
            "price_action": {"label": "Price Action", "direction": "BUY", "confidence": 84},
            "multitimeframe": {"label": "Multi-Timeframe", "direction": "BUY", "confidence": 92},
        },
        "session_plan": {
            "plan_ready": True,
            "plan_id": "PLAN::SCENARIO::XAU/USD::20260804::ASIA::BUY::FAILED_RECLAIM_CONTINUATION",
            "scenario_id": "SCENARIO::XAU/USD::20260804::ASIA::BUY::FAILED_RECLAIM_CONTINUATION",
            "symbol": "XAU/USD",
            "session_bias": "BUY",
            "scenario_type": "FAILED_RECLAIM_CONTINUATION",
            "planner_confidence": 97.8,
            "planner_grade": "A+",
            "poi_classification": "EXTREME_POI",
            "extreme_poi": True,
            "execution_preference": "LADDER_PENDING",
            "execution_readiness": {"state": "MARKET_EXECUTION_READY",
                                    "reason": "trigger FAILED_RECLAIM_CONFIRMED with 4 execution-support agents"},
            "primary_poi": _primary_candidate(),
            "standby_poi": {},
        },
    }


def test_validation_refusal_is_recorded_as_the_ladder_stop(tmp_path: Path) -> None:
    """Gate passes, pricing passes, final validation refuses -> the audit
    must name the refusal instead of reading 'stop not recorded'."""
    ra._LAST_LADDER_STOP.clear()
    db = _db(tmp_path)
    telegram = _Telegram()

    created = ra._execute_session_plan_ladder(_decision(), {"symbol": "XAU/USD"}, [], db, telegram, _config())

    # The gate: 4 qualified BUY agents (74/76/84/92) against a bar of 67.
    gate = ra._planner_execution_gate(_decision(), _config())
    assert gate.get("allow") is True
    assert gate.get("support_count") == 4

    # The trade itself is refused: TP1 4075.00 is 140.5 pts from the 4061
    # market fill, closer than the +150 pt breakeven trigger.
    assert created == 0
    assert telegram.sent == []

    # AND THE REFUSAL IS NAMED. This is the regression the fix closes:
    # before it, `_LAST_LADDER_STOP` stayed empty here, so the persisted
    # audit said "gate allowed; stop not recorded".
    assert ra._LAST_LADDER_STOP, "ladder stopped silently; the audit will not name the refusal"
    reason = str(ra._LAST_LADDER_STOP.get("reason") or "")
    assert "failed final validation" in reason
    assert "breakeven" in reason
