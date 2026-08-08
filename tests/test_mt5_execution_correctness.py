"""Execution-correctness tests for the MT5 demo layer (lot / partial / exits).

Operator directive: lot 0.1, TP1 books HALF, $/point math and XAUUSD symbol
must all be verified. These tests are fault-injection: re-introducing the
old bugs (unsnap volumes, TP1 booked on failure, SL_HIT-everything exits,
risk/10 wiring) makes them fail.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import mt5_fake  # noqa: E402

from scripts.run_tick_manager import (  # noqa: E402
    TickManager, classify_broker_exit)
from services.mt5_executor import (  # noqa: E402
    Mt5DemoExecutor, magic_for, plan_partial_close)


# ── plan_partial_close: broker volume constraints ──────────────────────────

def test_plan_partial_normal_half_of_01():
    part, remaining = plan_partial_close(0.10, 0.5, step=0.01, min_vol=0.01)
    assert part == 0.05 and remaining == 0.05


def test_plan_partial_min_vol_blocks_split_closes_all():
    # broker minimum 0.1: half-slices of 0.05 are unbookable → close ALL
    part, remaining = plan_partial_close(0.10, 0.5, step=0.01, min_vol=0.10)
    assert part == 0.10 and remaining is None


def test_plan_partial_remainder_below_min_closes_all():
    part, remaining = plan_partial_close(0.15, 0.5, step=0.01, min_vol=0.10)
    assert part == 0.15 and remaining is None  # 0.07 / 0.08 both < 0.10


def test_plan_partial_snaps_down_to_step():
    part, remaining = plan_partial_close(0.21, 0.5, step=0.01, min_vol=0.10)
    assert part == 0.10 and remaining == 0.11


# ── classify_broker_exit ───────────────────────────────────────────────────

def test_classify_buy_exits():
    assert classify_broker_exit("BUY", 4330.0, 4300.0, 4320.0, 4330.0) == "TP2_HIT"
    assert classify_broker_exit("BUY", 4293.0, 4300.0, 4293.0, 4330.0) == "SL_HIT"
    assert classify_broker_exit("BUY", 4318.0, 4300.0, 4315.0, 4330.0) == "TRAILING_SL_HIT"


def test_classify_sell_exits():
    assert classify_broker_exit("SELL", 4270.0, 4300.0, 4280.0, 4270.0) == "TP2_HIT"
    assert classify_broker_exit("SELL", 4307.0, 4300.0, 4307.0, 4270.0) == "SL_HIT"
    assert classify_broker_exit("SELL", 4285.0, 4300.0, 4288.0, 4270.0) == "TRAILING_SL_HIT"


# ── executor against the fake terminal ─────────────────────────────────────

def _executor(monkeypatch, state):
    monkeypatch.setitem(sys.modules, "MetaTrader5", mt5_fake.install(state))
    return Mt5DemoExecutor({"execution": {"demo": {}}})


def test_partial_close_books_exact_half(monkeypatch):
    state = mt5_fake.FakeState()
    state.positions.append(mt5_fake._Pos(7, magic_for("T1"), 4293.0, 4330.0,
                                         volume=0.10))
    ex = _executor(monkeypatch, state)
    assert ex.partial_close_at_tp1("T1", 0.5, "XAU/USD") is True
    tp1_reqs = [r for r in state.requests if r.get("comment") == "SS-demo-tp1"]
    assert len(tp1_reqs) == 1 and tp1_reqs[0]["volume"] == 0.05


def test_partial_close_min_vol_01_closes_whole_without_reopen(monkeypatch):
    state = mt5_fake.FakeState()
    state.volume_min = 0.10
    state.positions.append(mt5_fake._Pos(7, magic_for("T2"), 4293.0, 4330.0,
                                         volume=0.10))
    ex = _executor(monkeypatch, state)
    assert ex.partial_close_at_tp1("T2", 0.5, "XAU/USD") is True
    assert len(state.requests) == 1           # ONE close-all, no reopen
    assert state.requests[0]["volume"] == 0.10


def test_partial_refusal_fallback_keeps_snapped_remainder(monkeypatch):
    state = mt5_fake.FakeState()
    state.fail_partial = True
    state.positions.append(mt5_fake._Pos(7, magic_for("T3"), 4293.0, 4330.0,
                                         volume=0.10))
    ex = _executor(monkeypatch, state)
    assert ex.partial_close_at_tp1("T3", 0.5, "XAU/USD") is True
    volumes = [r["volume"] for r in state.requests]
    assert volumes == [0.05, 0.10, 0.05]      # refused partial, close-all, reopen


def test_last_exit_reads_the_broker_deal(monkeypatch):
    state = mt5_fake.FakeState()
    state.deals.append(mt5_fake._Deal(magic_for("T9"), 4330.0, 300.0, 123))
    ex = _executor(monkeypatch, state)
    out = ex.last_exit("T9")
    assert out == {"price": 4330.0, "profit": 300.0, "time": 123}
    assert ex.last_exit("UNKNOWN") is None


# ── tick manager wiring ────────────────────────────────────────────────────

class _StubDB:
    def __init__(self):
        self.updates = []

    def update_trade(self, tid, fields):
        self.updates.append((tid, dict(fields)))


class _StubExecutor:
    def __init__(self, partial_ok=True, position=True, exit_info=None):
        self.partial_ok = partial_ok
        self.position = position
        self.exit_info = exit_info

    def _position_by_magic(self, magic):
        if not self.position:
            return None
        return types.SimpleNamespace(ticket=1, volume=0.10, type=0,
                                     sl=4293.0, tp=4330.0, price_open=4300.0)

    def partial_close_at_tp1(self, tid, frac, sym):
        return self.partial_ok

    def apply_stop(self, *a, **k):
        return True

    def last_exit(self, tid):
        return self.exit_info


def _row(**over):
    base = {"id": "T1", "type": "BUY", "status": "OPEN", "symbol": "XAU/USD",
            "entry_price": 4300.0, "stop_loss": 4293.0,
            "initial_stop_loss": 4293.0, "tp1": 4310.0, "tp2": 4330.0}
    base.update(over)
    return base


def _tm():
    return TickManager({})


def test_tp1_failure_is_not_booked(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    ex = _StubExecutor(partial_ok=False)
    tick = types.SimpleNamespace(bid=4309.5, ask=4309.7)
    tm._handle_row(_row(), tick, ex, None)
    assert db.updates == []  # nothing claimed; retry next tick


def test_tp1_success_is_booked(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    ex = _StubExecutor(partial_ok=True)
    tick = types.SimpleNamespace(bid=4309.5, ask=4309.7)
    tm._handle_row(_row(), tick, ex, None)
    assert any(u[1].get("partial_close") is True for u in db.updates)
    assert any(u[1].get("status") == "TP1_HIT" for u in db.updates)


def test_broker_exit_uses_deal_price_and_labels_tp2(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    ex = _StubExecutor(position=False,
                       exit_info={"price": 4330.0, "profit": 300.0, "time": 1})
    tick = types.SimpleNamespace(bid=4329.0, ask=4329.2)
    tm._handle_row(_row(), tick, ex, None)
    assert db.updates[-1][1]["status"] == "TP2_HIT"
    assert db.updates[-1][1]["close_price"] == 4330.0   # deal price, not tick
    assert db.updates[-1][1]["pnl_points"] == 300.0     # 0.1 lot: $30 → 300 pts


def test_be_wiring_respects_min_rr_gate(monkeypatch):
    """$34.57 risk = 345.7 pts; 160 pts favorable must NOT arm BE
    (0.5 × 345.7 = 172.85 needed). The old risk/10 wiring armed it here.

    NOTE: the unified 150/40 trailing may still ratchet the stop (that is
    rule-correct and separate from the discrete BE move); we assert only
    that no sl_moved_to_entry booking happened."""
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    ex = _StubExecutor()
    row = _row(entry_price=4327.57, stop_loss=4293.0, initial_stop_loss=4293.0,
               tp1=4367.0, tp2=4425.0, sl_moved_to_entry=False)
    tick = types.SimpleNamespace(bid=4343.0, ask=4343.57)  # +$16 favorable
    tm._handle_row(row, tick, ex, None)
    assert not any(u[1].get("sl_moved_to_entry") for u in db.updates)
    assert not any(u[1].get("stop_loss") == 4327.57 for u in db.updates)


# ── broker symbol suffix (JustMarkets: XAUUSD.s) ───────────────────────────

def test_shipped_config_maps_to_broker_suffix():
    """config.json is the single source; reverting to plain XAUUSD makes the
    whole execution layer go blind on JustMarkets."""
    import json
    from pathlib import Path as _P
    cfg = json.loads((_P(__file__).resolve().parents[1] / "config.json")
                     .read_text(encoding="utf-8"))
    smap = cfg["execution"]["demo"]["symbol_map"]
    assert smap["XAU/USD"] == "XAUUSD.s"


def test_executor_sym_uses_map_and_fallback():
    ex = Mt5DemoExecutor({"execution": {"demo": {
        "symbol_map": {"XAU/USD": "XAUUSD.s"}}}})
    assert ex._sym("XAU/USD") == "XAUUSD.s"
    assert ex._sym("XAU/USD2") == "XAUUSD2"  # unmapped: slash-strip fallback


def test_tick_loop_symbol_lookup_uses_the_map(monkeypatch):
    """The loop must ask MT5 for the MAPPED symbol; looking up the raw
    XAU/USD-stripped name returns None on suffix brokers = loop sees nothing."""
    state = mt5_fake.FakeState()
    fake = mt5_fake.install(state)
    seen = []
    fake.symbol_info_tick = lambda s: (seen.append(s), None)[1]
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    import scripts.run_tick_manager as rtm
    tm = rtm.TickManager({"execution": {"demo": {
        "symbol_map": {"XAU/USD": "XAUUSD.s"}}}})
    tm.database = _StubDB()
    rows = [_row()]
    tm._magic_rows = lambda: rows

    import time as _time
    calls = {"n": 0}
    orig_sleep = _time.sleep

    def _stop_after_first(*a, **k):
        calls["n"] += 1
        if calls["n"] >= 1:
            raise KeyboardInterrupt
        orig_sleep(0)

    monkeypatch.setattr(rtm.time, "sleep", _stop_after_first)
    try:
        tm.run_forever()
    except KeyboardInterrupt:
        pass
    assert seen and seen[0] == "XAUUSD.s"


def test_be_wiring_arms_when_gate_passes(monkeypatch):
    """Same wide stop but +$20 favorable (200 pts ≥ 172.85) → BE arms at entry."""
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    ex = _StubExecutor()
    row = _row(entry_price=4327.57, stop_loss=4293.0, initial_stop_loss=4293.0,
               tp1=4367.0, tp2=4425.0, sl_moved_to_entry=False)
    tick = types.SimpleNamespace(bid=4347.0, ask=4347.57)  # +$20 favorable
    tm._handle_row(row, tick, ex, None)
    assert any(u[1].get("sl_moved_to_entry") is True
               and u[1].get("stop_loss") == 4327.57 for u in db.updates)
