from __future__ import annotations

import json
from pathlib import Path

from services.analyst_distillation import AnalystDistillationService
from services.database import DatabaseService


def _db(tmp_path: Path) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None}, "entry_timeframe": "15m"})
    db.analyst_labels_path = tmp_path / "analyst_labels.json"
    db.analyst_comparisons_path = tmp_path / "analyst_comparisons.json"
    db.setup_candidates_path = tmp_path / "setup_candidates.json"
    return db


def test_import_labels_from_json_file(tmp_path: Path) -> None:
    db = _db(tmp_path)
    service = AnalystDistillationService(db, {"symbol": "XAU/USD"})
    path = tmp_path / "labels.json"
    path.write_text(
        json.dumps(
            {
                "labels": [
                    {
                        "id": "L1",
                        "bias": "SELL",
                        "setup_type": "LIQUIDITY_REVERSAL",
                        "poi_type": "order_block",
                        "intended_entry": 4065.1,
                    },
                    {
                        "id": "L2",
                        "bias": "BUY",
                        "setup_type": "TREND_CONTINUATION",
                        "poi_type": "fvg",
                        "intended_entry": 4028.0,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    summary = service.import_labels_from_file(path, default_symbol="XAU/USD", analyst_name="nabil")
    labels = db.get_analyst_labels(limit=10, symbol="XAU/USD")
    assert summary["count"] == 2
    assert len(labels) == 2
    assert labels[0]["analyst_name"] == "nabil"


def test_compare_labels_and_setups_reports_partial_and_extra(tmp_path: Path) -> None:
    db = _db(tmp_path)
    service = AnalystDistillationService(
        db,
        {"symbol": "XAU/USD", "analyst_distillation": {"entry_tolerance_points": 80, "time_window_hours": 12, "match_threshold": 65, "partial_match_threshold": 45}},
    )
    labels = [
        {
            "id": "LABEL_MATCH",
            "symbol": "XAU/USD",
            "bias": "SELL",
            "setup_type": "LIQUIDITY_REVERSAL",
            "poi_type": "order_block",
            "sweep_side": "buy_side",
            "intended_entry": 4065.0,
            "created_at": "2026-07-15T10:00:00+00:00",
        },
        {
            "id": "LABEL_PARTIAL",
            "symbol": "XAU/USD",
            "bias": "BUY",
            "setup_type": "TREND_CONTINUATION",
            "poi_type": "fvg",
            "intended_entry": 4028.0,
            "created_at": "2026-07-15T11:00:00+00:00",
        },
    ]
    setups = [
        {
            "id": "SETUP_MATCH",
            "symbol": "XAU/USD",
            "direction": "SELL",
            "setup_type": "LIQUIDITY_REVERSAL",
            "poi_type": "order_block",
            "sweep_side": "buy_side",
            "entry_price": 4065.3,
            "poi_low": 4063.4,
            "poi_high": 4066.2,
            "created_at": "2026-07-15T09:30:00+00:00",
        },
        {
            "id": "SETUP_PARTIAL",
            "symbol": "XAU/USD",
            "direction": "BUY",
            "setup_type": "SMC_CONTEXT",
            "poi_type": "fvg",
            "sweep_side": "sell_side",
            "entry_price": 4045.0,
            "poi_low": 4042.0,
            "poi_high": 4046.0,
            "created_at": "2026-07-15T10:20:00+00:00",
        },
        {
            "id": "SETUP_EXTRA",
            "symbol": "XAU/USD",
            "direction": "SELL",
            "setup_type": "ORDER_BLOCK_PULLBACK",
            "poi_type": "order_block",
            "entry_price": 4070.0,
            "poi_low": 4068.0,
            "poi_high": 4071.0,
            "created_at": "2026-07-15T12:00:00+00:00",
        },
    ]
    summary = service.compare_labels_and_setups(labels, setups, symbol="XAU/USD", save=False)
    assert summary["labels_considered"] == 2
    assert summary["matched_labels"] == 1
    assert summary["partial_matches"] == 1
    assert summary["extra_bot_setups"] == 1
    assert summary["coverage_rate_pct"] == 100.0
    classes = {item["classification"] for item in summary["comparisons"]}
    assert "MATCHED" in classes
    assert "PARTIAL_MATCH" in classes
