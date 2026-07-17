from __future__ import annotations

from pathlib import Path

from services.database import DatabaseService
from services.scenario_governor import ScenarioGovernor
from utils.helpers import load_trades, save_trades


def _db(tmp_path: Path) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None, "local_fallback_file": str(tmp_path / 'trades.json')}})
    db.local_path = tmp_path / 'trades.json'
    return db


def _plan(score: float = 78.0, dominance: float = 68.0, scenario_id: str = "SCENARIO::NEW") -> dict:
    return {
        "plan_ready": True,
        "scenario_id": scenario_id,
        "plan_id": f"PLAN::{scenario_id}",
        "symbol": "XAU/USD",
        "session_bias": "SELL",
        "scenario_type": "STRUCTURE_CONTINUATION",
        "planner_confidence": score,
        "primary_poi": {"thesis_dominance_score": dominance},
    }


def _pending(trade_id: str, scenario_id: str, role: str, *, score: float = 72.0, dominance: float = 60.0, freshness: str = "FRESH") -> dict:
    return {
        "id": trade_id,
        "symbol": "XAU/USD",
        "type": "SELL",
        "status": "PENDING",
        "entry_price": 4020.0 if role == "PRIMARY" else 4009.0,
        "signal_snapshot": {
            "session_plan": {
                "scenario_id": scenario_id,
                "plan_id": f"PLAN::{scenario_id}",
                "symbol": "XAU/USD",
                "session_bias": "SELL",
                "scenario_type": "STRUCTURE_CONTINUATION",
                "planner_confidence": score,
            },
            "setup_context": {
                "scenario_id": scenario_id,
                "pending_plan_role": role,
                "selection_role": role,
                "thesis_dominance_score": dominance,
            },
            "pending_runtime": {
                "freshness_state": freshness,
            },
        },
    }


def test_scenario_governor_cancels_sibling_pending_when_one_activates(tmp_path: Path) -> None:
    db = _db(tmp_path)
    trades = [
        _pending("P1", "SCENARIO::A", "PRIMARY"),
        _pending("P2", "SCENARIO::A", "STANDBY"),
    ]
    save_trades(trades, db.local_path)
    gov = ScenarioGovernor({"scenario_governor": {"enabled": True}})
    result = gov.handle_activation(trades[0], database=db, open_trades=trades)
    assert result["action"] == "CANCELLED_SIBLINGS_ON_ACTIVATION"
    statuses = {t["id"]: t["status"] for t in load_trades(db.local_path)}
    assert statuses["P1"] == "PENDING"
    assert statuses["P2"] == "CANCELLED"


def test_scenario_governor_keeps_existing_pending_family_when_new_plan_not_stronger(tmp_path: Path) -> None:
    db = _db(tmp_path)
    trades = [_pending("P1", "SCENARIO::OLD", "PRIMARY", score=76.0, dominance=66.0)]
    save_trades(trades, db.local_path)
    gov = ScenarioGovernor({"scenario_governor": {"enabled": True, "min_plan_score_improvement": 4, "min_primary_dominance_improvement": 5}})
    result = gov.review_new_plan(_plan(score=78.0, dominance=68.0, scenario_id="SCENARIO::NEW"), trades, database=db)
    assert result["action"] == "KEEP_EXISTING_FAMILY"
    assert load_trades(db.local_path)[0]["status"] == "PENDING"


def test_scenario_governor_replaces_older_stale_pending_family(tmp_path: Path) -> None:
    db = _db(tmp_path)
    trades = [
        _pending("P1", "SCENARIO::OLD", "PRIMARY", score=70.0, dominance=58.0, freshness="STALE"),
        _pending("P2", "SCENARIO::OLD", "STANDBY", score=70.0, dominance=54.0, freshness="REVALIDATION_REQUIRED"),
    ]
    save_trades(trades, db.local_path)
    gov = ScenarioGovernor({"scenario_governor": {"enabled": True}})
    result = gov.review_new_plan(_plan(score=73.0, dominance=60.0, scenario_id="SCENARIO::NEW"), trades, database=db)
    assert result["action"] == "REPLACE_PENDING_FAMILY"
    statuses = {t["id"]: t["status"] for t in load_trades(db.local_path)}
    assert statuses["P1"] == "CANCELLED"
    assert statuses["P2"] == "CANCELLED"
