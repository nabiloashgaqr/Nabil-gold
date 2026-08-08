"""Phase-1 demo-branch tests with a fake MetaTrader5 (six mandated cases)."""
from __future__ import annotations

import sys
import time as time_mod

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.mt5_fake as fake  # noqa: E402


def _install(state):
    import sys as _sys
    _sys.modules["MetaTrader5"] = fake.install(state)
    return state


def test_mt5_feed_timezone_snapping():
    from services import mt5_feed
    for raw_off, expected in ((0, 0), (7200, 7200), (10800, 10800), (7000, 7200)):
        state = fake.FakeState()
        state.server_time = int(time_mod.time()) + raw_off
        _install(state)
        mt5_feed._offset_cache.clear()
        _install(state)
        assert mt5_feed.time_offset_seconds() == expected


def test_mt5_feed_payload_contract_and_fallback():
    state = fake.FakeState()
    _install(state)
    now = int(time_mod.time())
    state.rates = [{"time": now - 300, "open": 4300.0, "high": 4301.0,
                    "low": 4299.0, "close": 4300.5}] * 220
    from services import mt5_feed
    payload = mt5_feed.get_candles("XAU/USD", "5m", 220, {"XAU/USD": "XAUUSD"})
    assert payload and payload["source"] == "mt5"
    assert len(payload["data"]) == 220
    assert set(payload["data"][0]) >= {"time", "open", "high", "low", "close"}


def test_mt5_executor_idempotent_magic():
    state = _install(fake.FakeState())
    from services.mt5_executor import Mt5DemoExecutor
    ex = Mt5DemoExecutor({"execution": {"demo": {}}})
    t1 = ex.ensure_ticket("T1", "BUY", "MARKET", 4300, 4280, 4340, "XAU/USD")
    t2 = ex.ensure_ticket("T1", "BUY", "MARKET", 4300, 4280, 4340, "XAU/USD")
    assert t1 == t2
    assert len(state.positions) == 1


def test_mt5_partial_close_fallback():
    state = _install(fake.FakeState())
    state.fail_partial = True
    from services.mt5_executor import Mt5DemoExecutor
    ex = Mt5DemoExecutor({"execution": {"demo": {}}})
    ticket = ex.ensure_ticket("T2", "BUY", "MARKET", 4300, 4280, 4340, "XAU/USD")
    assert ticket
    n_before = len(state.requests)
    assert ex.partial_close_at_tp1("T2", 0.5, "XAU/USD") is True
    kinds = [r.get("comment") for r in state.requests[n_before:]]
    assert "SS-demo-tp1" in kinds          # refused partial attempted
    assert len(state.requests[n_before:]) == 3  # partial + close-all + reopen


def test_reconcile_halt_on_sl_drift(tmp_path, monkeypatch):
    state = _install(fake.FakeState())
    from services import mt5_executor
    monkeypatch.chdir(tmp_path)
    ex = mt5_executor.Mt5DemoExecutor({"execution": {"demo": {}}})
    ex.ensure_ticket("T3", "BUY", "MARKET", 4300, 4280, 4340, "XAU/USD")
    mism = ex.reconcile([{"id": "T3", "mt5_ticket": 1001, "stop_loss": 4290.0,
                          "type": "BUY"}])
    assert mism and "SL drift" in mism[0]
    assert ex.halted()
    (tmp_path / mt5_executor.HALT_FILE).unlink()


def test_demo_table_routing(monkeypatch):
    monkeypatch.setenv("TRADES_TABLE", "trades_demo")
    from services.database import DatabaseService
    ds = DatabaseService({"database": {}})
    assert ds.trades_table == "trades_demo"
    monkeypatch.setenv("TRADES_TABLE", "")
    ds2 = DatabaseService({"database": {}})
    assert ds2.trades_table == "trades"


def test_demo_cards_go_to_demo_chat(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "mt5_demo")
    monkeypatch.setenv("TELEGRAM_DEMO_CHAT_ID", "-100DEMO")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100MAIN")
    from services.telegram_bot import TelegramService
    svc = TelegramService({"telegram": {"bot_token": "x", "chat_id": "-100MAIN"}})
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True}

    def _post(url, **kw):
        captured.update(kw.get("json") or {})
        return _Resp()

    import requests
    monkeypatch.setattr(svc.session, "post", _post)
    assert svc.send_message("hello")
    assert captured["chat_id"] == "-100DEMO"
    assert captured["text"].startswith("🧪 DEMO · ")
