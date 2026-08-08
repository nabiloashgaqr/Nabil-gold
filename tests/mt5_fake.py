"""Fake MetaTrader5 module for tests (demo/mt5 branch, phase 1)."""
from __future__ import annotations

import types
from typing import Any, Dict, List

TIMEFRAME_M5 = 5
TIMEFRAME_M15 = 15
TIMEFRAME_H1 = 60
TIMEFRAME_H4 = 240
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
ORDER_FILLING_IOC = 2
TRADE_RETCODE_DONE = 10009


class _Pos:
    def __init__(self, ticket, magic, sl, tp, volume=0.10, ptype=ORDER_TYPE_BUY):
        self.ticket = ticket
        self.magic = magic
        self.sl = sl
        self.tp = tp
        self.volume = volume
        self.type = ptype


class FakeState:
    def __init__(self) -> None:
        self.server_time = 1_700_000_000
        self.epoch = 1_700_000_000
        self.positions: List[_Pos] = []
        self.requests: List[Dict[str, Any]] = []
        self.fail_partial = False
        self.rates: List[Dict[str, Any]] = []


def install(state: FakeState) -> types.ModuleType:
    mod = types.ModuleType("MetaTrader5")
    for name in ("TIMEFRAME_M5", "TIMEFRAME_M15", "TIMEFRAME_H1",
                 "TIMEFRAME_H4", "ORDER_TYPE_BUY", "ORDER_TYPE_SELL",
                 "ORDER_TYPE_BUY_LIMIT", "ORDER_TYPE_SELL_LIMIT",
                 "TRADE_ACTION_DEAL", "TRADE_ACTION_PENDING",
                 "TRADE_ACTION_SLTP", "ORDER_FILLING_IOC",
                 "TRADE_RETCODE_DONE"):
        setattr(mod, name, globals()[name])
    mod.initialize = lambda path=None: True
    mod.login = lambda login, password=None, server=None: True
    mod.terminal_info = lambda: object()
    mod.time = lambda: state.server_time
    mod.copy_rates_from_pos = lambda sym, tf, start, count: state.rates
    mod.symbol_info_tick = lambda sym: types.SimpleNamespace(bid=4300.0, ask=4300.2)
    mod.positions_get = lambda: list(state.positions)

    def order_send(request):
        state.requests.append(request)
        if state.fail_partial and request.get("comment") == "SS-demo-tp1":
            return types.SimpleNamespace(retcode=10018, comment="partial denied")
        if request.get("action") == TRADE_ACTION_DEAL and request.get("position"):
            return types.SimpleNamespace(retcode=TRADE_RETCODE_DONE, order=999)
        if request.get("action") in (TRADE_ACTION_DEAL, TRADE_ACTION_PENDING):
            magic = request.get("magic")
            ticket = 1000 + len(state.positions) + 1
            state.positions.append(_Pos(
                ticket, magic, request.get("sl", 0), request.get("tp", 0),
                request.get("volume", 0.10), request.get("type", ORDER_TYPE_BUY)))
            return types.SimpleNamespace(retcode=TRADE_RETCODE_DONE, order=ticket)
        return types.SimpleNamespace(retcode=TRADE_RETCODE_DONE, order=777)

    mod.order_send = order_send
    return mod
