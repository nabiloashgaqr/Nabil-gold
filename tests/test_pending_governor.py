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
    return {
        "id": trade_id,
        "symbol": "XAU/USD",
        "type": direction,
        "status": "PENDING",
        "entry_price": 4040.6,
        "signal_snapshot": {
            "setup_context": {
                "thesis_dominance_score": ctx.get("thesis_dominance_score", 60),
                "return_probability_score": ctx.get("return_probability_score", 55),
                "poi_quality_score": ctx.get("poi_quality_score", 80),
                "selection_role": ctx.get("selection_role", "PRIMARY"),
            }
        },
    }


def _decision(direction: str = "SELL", **ctx) -> dict:
    return {
        "decision": direction,
        "symbol": "XAU/USD",
        "setup_context": {
            "thesis_dominance_score": ctx.get("thesis_dominance_score", 70),
            "return_probability_score": ctx.get("return_probability_score", 65),
            "poi_quality_score": ctx.get("poi_quality_score", 84),
            "selection_role": ctx.get("selection_role", "PRIMARY"),
        },
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
    trades = [_pending_trade("P1", thesis_dominance_score=58, return_probability_score=40, poi_quality_score=72)]
    save_trades(trades, db.local_path)
    governor = PendingGovernor({"pending_governor": {"enabled": True, "replace_min_dominance_delta": 8}})
    decision = _decision(thesis_dominance_score=72, return_probability_score=64, poi_quality_score=86)
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
