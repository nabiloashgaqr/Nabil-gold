from __future__ import annotations

from pathlib import Path
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


def test_enrich_trade_trailing_sl_update_moves_stop_and_keeps_trade_open(monkeypatch, tmp_path: Path) -> None:
    db = _db(tmp_path)
    save_trades([
        {
            "id": "TRADE_TRAIL_UPDATE",
            "symbol": "XAU/USD",
            "type": "BUY",
            "status": "TP1_HIT",
            "entry_price": 4012.15,
            "stop_loss": 4012.15,
            "tp1": 4062.15,
            "tp2": 4102.15,
            "sl_moved_to_entry": True,
            "partial_close": True,
            "updates_sent": ["TP1_HIT", "MOVE_SL_TO_BE"],
            "created_at": "2026-07-20T13:03:18Z",
            "entry_time": "2026-07-20T13:03:18Z",
        }
    ], db.local_path)

    monkeypatch.setattr(enrich, "load_config", lambda: {"database": {"url": None, "key": None, "local_fallback_file": str(db.local_path)}})
    monkeypatch.setattr(enrich, "DatabaseService", lambda cfg: db)
    monkeypatch.setattr(enrich, "_write", lambda _db, tid, updates: (_db.update_trade(tid, updates) or True))
    monkeypatch.setenv("TRADE_ID", "TRADE_TRAIL_UPDATE")
    monkeypatch.setenv("ACTION", "trailing_sl_update")
    monkeypatch.setenv("CURRENT_PRICE", "4072.75")
    monkeypatch.setenv("HIGH_PRICE", "4072.75")
    monkeypatch.setenv("LOW_PRICE", "4000")
    monkeypatch.setenv("NEW_STOP_LOSS", "4057.85")
    monkeypatch.delenv("PNL_POINTS", raising=False)
    monkeypatch.delenv("CLOSE_REASON", raising=False)

    rc = enrich.main()
    rows = load_trades(db.local_path)
    assert rc == 0
    assert rows[0]["status"] == "TP1_HIT"
    assert rows[0]["stop_loss"] == 4057.85
    assert rows[0]["sl_moved_to_entry"] is True
    assert rows[0]["current_price"] == 4072.75
    assert rows[0]["current_pnl"] == 606.0
    assert rows[0]["current_pnl_points"] == 606.0
    assert "TRAILING_SL_UPDATED" in rows[0]["updates_sent"]
