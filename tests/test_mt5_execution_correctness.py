"""Execution-correctness tests for the MT5 demo layer (lot / partial / exits).

Operator directive: lot 0.1, TP1 books HALF, $/point math and XAUUSD symbol
must all be verified. These tests are fault-injection: re-introducing the
old bugs (unsnap volumes, TP1 booked on failure, SL_HIT-everything exits,
risk/10 wiring) makes them fail.
"""
import sys
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import mt5_fake  # noqa: E402

from scripts.run_tick_manager import (  # noqa: E402
    TickManager, classify_broker_exit)
from services.mt5_executor import (  # noqa: E402
    Mt5DemoExecutor, magic_for, plan_partial_close)


# ── plan_partial_close: ONLY ever the snapped fractional slice ─────────────

def test_plan_partial_normal_half_of_01():
    part, remaining = plan_partial_close(0.10, 0.5, step=0.01)
    assert part == 0.05 and remaining == 0.05


def test_plan_partial_never_escalates_to_whole():
    """Operator directive: close the HALF. Even when the leftover would sit
    below a broker minimum, the plan must still describe only the slice —
    booking decisions live in the executor, never a silent full close."""
    part, remaining = plan_partial_close(0.15, 0.5, step=0.01)
    assert part == 0.07 and remaining == 0.08


def test_plan_partial_snaps_down_to_step():
    part, remaining = plan_partial_close(0.21, 0.5, step=0.01)
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


def test_partial_half_below_min_refuses_with_no_order(monkeypatch):
    """If the half-slice is below broker volume_min we do NOT book anything —
    no partial, and never a full close either. Return False + the reason."""
    state = mt5_fake.FakeState()
    state.volume_min = 0.10
    state.positions.append(mt5_fake._Pos(7, magic_for("T2"), 4293.0, 4330.0,
                                         volume=0.10))
    ex = _executor(monkeypatch, state)
    assert ex.partial_close_at_tp1("T2", 0.5, "XAU/USD") is False
    assert state.requests == []               # nothing sent at all
    assert "volume_min" in ex.last_error


def test_partial_refusal_never_full_closes_and_keeps_reason(monkeypatch):
    """Broker refuses the half → exactly ONE request (the refused partial),
    no full close, no reopen. Reason is surfaced for the retry/alert loop."""
    state = mt5_fake.FakeState()
    state.fail_partial = True
    state.positions.append(mt5_fake._Pos(7, magic_for("T3"), 4293.0, 4330.0,
                                         volume=0.10))
    ex = _executor(monkeypatch, state)
    assert ex.partial_close_at_tp1("T3", 0.5, "XAU/USD") is False
    assert len(state.requests) == 1           # only the refused half
    assert state.requests[0]["volume"] == 0.05
    assert ex.last_error                       # reason surfaced


def test_last_exit_reads_the_broker_deal(monkeypatch):
    state = mt5_fake.FakeState()
    state.deals.append(mt5_fake._Deal(magic_for("T9"), 4330.0, 300.0, 123))
    ex = _executor(monkeypatch, state)
    out = ex.last_exit("T9")
    assert out["price"] == 4330.0 and out["profit"] == 300.0
    assert out["time"] == 123 and out["volume"] == 0.0 and out["position"] == 0
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
        self.last_error = "" if partial_ok else "retcode=10018 partial denied"
        self.partial_calls = 0
        self.last_order = {"kind": "BUY_LIMIT", "volume": 0.1,
                           "price": 4300.0, "ticket": 555}
        self.last_partial = {"volume": 0.05, "price": 4310.0,
                             "remaining": 0.05, "ticket": 1}
        self.lot = 0.1

    def _sym(self, symbol):
        return symbol

    def _position_by_magic(self, magic):
        if not self.position:
            return None
        return types.SimpleNamespace(ticket=1, volume=0.10, type=0,
                                     sl=4293.0, tp=4330.0, price_open=4300.0)

    def partial_close_at_tp1(self, tid, frac, sym):
        self.partial_calls += 1
        if not self.partial_ok:
            self.last_error = "retcode=10018 partial denied"
        return self.partial_ok

    def apply_stop(self, *a, **k):
        return True

    def last_exit(self, tid):
        return self.exit_info

    def ensure_ticket(self, *a, **k):
        return None


def _row(**over):
    from datetime import datetime, timezone
    base = {"id": "T1", "type": "BUY", "status": "OPEN", "symbol": "XAU/USD",
            "entry_price": 4300.0, "stop_loss": 4293.0,
            "initial_stop_loss": 4293.0, "tp1": 4310.0, "tp2": 4330.0,
            "created_at": datetime.now(timezone.utc).isoformat()}
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
    tick = types.SimpleNamespace(bid=4310.5, ask=4310.7)
    tm._handle_row(_row(), tick, ex, None)
    assert db.updates == []  # nothing claimed; retry next tick


def test_tp1_success_is_booked(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    ex = _StubExecutor(partial_ok=True)
    tick = types.SimpleNamespace(bid=4310.5, ask=4310.7)
    tm._handle_row(_row(), tick, ex, None)
    assert any(u[1].get("partial_close") is True for u in db.updates)
    assert any(u[1].get("status") == "TP1_HIT" for u in db.updates)


def test_tp1_refusal_alerts_once_and_retries_every_tick(monkeypatch):
    """Directive: on refusal do NOT close the whole position — retry the half
    every tick and tell the operator WHY, once per trade (no spam)."""
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    msgs = []
    tm._notify = msgs.append
    ex = _StubExecutor(partial_ok=False)
    tick = types.SimpleNamespace(bid=4310.5, ask=4310.7)
    row = _row()
    tm._handle_row(row, tick, ex, None)   # tick 1: refused -> alert
    tm._handle_row(row, tick, ex, None)   # tick 2: refused -> silent retry
    tm._handle_row(row, tick, ex, None)   # tick 3: refused -> silent retry
    refusals = [m for m in msgs if "REJECTED" in m]
    assert len(refusals) == 1
    assert "retcode=10018" in refusals[0]       # the WHY is reported
    assert ex.partial_calls == 3                # retried every tick
    assert db.updates == []                     # never booked, never closed


def test_tp1_refusal_then_success_books_and_clears(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    msgs = []
    tm._notify = msgs.append
    ex = _StubExecutor(partial_ok=False)
    tick = types.SimpleNamespace(bid=4310.5, ask=4310.7)
    row = _row()
    tm._handle_row(row, tick, ex, None)         # refused -> alert
    ex.partial_ok = True
    tm._handle_row(row, tick, ex, None)         # retry succeeds -> booked
    assert any(u[1].get("partial_close") is True for u in db.updates)
    assert len([m for m in msgs if "REJECTED" in m]) == 1


# ── order placement: signal → MetaTrader (the VPS-only path) ──────────────

def test_ensure_ticket_idempotent_for_outstanding_pending(monkeypatch):
    """A pending order that has NOT filled must not be sent twice."""
    state = mt5_fake.FakeState()
    state.pending_mode = True
    ex = _executor(monkeypatch, state)
    t1 = ex.ensure_ticket("T1", "BUY", "BUY_LIMIT", 4290.0, 4280.0, 4340.0,
                          "XAU/USD")
    t2 = ex.ensure_ticket("T1", "BUY", "BUY_LIMIT", 4290.0, 4280.0, 4340.0,
                          "XAU/USD")
    assert t1 and t1 == t2
    assert len(state.orders) == 1            # ONE pending, not two


def test_tick_manager_places_pending_once_and_books_ticket(monkeypatch):
    state = mt5_fake.FakeState()
    state.pending_mode = True
    monkeypatch.setitem(sys.modules, "MetaTrader5", mt5_fake.install(state))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    ex = Mt5DemoExecutor({"execution": {"demo": {}}})
    row = _row(status="PENDING", order_type="BUY_LIMIT", entry_price=4290.0,
               tp1=4310.0, tp2=4340.0)
    tick = types.SimpleNamespace(bid=4300.0, ask=4300.2)
    tm._handle_row(row, tick, ex, None)
    tm._handle_row(row, tick, ex, None)      # retry tick — must NOT duplicate
    assert len(state.orders) == 1
    assert any(u[1].get("mt5_ticket") for u in db.updates)


def test_tick_manager_opens_market_for_new_signal(monkeypatch):
    """OPEN row without ticket and without position → market order NOW,
    ticket + actual fill price booked to the DB."""
    state = mt5_fake.FakeState()
    monkeypatch.setitem(sys.modules, "MetaTrader5", mt5_fake.install(state))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    ex = Mt5DemoExecutor({"execution": {"demo": {}}})
    row = _row(status="OPEN")                # no mt5_ticket, no position
    tick = types.SimpleNamespace(bid=4300.0, ask=4300.2)
    tm._handle_row(row, tick, ex, None)
    assert len(state.positions) == 1
    assert state.positions[0].volume == 0.10          # the configured lot
    ticket_updates = [u for u in db.updates if u[1].get("mt5_ticket")]
    assert ticket_updates
    entry_updates = [u for u in db.updates if u[1].get("entry_price")]
    assert entry_updates and entry_updates[-1][1]["entry_price"] == 4300.2


def test_pending_fill_books_actual_fill_price(monkeypatch):
    state = mt5_fake.FakeState()
    magic = magic_for("T1")
    state.positions.append(mt5_fake._Pos(55, magic, 4280.0, 4340.0,
                                         volume=0.10))
    state.positions[-1].price_open = 4291.11          # slippage vs plan
    monkeypatch.setitem(sys.modules, "MetaTrader5", mt5_fake.install(state))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    ex = Mt5DemoExecutor({"execution": {"demo": {}}})
    row = _row(status="PENDING", order_type="BUY_LIMIT", entry_price=4290.0)
    tick = types.SimpleNamespace(bid=4300.0, ask=4300.2)
    tm._handle_row(row, tick, ex, None)
    open_upd = [u for u in db.updates if u[1].get("status") == "OPEN"]
    assert open_upd
    assert open_upd[-1][1]["entry_price"] == 4291.11  # actual fill, not plan


def test_broker_exit_uses_deal_price_and_labels_tp2(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    ex = _StubExecutor(position=False,
                       exit_info={"price": 4330.0, "profit": 300.0, "time": 1})
    tick = types.SimpleNamespace(bid=4329.0, ask=4329.2)
    tm._handle_row(_row(mt5_ticket=99), tick, ex, None)  # ticketed → exit path
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


# ── market_data → mt5_feed keyword contract ────────────────────────────────

def test_market_data_passes_valid_kwargs_to_mt5_feed(monkeypatch):
    """Regression (live incident 2026-08-10): market_data called
    get_candles(outputsize=...) while the feed's parameter is `count` —
    every VPS analysis cycle died with TypeError and the production guard
    stopped it. The REAL feed function must accept exactly what
    market_data passes; any future rename makes this test explode."""
    state = mt5_fake.FakeState()
    state.rates = [
        {"time": 1_700_000_000 + i * 300, "open": 4300.0, "high": 4301.0,
         "low": 4299.0, "close": 4300.5}
        for i in range(220)
    ]
    monkeypatch.setitem(sys.modules, "MetaTrader5", mt5_fake.install(state))
    monkeypatch.setenv("DATA_SOURCE_PRIMARY", "mt5")
    from services.market_data import MarketDataService
    svc = MarketDataService({
        "symbol": "XAU/USD",
        "data_source": {"api_keys": {}},
        "execution": {"demo": {"symbol_map": {"XAU/USD": "XAUUSD.s"}}},
    })
    payload = svc.get_ohlcv("5m", outputsize=100)
    assert payload is not None
    assert payload["source"] == "mt5"
    assert len(payload["data"]) == 220


def test_mt5_fresh_candles_support_signal_generation(monkeypatch):
    """Live incident 2026-08-10: enrich overwrote the mt5 feed's integrity
    with an UNKNOWN default and the analysis refused its own broker data.
    Fresh broker candles must pass the signal gate."""
    import time as _t
    from services import mt5_feed as _feed
    _feed._offset_cache.clear()
    state = mt5_fake.FakeState()
    state.server_time = int(_t.time())  # offset ~0 so candle times stay real
    base = int(_t.time())
    state.rates = [
        {"time": base - (219 - i) * 300, "open": 4300.0, "high": 4301.0,
         "low": 4299.0, "close": 4300.5}
        for i in range(220)
    ]
    monkeypatch.setitem(sys.modules, "MetaTrader5", mt5_fake.install(state))
    monkeypatch.setenv("DATA_SOURCE_PRIMARY", "mt5")
    from services.market_data import MarketDataService
    svc = MarketDataService({"symbol": "XAU/USD", "data_source": {"api_keys": {}}})
    payload = svc.get_ohlcv("5m", outputsize=220)
    integ = payload["source_integrity"]
    assert integ["source"] == "mt5"
    assert integ["reliability_grade"] == "HIGH"
    assert integ["supports_signal_generation"] is True
    assert payload["supports_signal_generation"] is True


def test_mt5_stale_candles_still_refused(monkeypatch):
    """Staleness protection must survive the fix: old candles stay MEDIUM
    and cannot generate signals."""
    import time as _t
    from services import mt5_feed as _feed
    _feed._offset_cache.clear()
    state = mt5_fake.FakeState()
    state.server_time = int(_t.time())  # offset ~0 so old candles stay old
    state.rates = [
        {"time": 1_700_000_000 + i * 300, "open": 4300.0, "high": 4301.0,
         "low": 4299.0, "close": 4300.5}
        for i in range(220)
    ]
    monkeypatch.setitem(sys.modules, "MetaTrader5", mt5_fake.install(state))
    monkeypatch.setenv("DATA_SOURCE_PRIMARY", "mt5")
    from services.market_data import MarketDataService
    svc = MarketDataService({"symbol": "XAU/USD", "data_source": {"api_keys": {}}})
    payload = svc.get_ohlcv("5m", outputsize=220)
    assert payload["source_integrity"]["reliability_grade"] == "MEDIUM"
    assert payload["supports_signal_generation"] is False
    _feed._offset_cache.clear()


def test_mt5_fetches_timeframes_natively_not_resampled(monkeypatch):
    """Live divergence incident 2026-08-10: with MT5 primary the analysis
    must fetch each timeframe natively (deep 4H like paper), NOT resample
    from a shallow 5m base."""
    import time as _t
    from services import mt5_feed as _feed
    _feed._offset_cache.clear()
    state = mt5_fake.FakeState()
    state.server_time = int(_t.time())
    base = int(_t.time())
    state.rates = [
        {"time": base - (219 - i) * 300, "open": 4300.0, "high": 4301.0,
         "low": 4299.0, "close": 4300.5}
        for i in range(220)
    ]
    seen = []
    real = _feed.get_candles
    def spy(*a, **k):
        seen.append(k.get("timeframe") or (a[1] if len(a) > 1 else None))
        return real(*a, **k)
    monkeypatch.setattr(_feed, "get_candles", spy)
    fake = mt5_fake.install(state)
    fake.get_candles = spy
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    monkeypatch.setenv("DATA_SOURCE_PRIMARY", "mt5")
    from services.market_data import MarketDataService
    cfg = {"symbol": "XAU/USD", "data_source": {"api_keys": {},
           "primary": "mt5", "resample_timeframes_from_base": True,
           "base_timeframe": "5m"},
           "timeframes": ["5m", "15m", "1H", "4H"],
           "primary_timeframe": "15m"}
    svc = MarketDataService(cfg)
    data = svc.get_gold_data()
    assert data is not None
    assert set(seen) == {"5m", "15m", "1H", "4H"}  # native, no base-only resample
    _feed._offset_cache.clear()


def test_trailing_move_sends_card(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    msgs = []
    tm._notify = msgs.append
    ex = _StubExecutor()
    row = _row(status="OPEN", stop_loss=4330.0, tp1=4390.0, tp2=4420.0,
               initial_stop_loss=4290.0)
    tick = types.SimpleNamespace(bid=4350.0, ask=4350.2)
    tm._handle_row(row, tick, ex, None)
    assert any("trailing stop moved" in m for m in msgs)


def test_breakeven_sends_card(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    msgs = []
    tm._notify = msgs.append
    ex = _StubExecutor()
    row = _row(status="OPEN", stop_loss=4290.0, tp1=4390.0, tp2=4420.0,
               initial_stop_loss=4290.0)
    tick = types.SimpleNamespace(bid=4320.5, ask=4320.7)  # +200 pts fav
    tm._handle_row(row, tick, ex, None)
    assert any("breakeven armed" in m for m in msgs)


def test_stale_pending_cancelled_at_broker():
    from scripts.run_tick_manager import magic_for

    class _Ord:
        ticket = 777
        magic = 424242  # not in active rows

    class _RecExec(_StubExecutor):
        def __init__(self):
            super().__init__()
            self.cancelled = []

        def open_orders(self):
            return [_Ord()]

        def cancel_order(self, ticket):
            self.cancelled.append(ticket)
            return True

    tm = _tm()
    msgs = []
    tm._notify = msgs.append
    ex = _RecExec()
    row = _row(status="PENDING")  # its magic != 424242
    tm._reconcile_pending([row], ex)
    assert ex.cancelled == [777]
    assert any("cancelled" in m for m in msgs)


def test_old_pending_still_gets_placed_like_paper(monkeypatch):
    """Paper keeps pendings alive for hours; the 60-min resurrection guard
    must NOT starve them. An aged PENDING row with no ticket is placed."""
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db

    class _Placer(_StubExecutor):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.placed = []

        def ensure_ticket(self, tid, side, kind, entry, sl, tp, sym):
            self.placed.append((tid, kind))
            return 555

    ex = _Placer(position=False)  # no broker position yet
    row = _row(status="PENDING", order_type="BUY_LIMIT")
    from datetime import timedelta
    row["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)
                         ).isoformat()  # aged but under stale_after_hours
    tick = types.SimpleNamespace(bid=4300.5, ask=4300.7)  # near entry
    tm._handle_row(row, tick, ex, None)
    assert ex.placed and ex.placed[0][1] == "BUY_LIMIT"


def test_closed_row_is_never_resurrected_even_if_open_status(monkeypatch):
    """Resurrection protection: a row that already has a close_price is
    finished forever — never re-placed."""
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db

    class _Placer(_StubExecutor):
        def __init__(self):
            super().__init__()
            self.placed = []

        def ensure_ticket(self, *a, **k):
            self.placed.append(1)
            return None

    ex = _Placer()
    row = _row(status="OPEN")
    row["created_at"] = "2026-08-10T04:00:00+00:00"
    row["close_price"] = 4341.0  # finished -> never resurrect
    tick = types.SimpleNamespace(bid=4340.0, ask=4340.2)
    tm._handle_row(row, tick, ex, None)
    assert ex.placed == []


def test_aged_never_placed_open_row_still_gets_placed(monkeypatch):
    """The card went to Telegram; the executor must place it even hours
    later if it was never placed (no ticket, no close)."""
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db

    class _Placer2(_StubExecutor):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.placed = []

        def ensure_ticket(self, tid, side, kind, entry, sl, tp, sym):
            self.placed.append(kind)
            return 77

    ex = _Placer2(position=False)
    row = _row(status="OPEN")
    row["created_at"] = "2026-08-10T01:00:00+00:00"  # aged, never placed
    tick = types.SimpleNamespace(bid=4340.0, ask=4340.2)
    tm._handle_row(row, tick, ex, None)
    assert ex.placed == ["MARKET"]


def test_be_card_only_after_broker_confirms(monkeypatch):
    """Operator directive: move the stop FIRST, message ONLY on success."""
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    msgs = []
    tm._notify = msgs.append

    class _Refusing(_StubExecutor):
        def __init__(self, ok):
            super().__init__()
            self.ok = ok

        def apply_stop(self, *a, **k):
            return self.ok

    row = _row(status="OPEN", stop_loss=4290.0, tp1=4390.0, tp2=4420.0,
               initial_stop_loss=4290.0)
    tick = types.SimpleNamespace(bid=4320.5, ask=4320.7)  # +200 pts fav
    tm._handle_row(dict(row), tick, _Refusing(False), None)
    assert msgs == [] and db.updates == []      # refusal: silent, retried
    tm._handle_row(dict(row), tick, _Refusing(True), None)
    assert any("breakeven armed" in m and "confirmed" in m for m in msgs)
    assert any(u[1].get("sl_moved_to_entry") for u in db.updates)


def test_trailing_card_only_after_broker_confirms(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    msgs = []
    tm._notify = msgs.append

    class _Refusing(_StubExecutor):
        def __init__(self, ok):
            super().__init__()
            self.ok = ok

        def apply_stop(self, *a, **k):
            return self.ok

    row = _row(status="OPEN", stop_loss=4330.0, tp1=4390.0, tp2=4420.0,
               initial_stop_loss=4290.0)
    tick = types.SimpleNamespace(bid=4350.0, ask=4350.2)
    tm._handle_row(dict(row), tick, _Refusing(False), None)
    assert msgs == [] and db.updates == []
    tm._handle_row(dict(row), tick, _Refusing(True), None)
    assert any("trailing stop moved" in m and "confirmed" in m for m in msgs)


def test_trailing_respects_peak_hit_while_down(monkeypatch):
    """Restart-lossless: a high printed while the process was down must still
    ratchet the stop (seeded from broker M1 history)."""
    state = mt5_fake.FakeState()
    state.rates_range = [
        {"high": 4394.0, "low": 4380.0},   # peak hit during downtime
        {"high": 4390.0, "low": 4385.0},
    ]
    monkeypatch.setitem(sys.modules, "MetaTrader5", mt5_fake.install(state))
    db = _StubDB()
    tm = _tm()
    tm.database = db

    class _TrailExec(_StubExecutor):
        def __init__(self):
            super().__init__()
            self.applied = []

        def apply_stop(self, tid, sl, tp, sym):
            self.applied.append(sl)
            return True

    ex = _TrailExec()

    class _PosSeed:
        ticket = 7
        sl = 4374.48
        tp = 4400.0
        volume = 0.05
        type = 0
        time = 1754830000
        price_open = 4330.26

    row = _row(status="TP1_HIT", entry_price=4330.26, stop_loss=4374.48,
               tp1=4390.0, tp2=4400.0, partial_close=True,
               sl_moved_to_entry=True)
    tick = types.SimpleNamespace(bid=4388.0, ask=4388.2)
    tm._handle_row(row, tick, _WrapSeed(ex, _PosSeed()), None)
    # seeded extreme 4394 -> candidate 4379 >= 4374.48+4 -> ratchet fires
    assert 4379.0 in ex.applied


class _WrapSeed:
    def __init__(self, inner, pos):
        self._i = inner
        self._p = pos

    def __getattr__(self, name):
        return getattr(self._i, name)

    def _position_by_magic(self, magic):
        return self._p


def test_filling_mode_fallback_on_10030(monkeypatch):
    """Broker lies about filling flags (live 10030 flood on pendings):
    the executor must walk the filling modes until accepted."""
    import types as _t
    modes_seen = []
    calls = {"n": 0}

    mod = mt5_fake.install(mt5_fake.FakeState())

    def _send(request):
        calls["n"] += 1
        modes_seen.append(request.get("type_filling"))
        if calls["n"] == 1:
            return _t.SimpleNamespace(retcode=10030, comment="Unsupported filling mode")
        return _t.SimpleNamespace(retcode=mt5_fake.TRADE_RETCODE_DONE, order=91)

    mod.order_send = _send
    monkeypatch.setitem(sys.modules, "MetaTrader5", mod)
    from services import mt5_executor as ex_mod
    ex_mod._MT5_READY["ok"] = False
    ex = ex_mod.Mt5DemoExecutor({"execution": {"demo": {}}})
    t = ex.ensure_ticket("T-FB", "BUY", "BUY_LIMIT", 4300.0, 4290.0,
                         4400.0, "XAU/USD")
    assert t == 91
    assert len(modes_seen) >= 2          # first refused, second accepted
    ex_mod._MT5_READY["ok"] = False


def test_manual_sl_drift_backward_is_corrected(monkeypatch):
    """Operator moved the broker SL backwards; the tick guard must restore
    the book level at the broker and card it."""
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    msgs = []
    tm._notify = msgs.append

    class _DriftExec(_StubExecutor):
        def __init__(self):
            super().__init__()
            self.applied = []

        def apply_stop(self, tid, sl, tp, sym):
            self.applied.append(sl)
            return True

    ex = _DriftExec()

    class _PosDrift:
        ticket = 7
        sl = 4320.0           # manually dragged back
        tp = 4400.0
        volume = 0.05
        type = 0
        price_open = 4330.26

    row = _row(status="TP1_HIT", entry_price=4330.26, stop_loss=4338.30,
               tp1=4390.0, tp2=4400.0, partial_close=True)
    tick = types.SimpleNamespace(bid=4359.0, ask=4359.2)
    tm._handle_row(row, tick, _WrapDrift(ex, _PosDrift()), None)
    assert ex.applied and ex.applied[0] == 4338.30
    assert any("drift corrected" in m for m in msgs)


class _WrapDrift:
    def __init__(self, inner, pos):
        self._i = inner
        self._p = pos

    def __getattr__(self, name):
        return getattr(self._i, name)

    def _position_by_magic(self, magic):
        return self._p


def test_stale_pending_cancelled_by_freshness_mirror(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    ex = _StubExecutor(position=False)
    old_row = _row(status="PENDING", order_type="BUY_LIMIT",
                   entry_price=4300.0)
    old_row["created_at"] = "2026-08-10T00:00:00+00:00"  # >6h old
    tick = types.SimpleNamespace(bid=4300.5, ask=4300.7)
    tm._handle_row(old_row, tick, ex, None)
    assert any(u[1].get("status") == "CANCELLED" for u in db.updates)


def test_runaway_excursion_cancels_pending(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db
    ex = _StubExecutor(position=False)
    row = _row(status="PENDING", order_type="BUY_LIMIT", entry_price=4300.0)
    tick = types.SimpleNamespace(bid=4330.0, ask=4330.2)  # +$30 away
    tm._handle_row(row, tick, ex, None)
    assert any(u[1].get("status") == "CANCELLED" for u in db.updates)


def test_fresh_near_pending_survives(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db

    class _Placer(_StubExecutor):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.placed = []

        def ensure_ticket(self, tid, side, kind, entry, sl, tp, sym):
            self.placed.append(kind)
            return 9

    ex = _Placer(position=False)
    row = _row(status="PENDING", order_type="BUY_LIMIT", entry_price=4300.0)
    tick = types.SimpleNamespace(bid=4302.0, ask=4302.2)  # near, fresh
    tm._handle_row(row, tick, ex, None)
    assert ex.placed == ["BUY_LIMIT"]
    assert not any(u[1].get("status") == "CANCELLED" for u in db.updates)


def test_finished_row_is_never_resurrected(monkeypatch):
    """Live incident 2026-08-10: a finished TP1_HIT row with no ticket was
    re-opened as fresh market orders every tick. Finished/stale rows must be
    booked closed, never resurrected."""
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        mt5_fake.install(mt5_fake.FakeState()))
    db = _StubDB()
    tm = _tm()
    tm.database = db

    class _NoResurrect(_StubExecutor):
        def __init__(self):
            super().__init__(position=False, exit_info=None)
            self.ensure_calls = 0

        def ensure_ticket(self, *a, **k):
            self.ensure_calls += 1
            return None

    ex = _NoResurrect()
    row = _row(status="TP1_HIT")
    row["mt5_ticket"] = None
    row["created_at"] = "2026-08-10T08:11:57+00:00"  # stale
    tick = types.SimpleNamespace(bid=4341.0, ask=4341.2)
    tm._handle_row(row, tick, ex, None)
    assert ex.ensure_calls == 0                     # never resurrect
    assert any(u[1].get("close_price") for u in db.updates)  # booked closed
    row["close_price"] = 4341.0                     # as the DB would hold it
    n_upd, n_calls = len(db.updates), ex.ensure_calls
    tm._handle_row(row, tick, ex, None)             # next tick: silent
    assert len(db.updates) == n_upd and ex.ensure_calls == n_calls


def test_filling_mode_chosen_from_broker_flags(monkeypatch):
    """Live rejection 2026-08-10: retcode 10030 'Unsupported filling mode'.
    The executor must obey the symbol's filling_mode flags, never hard-code."""
    from services import mt5_executor as ex_mod

    def _place(mode_flag):
        ex_mod._MT5_READY["ok"] = False
        state = mt5_fake.FakeState()
        state.filling_mode = mode_flag
        monkeypatch.setitem(sys.modules, "MetaTrader5", mt5_fake.install(state))
        ex = ex_mod.Mt5DemoExecutor({"execution": {"demo": {}}})
        t = ex.ensure_ticket("T-FILL", "BUY", "BUY_MARKET", 4350.0, 4320.0,
                             4420.0, "XAU/USD")
        assert t is not None
        return state.requests[-1]["type_filling"]

    assert _place(0) == mt5_fake.ORDER_FILLING_RETURN   # no IOC/FOK -> RETURN
    assert _place(mt5_fake.SYMBOL_FILLING_IOC) == mt5_fake.ORDER_FILLING_IOC
    ex_mod._MT5_READY["ok"] = False


def test_ensure_ticket_auto_initializes_terminal(monkeypatch):
    """Live incident 2026-08-10: the executor queried the terminal without
    ever calling initialize() -> 'no tick for XAUUSD.s' -> no order was ever
    sent. ensure_ticket must self-initialize."""
    from services import mt5_executor as ex_mod
    ex_mod._MT5_READY["ok"] = False
    state = mt5_fake.FakeState()
    monkeypatch.setitem(sys.modules, "MetaTrader5", mt5_fake.install(state))
    ex = ex_mod.Mt5DemoExecutor({"execution": {"demo": {}}})
    t = ex.ensure_ticket("T-AUTO", "BUY", "BUY_MARKET", 4350.0, 4320.0,
                         4420.0, "XAU/USD")
    assert t is not None
    assert ex_mod._MT5_READY["ok"] is True
    ex_mod._MT5_READY["ok"] = False


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
