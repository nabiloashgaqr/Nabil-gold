"""Regression proofs for the live Telegram/MT5 divergence found 2026-08-11."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import types
from pathlib import Path

from scripts.demo_watchdog import _heartbeat_age
from scripts.run_tick_manager import TickManager
from services.mt5_executor import Mt5DemoExecutor, magic_for
from tests import mt5_fake
from utils.helpers import mutate_trades


def _append_worker(path: str, prefix: str, count: int) -> None:
    for i in range(count):
        mutate_trades(lambda rows, i=i: rows.append({"id": f"{prefix}-{i}"}), path)


def test_local_trade_transactions_do_not_lose_concurrent_writes(tmp_path) -> None:
    """Analysis + tick manager may append/update from separate processes."""
    path = tmp_path / "trades.json"
    path.write_text("[]", encoding="utf-8")
    workers = [
        mp.Process(target=_append_worker, args=(str(path), "analysis", 30)),
        mp.Process(target=_append_worker, args=(str(path), "tick", 30)),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert len(rows) == 60
    assert len({row["id"] for row in rows}) == 60


def test_tick_heartbeat_is_always_valid_json(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    TickManager._write_heartbeat()
    payload = json.loads((tmp_path / "tick_heartbeat.json").read_text(encoding="utf-8"))
    assert payload["ts"].endswith("+00:00")
    assert not (tmp_path / "tick_heartbeat.json.tmp").exists()


def test_watchdog_reports_invalid_heartbeat_instead_of_crashing(tmp_path) -> None:
    path = tmp_path / "empty.json"
    path.write_text("", encoding="utf-8")
    age, error = _heartbeat_age(str(path))
    assert age is None
    assert error and "JSONDecodeError" in error


class _DB:
    def __init__(self):
        self.updates = []

    def update_trade(self, tid, updates):
        self.updates.append((tid, dict(updates)))


class _Telegram:
    def __init__(self):
        self.signals = []

    def send_signal(self, decision):
        self.signals.append(decision)
        return True


def test_execution_signal_is_sent_once_and_uses_actual_fill() -> None:
    db = _DB()
    telegram = _Telegram()
    tm = TickManager({}, telegram=telegram, database=db)
    row = {
        "id": "T-ACTUAL", "telegram_signal_sent": False,
        "signal_snapshot": {
            "trade_id": "T-ACTUAL", "decision": "BUY", "current_price": 4300.0,
            "signal": {"entry": {"price": 4300.0}, "stop_loss": 4260.0,
                       "tp1": 4332.0, "tp2": 4364.0},
        },
    }
    tm._send_execution_signal(row, actual_entry=4301.25)
    tm._send_execution_signal(row, actual_entry=4301.25)
    assert len(telegram.signals) == 1
    assert telegram.signals[0]["signal"]["entry"]["price"] == 4301.25
    assert row["telegram_signal_sent"] is True
    assert db.updates[-1][1]["telegram_signal_sent"] is True


def test_cached_row_prevents_second_tp1_partial(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))

    class Executor:
        lot = 0.1
        last_partial = {"volume": 0.05, "price": 4310.0,
                        "remaining": 0.05, "ticket": 9, "existing": False}

        def __init__(self):
            self.calls = 0

        def _sym(self, symbol):
            return symbol

        def _position_by_magic(self, magic):
            return types.SimpleNamespace(ticket=9, volume=0.1, type=0,
                                         sl=4290.0, tp=4340.0, price_open=4300.0)

        def partial_close_at_tp1(self, *args):
            self.calls += 1
            return True

        def apply_stop(self, *args):
            return False

    row = {
        "id": "T1", "type": "BUY", "status": "OPEN", "symbol": "XAU/USD",
        "entry_price": 4300.0, "stop_loss": 4290.0, "initial_stop_loss": 4290.0,
        "tp1": 4310.0, "tp2": 4340.0, "partial_close": False,
        "mt5_ticket": 9,
    }
    db = _DB()
    tm = TickManager({}, database=db)
    tm._notify = lambda text: None
    ex = Executor()
    tick = types.SimpleNamespace(bid=4310.1, ask=4310.3)
    tm._handle_row(row, tick, ex, None)
    tm._handle_row(row, tick, ex, None)
    assert ex.calls == 1
    assert row["partial_close"] is True


def test_broker_history_blocks_a_second_tp1_slice(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    state = mt5_fake.FakeState()
    tid = "T-HISTORY"
    magic = magic_for(tid)
    state.positions = [mt5_fake._Pos(77, magic, 4290.0, 4340.0,
                                    volume=0.05, price_open=4300.0)]
    state.deals = [types.SimpleNamespace(
        magic=magic, comment="SS-demo-tp1", entry=mt5_fake.DEAL_ENTRY_OUT,
        volume=0.05, price=4310.0, time=10, position_id=77,
    )]
    monkeypatch.setitem(sys.modules, "MetaTrader5", mt5_fake.install(state))
    ex = Mt5DemoExecutor({"execution": {"demo": {}}})
    assert ex.partial_close_at_tp1(tid, 0.5, "XAU/USD") is True
    assert ex.last_partial["existing"] is True
    assert state.requests == []  # no second broker DEAL


def test_real_account_is_hard_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    state = mt5_fake.FakeState()
    mt5 = mt5_fake.install(state)
    mt5.ACCOUNT_TRADE_MODE_REAL = 2
    mt5.account_info = lambda: types.SimpleNamespace(trade_mode=2)
    monkeypatch.setitem(sys.modules, "MetaTrader5", mt5)
    ex = Mt5DemoExecutor({"execution": {"demo": {}}})
    ticket = ex.ensure_ticket("T-REAL", "BUY", "BUY_MARKET",
                              4300.0, 4260.0, 4380.0, "XAU/USD")
    assert ticket is None
    assert "REAL" in ex.last_error
    assert state.requests == []
    assert (tmp_path / ".demo_halt").exists()


def test_runtime_pid_and_heartbeat_files_are_ignored_and_not_tracked() -> None:
    root = Path(__file__).resolve().parents[1]
    ignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "*.pid" in ignore
    assert "tick_heartbeat.json" in ignore
    # Runtime files may legitimately exist while a VPS/test loop is alive; the
    # invariant is that future snapshots ignore them rather than redeploy them.
