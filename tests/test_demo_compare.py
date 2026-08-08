"""Phase-3 compare logic tests (no VPS needed)."""
from __future__ import annotations

import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.demo_compare_report import compare_books, report_text  # noqa: E402


def _row(tid, **kw):
    base = {"id": tid, "status": "OPEN", "stop_loss": 4280.0, "tp1": 4340.0,
            "tp2": 4400.0, "entry_price": 4300.0, "closed_at": None,
            "final_pnl_points": None}
    base.update(kw)
    return base


def test_identical_books_report_identical():
    rows = [_row("T1"), _row("T2", status="TP1_HIT")]
    cmp = compare_books(rows, [dict(r) for r in rows])
    assert cmp["identical"]
    assert "identical" in report_text(cmp, "2026-08-08")


def test_field_divergence_flagged():
    cmp = compare_books([_row("T1")], [_row("T1", stop_loss=4290.0)])
    assert not cmp["identical"]
    assert any("stop_loss" in d for d in cmp["field_diffs"])


def test_missing_trade_flagged_both_ways():
    cmp = compare_books([_row("T1"), _row("T2")], [_row("T2"), _row("T3")])
    assert cmp["only_paper"] == ["T1"]
    assert cmp["only_demo"] == ["T3"]
    assert "DIVERGENCE" in report_text(cmp, "2026-08-08")
