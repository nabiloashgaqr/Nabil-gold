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
        "decision": "WAIT",
        "symbol": "XAU/USD",
        "current_price": 3992.76,
        "confidence": 0,
        "agent_details": {},
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
