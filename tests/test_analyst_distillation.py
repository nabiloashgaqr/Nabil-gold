from __future__ import annotations

from pathlib import Path

from services.analyst_distillation import AnalystDistillationService
from services.database import DatabaseService


def _db(tmp_path: Path) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None}})
    db.analyst_labels_path = tmp_path / "analyst_labels.json"
    db.analyst_comparisons_path = tmp_path / "analyst_comparisons.json"
    db.setup_candidates_path = tmp_path / "setup_candidates.json"
    return db


def test_save_and_fetch_analyst_label_local_fallback(tmp_path: Path) -> None:
    db = _db(tmp_path)
    label_id = db.save_analyst_label(
        {
            "id": "LABEL_1",
            "symbol": "XAU/USD",
            "bias": "SELL",
            "setup_type": "LIQUIDITY_REVERSAL",
            "poi_type": "order_block",
            "intended_entry": 4065.1,
            "created_at": "2026-07-15T10:00:00+00:00",
        }
    )
    labels = db.get_analyst_labels(limit=5, symbol="XAU/USD")
    assert label_id == "LABEL_1"
    assert labels
    assert labels[0]["setup_type"] == "LIQUIDITY_REVERSAL"
    assert labels[0]["bias"] == "SELL"


def test_best_match_scores_direction_setup_and_entry_proximity(tmp_path: Path) -> None:
    db = _db(tmp_path)
    service = AnalystDistillationService(db, {"analyst_distillation": {"entry_tolerance_points": 80, "time_window_hours": 12, "match_threshold": 65}})
    label = {
        "id": "LABEL_2",
        "symbol": "XAU/USD",
        "bias": "SELL",
        "setup_type": "LIQUIDITY_REVERSAL",
        "poi_type": "order_block",
        "sweep_side": "buy_side",
        "intended_entry": 4065.1,
        "created_at": "2026-07-15T10:00:00+00:00",
    }
    setup = {
        "id": "SETUP_2",
        "symbol": "XAU/USD",
        "direction": "SELL",
        "setup_type": "LIQUIDITY_REVERSAL",
        "poi_type": "order_block",
        "sweep_side": "buy_side",
        "entry_price": 4065.4,
        "poi_low": 4063.4,
        "poi_high": 4066.2,
        "created_at": "2026-07-15T09:30:00+00:00",
    }
    comparison = service.best_match_for_label(label, [setup])
    assert comparison["classification"] == "MATCHED"
    assert comparison["match_score"] >= 65
    assert comparison["payload"]["entry_inside_poi"] is True


def test_compare_recent_counts_missed_labels(tmp_path: Path) -> None:
    db = _db(tmp_path)
    service = AnalystDistillationService(db, {"symbol": "XAU/USD", "analyst_distillation": {"entry_tolerance_points": 80, "time_window_hours": 12, "match_threshold": 65}})
    db.save_analyst_label(
        {
            "id": "LABEL_A",
            "symbol": "XAU/USD",
            "bias": "SELL",
            "setup_type": "LIQUIDITY_REVERSAL",
            "poi_type": "order_block",
            "sweep_side": "buy_side",
            "intended_entry": 4065.1,
            "created_at": "2026-07-15T10:00:00+00:00",
        }
    )
    db.save_analyst_label(
        {
            "id": "LABEL_B",
            "symbol": "XAU/USD",
            "bias": "BUY",
            "setup_type": "TREND_CONTINUATION",
            "poi_type": "fvg",
            "intended_entry": 4028.0,
            "created_at": "2026-07-15T11:00:00+00:00",
        }
    )
    db.save_setup_candidate(
        {
            "id": "SETUP_A",
            "symbol": "XAU/USD",
            "direction": "SELL",
            "setup_type": "LIQUIDITY_REVERSAL",
            "poi_type": "order_block",
            "sweep_side": "buy_side",
            "entry_price": 4065.3,
            "poi_low": 4063.4,
            "poi_high": 4066.2,
            "created_at": "2026-07-15T09:30:00+00:00",
        }
    )
    summary = service.compare_recent(symbol="XAU/USD", limit=10)
    assert summary["labels_considered"] == 2
    assert summary["matched_labels"] == 1
    assert summary["missed_labels"] == 1
