from __future__ import annotations

from pathlib import Path

from services.database import DatabaseService
from services.pending_governor import PendingGovernor
from utils.helpers import load_trades, save_trades


def _db(tmp_path: Path) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None}})
    db.local_path = tmp_path / "trades.json"
    return db


def _pending_trade(trade_id: str, direction: str = "SELL", **ctx) -> dict:
    setup = {
        "thesis_dominance_score": ctx.get("thesis_dominance_score", 60),
        "return_probability_score": ctx.get("return_probability_score", 55),
        "poi_quality_score": ctx.get("poi_quality_score", 80),
        "selection_role": ctx.get("selection_role", "PRIMARY"),
    }
    setup.update({k: v for k, v in ctx.items() if k not in setup})
    return {
        "id": trade_id,
        "symbol": "XAU/USD",
        "type": direction,
        "status": "PENDING",
        "entry_price": 4040.6,
        "signal_snapshot": {"setup_context": setup},
    }


def _decision(direction: str = "SELL", **ctx) -> dict:
    setup = {
        "thesis_dominance_score": ctx.get("thesis_dominance_score", 70),
        "return_probability_score": ctx.get("return_probability_score", 65),
        "poi_quality_score": ctx.get("poi_quality_score", 84),
        "selection_role": ctx.get("selection_role", "PRIMARY"),
    }
    setup.update({k: v for k, v in ctx.items() if k not in setup})
    return {
        "decision": direction,
        "symbol": "XAU/USD",
        "setup_context": setup,
    }


def test_pending_governor_keeps_existing_dominant_pending(tmp_path: Path) -> None:
    db = _db(tmp_path)
    trades = [_pending_trade("P1", thesis_dominance_score=74, return_probability_score=58, poi_quality_score=84)]
    save_trades(trades, db.local_path)
    governor = PendingGovernor({"pending_governor": {"enabled": True}})
    decision = _decision(thesis_dominance_score=68, return_probability_score=52, poi_quality_score=80)
    result = governor.review(decision, load_trades(db.local_path), database=db)
    assert result["action"] == "KEEP_EXISTING_PENDING"
    assert load_trades(db.local_path)[0]["status"] == "PENDING"


def test_pending_governor_replaces_weaker_pending_with_stronger_new_thesis(tmp_path: Path) -> None:
    db = _db(tmp_path)
    trades = [_pending_trade(
        "P1",
        thesis_dominance_score=58,
        return_probability_score=40,
        poi_quality_score=72,
        state_key="OLD::A",
        setup_type="STRUCTURE_CONTINUATION",
        setup_state="DETECTED",
        poi_type="order_block",
        poi_zone={"top": 4045.0, "bottom": 4043.0},
        trigger_state="AWAY_FROM_POI",
        trigger_score=42,
    )]
    save_trades(trades, db.local_path)
    governor = PendingGovernor({"pending_governor": {"enabled": True, "replace_min_dominance_delta": 8}})
    decision = _decision(
        thesis_dominance_score=72,
        return_probability_score=64,
        poi_quality_score=86,
        state_key="NEW::B",
        setup_type="LIQUIDITY_REVERSAL",
        setup_state="ENTRY_ARMED",
        poi_type="fvg",
        poi_zone={"top": 4033.0, "bottom": 4031.0},
        trigger_state="REJECTION_CONFIRMED",
        trigger_score=74,
    )
    result = governor.review(decision, load_trades(db.local_path), database=db)
    assert result["action"] == "REPLACE_PENDING"
    updated = load_trades(db.local_path)[0]
    assert updated["status"] == "CANCELLED"


def test_pending_governor_cancels_low_probability_pending(tmp_path: Path) -> None:
    db = _db(tmp_path)
    trades = [_pending_trade("P1", thesis_dominance_score=50, return_probability_score=20, poi_quality_score=70)]
    save_trades(trades, db.local_path)
    governor = PendingGovernor({"pending_governor": {"enabled": True, "cancel_if_return_probability_below": 25}})
    decision = _decision(thesis_dominance_score=48, return_probability_score=18, poi_quality_score=68)
    result = governor.review(decision, load_trades(db.local_path), database=db)
    assert result["action"] == "CANCEL_PENDING_ALLOW_NEW"
    updated = load_trades(db.local_path)[0]
    assert updated["status"] == "CANCELLED"


def test_pending_governor_blocks_replacement_when_thesis_is_not_materially_new(tmp_path: Path) -> None:
    db = _db(tmp_path)
    trades = [_pending_trade(
        "P1",
        thesis_dominance_score=58,
        return_probability_score=40,
        poi_quality_score=72,
        state_key="STATE::SELL::A",
        setup_type="STRUCTURE_CONTINUATION",
        setup_state="DETECTED",
        poi_type="order_block",
        poi_zone={"top": 4045.0, "bottom": 4043.0},
        trigger_state="AWAY_FROM_POI",
        trigger_score=42,
        displacement_score=10,
        details={"recent_sweep": {"time": "2026-07-16T12:00:00+00:00"}},
    )]
    save_trades(trades, db.local_path)
    governor = PendingGovernor({"pending_governor": {"enabled": True, "replace_min_dominance_delta": 8}})
    decision = _decision(
        thesis_dominance_score=70,
        return_probability_score=60,
        poi_quality_score=84,
        state_key="STATE::SELL::A",
        setup_type="STRUCTURE_CONTINUATION",
        setup_state="DETECTED",
        poi_type="order_block",
        poi_zone={"top": 4045.2, "bottom": 4043.2},
        trigger_state="AT_POI_WAIT_TRIGGER",
        trigger_score=45,
        displacement_score=12,
        details={"recent_sweep": {"time": "2026-07-16T12:00:00+00:00"}},
    )
    result = governor.review(decision, load_trades(db.local_path), database=db)
    assert result["action"] == "KEEP_EXISTING_PENDING"
    assert "replacement blocked" in str(result["reason"])
    assert load_trades(db.local_path)[0]["status"] == "PENDING"
