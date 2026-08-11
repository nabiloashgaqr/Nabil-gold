"""Local-storage migration guarantees (operator directive 2026-08-10):
old paper + demo history merge into ONE continuous local book; new rows
continue on top; nothing lost."""
import json

from scripts.migrate_supabase_to_local import _write
from services.dashboard import merge_trade_rows


def _row(tid, created):
    return {"id": tid, "created_at": created, "status": "TP2_HIT",
            "symbol": "XAU/USD", "type": "BUY", "pnl_points": 100.0}


def test_merged_book_old_first_new_on_top():
    paper = [_row("P1", "2026-07-01T10:00:00Z"), _row("P2", "2026-07-20T10:00:00Z")]
    demo = [_row("D1", "2026-08-10T10:00:00Z"), _row("P2", "2026-07-20T10:00:00Z")]
    merged = merge_trade_rows(paper, demo)
    merged.sort(key=lambda r: str(r.get("created_at") or ""))
    ids = [r["id"] for r in merged]
    assert ids == ["P1", "P2", "D1"]  # dedup + chronological continuity


def test_write_roundtrip(tmp_path, monkeypatch):
    import scripts.migrate_supabase_to_local as m
    monkeypatch.setattr(m, "STORAGE", tmp_path)
    rows = [_row("X1", "2026-08-10T10:00:00Z")]
    assert _write("trades.json", rows) == 1
    assert json.loads((tmp_path / "trades.json").read_text(encoding="utf-8")) == rows
