"""A revived day map must earn its execution permission from the LIVE book.

FAULT INJECTION: delete the readiness re-judgement block in
`scripts/run_analysis.py :: _revive_recent_ready_plan` (the
`revived["readiness_rejudged_on_revival"]` section) and these tests fail --
the ladder replays the stored verdict instead.

The bug, measured on 2026-08-04 17:25 (run #8341): a BUY map built 12:45 and
stored as WATCH_EXECUTION was revived, re-authorised on the agents, and then
blocked by "map is valid but still waiting for stronger execution
confirmation" -- a sentence describing the 12:45 book, while the same cycle
confirmed BUY via two agents at 92% and macro at 68%. The readiness stamp was
a fossil. The re-authorisation gate re-counted the agents; readiness did not.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

import scripts.run_analysis as ra
from services.database import DatabaseService
from utils.helpers import load_config

# All stored timestamps are relative to the test's own start, never a fixed
# wall-clock instant: the ladder revives against datetime.now(timezone.utc),
# so a hardcoded expiry turned this test into a time bomb that detonated on
# CI the moment the runner's clock passed it (2026-08-04, run #1).
NOW = datetime.now(timezone.utc)


def _config() -> dict:
    cfg = load_config()
    cfg["session_planner"] = dict(cfg.get("session_planner") or {})
    cfg["session_planner"].update({
        "create_pending_orders_from_plan": True,
        "revive_unexpired_plans": True,
        "agent_alignment_min_confidence": 68,
    })
    cfg["risk_settings"] = dict(cfg.get("risk_settings") or {})
    cfg["risk_settings"].update({
        "min_sl_distance_points": 400,
        "min_rr_ratio": 1.5,
        "min_tp1_rr": 0.8,
        "dynamic_sl_floor": {"enabled": True, "structural_multiplier": 3.0,
                             "min_points": 150, "max_points": 400},
    })
    cfg["trade_management"] = dict(cfg.get("trade_management") or {})
    cfg["trade_management"]["auto_move_sl_to_entry_after_tp1"] = True
    cfg["trade_management"]["min_breakeven_rr"] = 0.5
    cfg["order_execution"] = {"enabled": True, "entry_style": "hybrid",
                              "market_threshold_points": 30,
                              "pending_threshold_points": 20}
    cfg["split_execution"] = {"enabled": False}
    return cfg


def _stored_plan(stored_state: str) -> dict:
    """The 2026-08-04 Asia Morning BUY geometry, stored as a session_plans row."""
    return {
        "symbol": "XAU/USD",
        "plan_ready": True,
        "plan_status": "READY",
        "session_bias": "BUY",
        "scenario_type": "FAILED_RECLAIM_CONTINUATION",
        "preferred_execution_family": "FAILED_RECLAIM_CONTINUATION",
        "plan_id": "PLAN::SCENARIO::XAU/USD::20260804::ASIA::BUY::FAILED_RECLAIM_CONTINUATION",
        "scenario_id": "SCENARIO::XAU/USD::20260804::ASIA::BUY::FAILED_RECLAIM_CONTINUATION",
        "planner_confidence": 97.8,
        "planner_grade": "A+",
        "plan_expires_at": (NOW + timedelta(hours=2)).isoformat(),
        "created_at": (NOW - timedelta(minutes=280)).isoformat(),
        "execution_readiness": {
            "state": stored_state,
            "reason": "map is valid but still waiting for stronger execution confirmation"
            if stored_state == "WATCH_EXECUTION" else "trigger FAILED_RECLAIM_CONFIRMED with 4 execution-support agents",
        },
        "primary_poi": {
            "id": "CAND::PRIMARY",
            "state_key": "STATE::PRIMARY",
            "direction": "BUY",
            "setup_type": "FAILED_RECLAIM_CONTINUATION",
            "setup_state": "ENTRY_ARMED",
            "selection_role": "PRIMARY",
            "selection_rank": 1,
            "entry_price": 4060.95,
            "stop_loss": 4057.83,
            "target_price": 4090.00,
            "target_liquidity": 4090.00,
            "tp1": 4075.00,
            "poi_type": "order_block",
            "poi_zone": {"top": 4063.95, "bottom": 4057.95},
            "poi_low": 4057.95,
            "poi_high": 4063.95,
            "quality_score": 97.8,
            "quality_grade": "A+",
            "trigger_state": "FAILED_RECLAIM_CONFIRMED",
            "trigger_ready": True,
        },
        "standby_poi": {},
    }


def _row(stored_state: str) -> dict:
    plan = _stored_plan(stored_state)
    return {"symbol": "XAU/USD", "plan_ready": True,
            "analysis_run_at": (NOW - timedelta(minutes=280)).isoformat(),
            "payload": plan}


def _db_with(monkeypatch, stored_state: str) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None,
                                       "local_fallback_file": "/tmp/revival_probe_trades.json"}})
    monkeypatch.setattr(db, "get_recent_session_plans",
                        lambda **kwargs: [_row(stored_state)])
    return db


def _live_confirmation_results() -> dict:
    """The 17:25 book: four BUY agents above the 68 alignment bar, macro confirming."""
    return {
        "technical": {"signal": "WAIT", "confidence": 41},
        "classical": {"signal": "BUY", "confidence": 74},
        "smc": {"signal": "BUY", "confidence": 76},
        "price_action": {"signal": "BUY", "confidence": 84},
        "multitimeframe": {"signal": "BUY", "confidence": 92},
        "macro_fundamental": {"bias": "BULLISH_GOLD", "confidence": 68},
    }


def _base_decision() -> dict:
    return {
        "decision": "WAIT",
        "symbol": "XAU/USD",
        "current_price": 4061.0,
        "confidence": 0.0,
        "agent_details": {
            "technical": {"direction": "WAIT", "confidence": 41},
            "classical": {"direction": "BUY", "confidence": 74},
            "smc": {"direction": "BUY", "confidence": 76},
            "price_action": {"direction": "BUY", "confidence": 84},
            "multitimeframe": {"direction": "BUY", "confidence": 92},
        },
    }


def test_revival_lifts_a_stored_watch_map_when_the_live_book_confirms(monkeypatch) -> None:
    """The 17:25 case: stored WATCH, live confirmation strong -> executable."""
    db = _db_with(monkeypatch, "WATCH_EXECUTION")
    revived = ra._revive_recent_ready_plan(
        db, _config(), symbol="XAU/USD", now=NOW,
        base_decision=_base_decision(), all_results=_live_confirmation_results(),
    )
    assert revived is not None
    state = (revived.get("execution_readiness") or {}).get("state")
    assert state in {"PENDING_EXECUTION_READY", "MARKET_EXECUTION_READY"}, (
        f"stored WATCH was replayed instead of re-judged (got {state})"
    )
    assert revived.get("readiness_rejudged_on_revival") is True
    audit = revived.get("readiness_at_revival") or {}
    assert audit.get("stored_state") == "WATCH_EXECUTION"


def test_revival_degrades_a_stored_ready_map_when_live_support_drifts(monkeypatch) -> None:
    """Symmetry: the re-judgement also DEMOTES. One supporter, no SMC, macro
    opposed -> fallback strictness must pull a stored READY back off the trigger."""
    db = _db_with(monkeypatch, "MARKET_EXECUTION_READY")
    drifted = {
        "technical": {"signal": "WAIT", "confidence": 41},
        "classical": {"signal": "BUY", "confidence": 74},
        "smc": {"signal": "WAIT", "confidence": 40},
        "price_action": {"signal": "WAIT", "confidence": 50},
        "multitimeframe": {"signal": "WAIT", "confidence": 55},
        "macro_fundamental": {"bias": "BEARISH_GOLD", "confidence": 64},
    }
    revived = ra._revive_recent_ready_plan(
        db, _config(), symbol="XAU/USD", now=NOW,
        base_decision=_base_decision(), all_results=drifted,
    )
    assert revived is not None
    state = (revived.get("execution_readiness") or {}).get("state")
    assert state not in {"PENDING_EXECUTION_READY", "MARKET_EXECUTION_READY"}, (
        f"stored READY was replayed despite live drift (got {state})"
    )
    assert revived.get("readiness_rejudged_on_revival") is True


class _Telegram:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_signal(self, decision: dict) -> bool:
        self.sent.append(decision)
        return True

    def send_scenario_governance(self, *args, **kwargs) -> bool:
        return True


def test_revived_map_now_trades_end_to_end(tmp_path, monkeypatch) -> None:
    """Full ladder: no fresh plan this cycle, the revived one takes the live
    confirmation and CREATES the order the 17:25 run refused."""
    db = DatabaseService({"database": {"url": None, "key": None,
                                       "local_fallback_file": str(tmp_path / "trades.json")}})
    db.local_path = tmp_path / "trades.json"
    monkeypatch.setattr(db, "get_recent_session_plans",
                        lambda **kwargs: [_row("WATCH_EXECUTION")])
    telegram = _Telegram()
    decision = _base_decision()  # no session_plan -> the ladder must revive

    ra._LAST_LADDER_STOP.clear()
    created = ra._execute_session_plan_ladder(
        decision, _live_confirmation_results(), [], db, telegram, _config(),
    )
    assert created == 1, f"revived map did not trade; stop={ra._LAST_LADDER_STOP}"
    assert len(telegram.sent) == 1
