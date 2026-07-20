from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

import scripts.run_close_trade_now as mclose
from services.database import DatabaseService
from utils.helpers import load_trades, save_trades


def _db(tmp_path: Path) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None, "local_fallback_file": str(tmp_path / 'trades.json')}})
    db.local_path = tmp_path / 'trades.json'
    return db


def _seed_open_trade(db: DatabaseService, *, trade_id: str = "TRADE_TEST_001", side: str = "SELL") -> None:
    save_trades([
        {
            "id": trade_id,
            "symbol": "XAU/USD",
            "type": side,
            "status": "OPEN",
            "entry_price": 4020.0,
            "stop_loss": 4045.0,
            "tp1": 4000.0,
            "tp2": 3965.0,
            "created_at": "2026-07-20T09:00:00+00:00",
            "entry_time": "2026-07-20T09:00:00+00:00",
        }
    ], db.local_path)


def test_close_trade_now_updates_trade_to_manual_close(monkeypatch, tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_open_trade(db)

    monkeypatch.setattr(mclose, "DatabaseService", lambda _cfg: db)
    monkeypatch.setattr(mclose, "TelegramService", lambda _cfg: type("T", (), {"send_trade_events": lambda *a, **k: True})())
    monkeypatch.setattr(mclose, "_resolve_live_price", lambda cfg, symbol: (3992.0, "twelvedata"))

    result = mclose.close_trade_now("TRADE_TEST_001", reason="Manual take profit", send_telegram=False, config={"symbol": "XAU/USD"})
    trades = load_trades(db.local_path)
    assert result["result"] == "WIN"
    assert trades[0]["status"] == "MANUAL_CLOSE"
    assert trades[0]["close_price"] == 3992.0
    assert trades[0]["final_pnl"] > 0
    assert trades[0]["reasons"] == ["Manual take profit"]


def test_close_trade_now_rejects_pending_trade(monkeypatch, tmp_path: Path) -> None:
    db = _db(tmp_path)
    save_trades([
        {
            "id": "TRADE_PENDING",
            "symbol": "XAU/USD",
            "type": "SELL",
            "status": "PENDING",
            "entry_price": 4020.0,
            "created_at": "2026-07-20T09:00:00+00:00",
            "entry_time": "2026-07-20T09:00:00+00:00",
        }
    ], db.local_path)

    monkeypatch.setattr(mclose, "DatabaseService", lambda _cfg: db)
    monkeypatch.setattr(mclose, "TelegramService", lambda _cfg: type("T", (), {"send_trade_events": lambda *a, **k: True})())
    monkeypatch.setattr(mclose, "_resolve_live_price", lambda cfg, symbol: (3992.0, "twelvedata"))

    try:
        mclose.close_trade_now("TRADE_PENDING", reason="x", send_telegram=False, config={"symbol": "XAU/USD"})
        assert False, "Expected RuntimeError for pending trade"
    except RuntimeError as exc:
        assert "not closeable" in str(exc)
