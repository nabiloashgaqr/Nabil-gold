from __future__ import annotations

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.database import DatabaseService
from utils.helpers import load_trades, save_trades
import scripts.enrich_trade as enrich


def _db(tmp_path: Path) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None, "local_fallback_file": str(tmp_path / 'trades.json')}})
    db.local_path = tmp_path / 'trades.json'
    return db


def test_enrich_trade_close_now_marks_manual_close(monkeypatch, tmp_path: Path) -> None:
    db = _db(tmp_path)
    save_trades([
        {
            "id": "TRADE_CLOSE_NOW",
            "symbol": "XAU/USD",
            "type": "SELL",
            "status": "OPEN",
            "entry_price": 4020.0,
            "stop_loss": 4045.0,
            "tp1": 4000.0,
            "tp2": 3965.0,
            "created_at": "2026-07-20T09:00:00Z",
            "entry_time": "2026-07-20T09:00:00Z",
        }
    ], db.local_path)

    monkeypatch.setattr(enrich, "load_config", lambda: {"database": {"url": None, "key": None, "local_fallback_file": str(db.local_path)}})
    monkeypatch.setattr(enrich, "DatabaseService", lambda cfg: db)
    monkeypatch.setattr(enrich, "_write", lambda _db, tid, updates: (_db.update_trade(tid, updates) or True))
    monkeypatch.setenv("TRADE_ID", "TRADE_CLOSE_NOW")
    monkeypatch.setenv("ACTION", "close_now")
    monkeypatch.setenv("CURRENT_PRICE", "3992")
    monkeypatch.setenv("CLOSE_REASON", "Operator manual exit")
    monkeypatch.delenv("PNL_POINTS", raising=False)
    monkeypatch.delenv("HIGH_PRICE", raising=False)
    monkeypatch.delenv("LOW_PRICE", raising=False)

    rc = enrich.main()
    rows = load_trades(db.local_path)
    assert rc == 0
    assert rows[0]["status"] == "MANUAL_CLOSE"
    assert rows[0]["result"] == "WIN"
    assert rows[0]["close_price"] == 3992.0
    assert rows[0]["reasons"] == ["Operator manual exit"]
