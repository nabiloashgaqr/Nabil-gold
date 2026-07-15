from __future__ import annotations

from pathlib import Path

from services.database import DatabaseService


def test_cancel_pending_orders_can_filter_by_symbol_and_direction(tmp_path: Path) -> None:
    db = DatabaseService({"database": {"url": None, "key": None, "local_fallback_file": str(tmp_path / 'trades.json')}})
    db.local_path = tmp_path / 'trades.json'
    seed = [
        {"id": "P1", "status": "PENDING", "symbol": "XAU/USD", "type": "SELL"},
        {"id": "P2", "status": "PENDING", "symbol": "XAU/USD", "type": "BUY"},
        {"id": "P3", "status": "PENDING", "symbol": "WTI/USD", "type": "SELL"},
        {"id": "O1", "status": "OPEN", "symbol": "XAU/USD", "type": "SELL"},
    ]
    from utils.helpers import save_trades, load_trades
    save_trades(seed, db.local_path)

    cancelled = db.cancel_pending_orders(symbol="XAU/USD", direction="SELL", reason="replace")
    trades = load_trades(db.local_path)

    assert cancelled == 1
    statuses = {t["id"]: t["status"] for t in trades}
    assert statuses["P1"] == "CANCELLED"
    assert statuses["P2"] == "PENDING"
    assert statuses["P3"] == "PENDING"
    assert statuses["O1"] == "OPEN"
