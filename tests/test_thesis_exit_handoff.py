"""Thesis-exit handoff tests (operator directive 2026-08-10): the embedded
manager DECIDES, the tick manager EXECUTES at the broker first, cards are
truthful. Virtual bookings never leak into the demo book."""
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import mt5_fake  # noqa: E402
from services.demo_handoff import DemoHandoffDB  # noqa: E402
from scripts.run_tick_manager import TickManager  # noqa: E402


class _RecDB:
    def __init__(self):
        self.updates = []

    def update_trade(self, tid, fields, *a, **k):
        self.updates.append((tid, dict(fields)))
        return True

    def __getattr__(self, name):
        return lambda *a, **k: None


def test_handoff_converts_thesis_exit_to_request():
    db = _RecDB()
    w = DemoHandoffDB(db)
    w.update_trade("T1", {"status": "THESIS_EXIT", "close_price": 1.0})
    assert db.updates[-1][1] == {"requested_exit": True,
                                 "requested_exit_reason": "THESIS_EXIT"}


def test_handoff_converts_scale_out_to_partial_request():
    db = _RecDB()
    w = DemoHandoffDB(db)
    w.update_trade("T1", {"status": "THESIS_SCALE_OUT"})
    assert db.updates[-1][1] == {"requested_partial": True}


def test_handoff_drops_virtual_bookings():
    db = _RecDB()
    w = DemoHandoffDB(db)
    w.update_trade("T1", {"partial_close": True, "status": "TP1_HIT",
                          "stop_loss": 4310.0, "sl_moved_to_entry": True})
    assert db.updates == []  # nothing virtual reaches the demo book


def test_handoff_allows_cancellation():
    db = _RecDB()
    w = DemoHandoffDB(db)
    w.update_trade("T1", {"status": "CANCELLED"})
    assert db.updates[-1][1]["status"] == "CANCELLED"


class _CloseExec:
    last_close = {"price": 4345.0, "volume": 0.1, "ticket": 42}
    last_partial = {"volume": 0.05, "price": 4345.0, "remaining": 0.05,
                    "ticket": 42}

    def __init__(self, ok=True):
        self.ok = ok
        self.closed = 0

    def close_position(self, tid, sym):
        self.closed += 1
        return self.ok

    def partial_close_at_tp1(self, tid, frac, sym):
        return self.ok

    def apply_stop(self, *a, **k):
        return False

    def _sym(self, symbol):
        return symbol


def test_tick_executes_requested_exit_first(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _RecDB()
    tm = TickManager({})
    tm.database = db
    msgs = []
    tm._notify = msgs.append
    row = {"id": "T1", "type": "BUY", "status": "OPEN", "symbol": "XAU/USD",
           "entry_price": 4330.0, "requested_exit": True,
           "created_at": datetime.now(timezone.utc).isoformat()}
    tick = types.SimpleNamespace(bid=4345.0, ask=4345.2)

    class _Pos:
        ticket = 42
    ex = _CloseExec()
    tm._handle_row({**row}, tick, _WrapPos(ex, _Pos()), None)
    assert ex.closed == 1
    assert db.updates[-1][1]["status"] == "THESIS_EXIT"
    assert any("thesis exit closed" in m for m in msgs)


def test_tick_thesis_partial_executes(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _RecDB()
    tm = TickManager({})
    tm.database = db
    msgs = []
    tm._notify = msgs.append
    row = {"id": "T1", "type": "BUY", "status": "OPEN", "symbol": "XAU/USD",
           "entry_price": 4330.0, "requested_partial": True,
           "created_at": datetime.now(timezone.utc).isoformat()}
    tick = types.SimpleNamespace(bid=4345.0, ask=4345.2)

    class _Pos:
        ticket = 42
    ex = _CloseExec()
    tm._handle_row({**row}, tick, _WrapPos(ex, _Pos()), None)
    assert any("thesis scale-out closed" in m for m in msgs)


class _WrapPos:
    def __init__(self, inner, pos):
        self._i = inner
        self._p = pos

    def __getattr__(self, name):
        return getattr(self._i, name)

    def _position_by_magic(self, magic):
        return self._p
