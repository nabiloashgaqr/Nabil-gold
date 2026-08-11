"""MT5 demo executor (demo branch, phase 1).

Transmits the unified-law levels (utils/trading_rules) to a MetaTrader 5
DEMO account. Never decides risk itself. Idempotent per trade via magic
number. Hard halt on reconciliation mismatch. All failures are logged and
reported; the executor must never crash the cycle.
"""

from __future__ import annotations

import json
import logging
import os
import time
import zlib
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HALT_FILE = ".demo_halt"


def _mt5():
    import MetaTrader5 as mt5  # noqa: lazy on purpose
    return mt5


_MT5_READY = {"ok": False}


def _mt5_ready():
    """Lazy import + initialize ONCE (live incident 2026-08-10: the executor
    and tick manager queried the terminal without ever initializing it —
    every symbol_info_tick silently returned None and no order was ever
    sent)."""
    mt5 = _mt5()
    if not _MT5_READY["ok"]:
        try:
            path = os.environ.get("MT5_PATH") or None
            if mt5.initialize(path=path) if path else mt5.initialize():
                _MT5_READY["ok"] = True
            else:
                logger.warning("MT5 initialize failed: %s", mt5.last_error())
        except Exception as exc:  # noqa: BLE001
            logger.warning("MT5 initialize crashed: %s", exc)
    return mt5


def magic_for(trade_id: str) -> int:
    return 1000000 + (zlib.crc32(trade_id.encode()) % 8999999)


def plan_partial_close(volume: float, fraction: float, step: float):
    """Snap the TP1 booked slice to the broker's volume_step.

    Returns (part, remaining). This plan ONLY ever describes the fractional
    slice — it never escalates to closing the whole position (operator
    directive: close the half, nothing else). Pure + unit-tested.
    """
    if step <= 0:
        step = 0.01
    import math
    part = math.floor(volume * fraction / step + 1e-9) * step
    part = round(part, 8)
    return part, round(volume - part, 8)


class Mt5DemoExecutor:
    def __init__(self, config: Dict[str, Any], telegram=None):
        self.config = config or {}
        demo = (self.config.get("execution") or {}).get("demo") or {}
        self.lot = float(demo.get("lot_size", 0.10))
        self.deviation = int(demo.get("deviation_points_mt5", 30))
        self.max_per_day = int(demo.get("max_new_orders_per_day", 6))
        self.halt_on_mismatch = bool(demo.get("reconcile_halt_on_mismatch", True))
        self.symbol_map = demo.get("symbol_map") or {"XAU/USD": "XAUUSD"}
        self.telegram = telegram
        self._orders_today = 0
        self.last_error = ""
        self.last_order: Dict[str, Any] = {}
        self.last_partial: Dict[str, Any] = {}
        self.last_close: Dict[str, Any] = {}

    # -- lifecycle ---------------------------------------------------------
    def alive(self) -> bool:
        try:
            return _mt5().terminal_info() is not None
        except Exception:  # noqa: BLE001
            return False

    def _sym(self, symbol: str) -> str:
        return self.symbol_map.get(symbol, symbol.replace("/", ""))

    # -- halt --------------------------------------------------------------
    def halted(self) -> bool:
        return os.path.exists(HALT_FILE)

    def _halt(self, why: str) -> None:
        if self.halt_on_mismatch:
            try:
                with open(HALT_FILE, "w", encoding="utf-8") as fh:
                    fh.write(why)
            except OSError:
                pass
        logger.error("DEMO HALT: %s", why)
        if self.telegram:
            try:
                self.telegram.send_error_alert(f"🧪 DEMO HALT: {why}")
            except Exception:  # noqa: BLE001
                pass

    # -- orders ------------------------------------------------------------
    def _position_by_magic(self, magic: int):
        try:
            for pos in _mt5_ready().positions_get() or []:
                if pos.magic == magic:
                    return pos
        except Exception:  # noqa: BLE001
            return None
        return None

    def _send_deal(self, mt5, request: Dict[str, Any]):
        """Send DEAL/PENDING; when the broker answers 10030 'Unsupported
        filling mode' (its flags can lie), retry across all filling modes.
        Live incident 2026-08-10: pendings flooded 10030 refusals."""
        pref = request.get(
            "type_filling", self._filling_type(mt5, request["symbol"]))
        tried = []
        last = None
        for mode in (pref, getattr(mt5, "ORDER_FILLING_RETURN", 2),
                     getattr(mt5, "ORDER_FILLING_IOC", 1),
                     getattr(mt5, "ORDER_FILLING_FOK", 0)):
            if mode in tried:
                continue
            tried.append(mode)
            request["type_filling"] = mode
            try:
                res = mt5.order_send(request)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"order_send crashed: {exc}"
                logger.error("order_send crashed: %s", exc)
                return None
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                return res
            last = res
            if getattr(res, "retcode", None) != 10030:
                break
        if last is not None:
            self.last_error = (
                f"retcode={getattr(last, 'retcode', '?')} "
                f"{getattr(last, 'comment', '')}".strip())
            logger.error("order_send refused: %s", self.last_error)
        return None

    def _filling_type(self, mt5, sym: str):
        """Pick a filling mode the broker actually supports (live rejection
        2026-08-10: retcode 10030 'Unsupported filling mode' on XAUUSD.s).
        Preference IOC -> FOK -> RETURN, from the symbol's filling_mode flags."""
        try:
            info = mt5.symbol_info(sym)
            fm = int(getattr(info, "filling_mode", 0) or 0)
            if fm & getattr(mt5, "SYMBOL_FILLING_IOC", 2):
                return mt5.ORDER_FILLING_IOC
            if fm & getattr(mt5, "SYMBOL_FILLING_FOK", 1):
                return mt5.ORDER_FILLING_FOK
        except Exception:  # noqa: BLE001
            pass
        return getattr(mt5, "ORDER_FILLING_RETURN", 0)

    def _order_by_magic(self, magic: int):
        """Outstanding PENDING order with this magic (not yet a position)."""
        try:
            for o in _mt5_ready().orders_get() or []:
                if getattr(o, "magic", None) == magic:
                    return o
        except Exception:  # noqa: BLE001
            return None
        return None

    def ensure_ticket(
        self,
        trade_id: str,
        side: str,
        order_kind: str,
        entry_price: float,
        sl: float,
        tp: float,
        symbol: str,
    ) -> Optional[int]:
        """Idempotent open. Returns ticket or None (refused/failed).

        Idempotency covers BOTH states of an order's life: an open POSITION
        and an outstanding PENDING order. Without the pending check, a
        tick-level retry loop would stack duplicate limit orders.
        """
        self.last_error = ""
        if self.halted():
            self.last_error = "executor halted (.demo_halt)"
            return None
        if self._orders_today >= self.max_per_day:
            self.last_error = "daily order cap reached"
            logger.warning("Demo order refused: daily cap reached")
            return None
        magic = magic_for(trade_id)
        existing = self._position_by_magic(magic)
        if existing:
            return int(existing.ticket)
        existing_order = self._order_by_magic(magic)
        if existing_order:
            return int(existing_order.ticket)
        mt5 = _mt5_ready()
        sym = self._sym(symbol)
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            self.last_error = f"no tick for {sym}"
            return None
        buy = str(side).upper() == "BUY"
        if str(order_kind or "").upper().endswith("MARKET"):
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": sym,
                "volume": self.lot,
                "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
                "price": tick.ask if buy else tick.bid,
                "sl": float(sl),
                "tp": float(tp),
                "deviation": self.deviation,
                "magic": magic,
                "comment": "SS-demo",
                "type_filling": self._filling_type(mt5, sym),
            }
        else:
            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": sym,
                "volume": self.lot,
                "type": mt5.ORDER_TYPE_BUY_LIMIT if buy else mt5.ORDER_TYPE_SELL_LIMIT,
                "price": float(entry_price),
                "sl": float(sl),
                "tp": float(tp),
                "magic": magic,
                "comment": "SS-demo",
            }
        res = self._send_deal(mt5, request)
        if res is None:
            return None
        self._orders_today += 1
        self.last_order = {
            "ticket": int(res.order),
            "kind": str(order_kind or ""),
            "volume": self.lot,
            "price": round(float(request.get("price") or 0.0), 2),
        }
        return int(res.order)

    def apply_stop(self, trade_id: str, new_sl: float, tp: float, symbol: str) -> bool:
        magic = magic_for(trade_id)
        pos = self._position_by_magic(magic)
        if not pos:
            return False
        mt5 = _mt5_ready()
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": self._sym(symbol),
            "position": int(pos.ticket),
            "sl": float(new_sl),
            "tp": float(tp),
        }
        try:
            res = mt5.order_send(request)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"SLTP crashed: {exc}"
            logger.error("SLTP modify crashed: %s", exc)
            return False
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            return True
        self.last_error = (f"SLTP retcode={getattr(res, 'retcode', '?')} "
                           f"{getattr(res, 'comment', '')}".strip())
        logger.warning("SLTP refused for %s: %s", trade_id, self.last_error)
        return False

    def partial_close_at_tp1(self, trade_id: str, fraction: float, symbol: str) -> bool:
        """Close ONLY the booked fraction at TP1 (operator directive).

        NEVER full-close + reopen. The volume is snapped to the broker's
        volume_step; if the slice is below volume_min it cannot be booked at
        all. On any refusal: return False and leave the reason in
        self.last_error — the tick manager retries every tick and reports
        the refusal to the operator once per trade.
        """
        mt5 = _mt5()
        self.last_error = ""
        magic = magic_for(trade_id)
        pos = self._position_by_magic(magic)
        if not pos:
            self.last_error = "position not found"
            return False
        sym = self._sym(symbol)
        info = mt5.symbol_info(sym)
        step = float(getattr(info, "volume_step", 0.01) or 0.01)
        min_vol = float(getattr(info, "volume_min", 0.01) or 0.01)
        part, _remaining = plan_partial_close(float(pos.volume), fraction, step)
        if part < min_vol or part <= 0:
            self.last_error = (
                f"slice {part:.2f} below broker volume_min {min_vol:g} — "
                f"cannot book the half")
            logger.error("TP1 partial impossible for %s: %s", trade_id,
                         self.last_error)
            return False
        tick = mt5.symbol_info_tick(sym)
        close_type = (mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY
                      else mt5.ORDER_TYPE_BUY)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": part,
            "type": close_type,
            "position": int(pos.ticket),
            "price": tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask,
            "deviation": self.deviation,
            "magic": magic,
            "comment": "SS-demo-tp1",
            "type_filling": self._filling_type(mt5, sym),
        }
        res = self._send_deal(mt5, request)
        if res is not None:
            self.last_partial = {
                "volume": part,
                "price": round(float(request["price"]), 2),
                "remaining": round(float(pos.volume) - part, 2),
                "ticket": int(pos.ticket),
            }
            return True
        logger.warning("TP1 partial refused for %s: %s (will retry)",
                       trade_id, self.last_error)
        return False

    def close_position(self, trade_id: str, symbol: str) -> bool:
        """Full market close of the trade's position (thesis exit handoff)."""
        mt5 = _mt5_ready()
        magic = magic_for(trade_id)
        pos = self._position_by_magic(magic)
        if not pos:
            self.last_error = "close_position: position not found"
            return False
        sym = self._sym(symbol)
        tick = mt5.symbol_info_tick(sym)
        close_type = (mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY
                      else mt5.ORDER_TYPE_BUY)
        price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": float(pos.volume),
            "type": close_type,
            "position": int(pos.ticket),
            "price": price,
            "deviation": self.deviation,
            "magic": magic,
            "comment": "SS-demo-exit",
            "type_filling": self._filling_type(mt5, sym),
        }
        res = self._send_deal(mt5, request)
        if res is not None:
            self.last_close = {"price": round(float(price), 2),
                               "volume": float(pos.volume),
                               "ticket": int(pos.ticket)}
            return True
        logger.warning("close_position refused for %s: %s", trade_id,
                       self.last_error)
        return False

    def open_orders(self):
        """Outstanding broker orders (for pending reconciliation)."""
        try:
            return list(_mt5_ready().orders_get() or [])
        except Exception:  # noqa: BLE001
            return []

    def cancel_order(self, ticket: int) -> bool:
        """Cancel a broker pending order by ticket."""
        mt5 = _mt5_ready()
        try:
            res = mt5.order_send({
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": int(ticket),
            })
            ok = bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)
            if not ok:
                logger.warning("cancel order %s refused: %s", ticket,
                               getattr(res, "comment", res))
            return ok
        except Exception as exc:  # noqa: BLE001
            logger.error("cancel order %s crashed: %s", ticket, exc)
            return False

    def last_exit(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """The broker's own record of how this trade's position exited.

        Returns the newest OUT deal {price, profit, time} for the trade's
        magic, or None if no exit deal exists yet. Used so the DB mirrors
        the REAL broker exit price + realized P&L instead of the live tick
        at the moment we happened to notice the position was gone.
        """
        mt5 = _mt5()
        magic = magic_for(trade_id)
        try:
            now = int(time.time()) + 86400
            deals = mt5.history_deals_get(0, now) or ()
        except Exception as exc:  # noqa: BLE001
            logger.error("history_deals_get crashed: %s", exc)
            return None
        outs = [d for d in deals
                if getattr(d, "magic", None) == magic
                and getattr(d, "entry", None) == getattr(mt5, "DEAL_ENTRY_OUT", 1)]
        if not outs:
            return None
        d = max(outs, key=lambda x: getattr(x, "time", 0))
        return {"price": float(getattr(d, "price", 0.0)),
                "profit": float(getattr(d, "profit", 0.0)),
                "time": int(getattr(d, "time", 0)),
                "volume": float(getattr(d, "volume", 0.0)),
                "position": int(getattr(d, "position", 0))}

    # -- reconciliation ------------------------------------------------------
    def reconcile(self, open_rows: List[Dict[str, Any]]) -> List[str]:
        mismatches: List[str] = []
        for row in open_rows:
            ticket = row.get("mt5_ticket")
            if not ticket:
                continue
            pos = self._position_by_magic(magic_for(str(row.get("id"))))
            if pos is None:
                mismatches.append(f"{row.get('id')}: no MT5 position for ticket {ticket}")
                continue
            db_sl = float(row.get("stop_loss") or 0)
            if db_sl and abs(pos.sl - db_sl) > 0.05:
                mismatches.append(
                    f"{row.get('id')}: SL drift mt5={pos.sl:.2f} db={db_sl:.2f}")
            side_db = str(row.get("type") or row.get("side") or "").upper()
            pos_buy = pos.type == _mt5().ORDER_TYPE_BUY
            if (side_db == "BUY") != pos_buy:
                mismatches.append(f"{row.get('id')}: side mismatch")
        if mismatches and self.halt_on_mismatch:
            self._halt("; ".join(mismatches))
        return mismatches
