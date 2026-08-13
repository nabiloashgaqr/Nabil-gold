"""Phase-2 parity lock (full audit 2026-08-10): pending lifecycle works
identically in LOCAL mode (no Supabase) — cancellations, staleness mirror."""
import json

from services.database import DatabaseService


def _local_db(tmp_path, monkeypatch, rows):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    (tmp_path / "storage").mkdir(exist_ok=True)
    p = tmp_path / "storage" / "trades.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    db = DatabaseService({"database": {"local_fallback_file": str(p)}})
    return db, p


def test_local_cancel_pending_orders(tmp_path, monkeypatch):
    rows = [
        {"id": "P1", "status": "PENDING", "symbol": "XAU/USD", "type": "BUY"},
        {"id": "O1", "status": "OPEN", "symbol": "XAU/USD", "type": "BUY"},
    ]
    db, p = _local_db(tmp_path, monkeypatch, rows)
    n = db.cancel_pending_orders(reason="stale", symbol="XAU/USD",
                                 direction="BUY")
    assert n == 1
    after = {r["id"]: r["status"] for r in json.loads(p.read_text("utf-8"))}
    assert after == {"P1": "CANCELLED", "O1": "OPEN"}


def test_local_cancel_respects_direction_filter(tmp_path, monkeypatch):
    rows = [
        {"id": "P1", "status": "PENDING", "symbol": "XAU/USD", "type": "BUY"},
        {"id": "P2", "status": "PENDING", "symbol": "XAU/USD", "type": "SELL"},
    ]
    db, p = _local_db(tmp_path, monkeypatch, rows)
    n = db.cancel_pending_orders(reason="replace", direction="SELL")
    assert n == 1
    after = {r["id"]: r["status"] for r in json.loads(p.read_text("utf-8"))}
    assert after["P1"] == "PENDING" and after["P2"] == "CANCELLED"
