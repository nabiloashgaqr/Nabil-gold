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


def magic_for(trade_id: str) -> int:
    return 1000000 + (zlib.crc32(trade_id.encode()) % 8999999)


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
            for pos in _mt5().positions_get() or []:
                if pos.magic == magic:
                    return pos
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
        """Idempotent open. Returns ticket or None (refused/failed)."""
        if self.halted():
            return None
        if self._orders_today >= self.max_per_day:
            logger.warning("Demo order refused: daily cap reached")
            return None
        magic = magic_for(trade_id)
        existing = self._position_by_magic(magic)
        if existing:
            return int(existing.ticket)
        mt5 = _mt5()
        sym = self._sym(symbol)
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
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
                "type_filling": mt5.ORDER_FILLING_IOC,
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
        try:
            res = mt5.order_send(request)
        except Exception as exc:  # noqa: BLE001
            logger.error("order_send crashed: %s", exc)
            return None
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("order_send refused: %s", getattr(res, "comment", res))
            return None
        self._orders_today += 1
        return int(res.order)

    def apply_stop(self, trade_id: str, new_sl: float, tp: float, symbol: str) -> bool:
        magic = magic_for(trade_id)
        pos = self._position_by_magic(magic)
        if not pos:
            return False
        mt5 = _mt5()
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": self._sym(symbol),
            "position": int(pos.ticket),
            "sl": float(new_sl),
            "tp": float(tp),
        }
        try:
            res = mt5.order_send(request)
            return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)
        except Exception as exc:  # noqa: BLE001
            logger.error("SLTP modify crashed: %s", exc)
            return False

    def partial_close_at_tp1(self, trade_id: str, fraction: float, symbol: str) -> bool:
        """Partial close; on broker refusal fall back to full close + reopen
        the remainder with the same SL/TP."""
        mt5 = _mt5()
        magic = magic_for(trade_id)
        pos = self._position_by_magic(magic)
        if not pos:
            return False
        sym = self._sym(symbol)
        part = round(float(pos.volume) * fraction, 2)
        if part <= 0:
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
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        try:
            res = mt5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                return True
        except Exception as exc:  # noqa: BLE001
            logger.error("partial close crashed: %s", exc)
            return False
        logger.warning("partial unsupported; full-close + reopen remainder")
        remaining = round(float(pos.volume) - part, 2)
        sl, tp, otype = float(pos.sl), float(pos.tp), pos.type
        close_all = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": float(pos.volume),
            "type": close_type,
            "position": int(pos.ticket),
            "price": tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask,
            "magic": magic,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        reopen = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": remaining,
            "type": otype,
            "price": tick.ask if otype == mt5.ORDER_TYPE_BUY else tick.bid,
            "sl": sl,
            "tp": tp,
            "magic": magic,
            "deviation": self.deviation,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        try:
            r1 = mt5.order_send(close_all)
            r2 = mt5.order_send(reopen)
            return bool(r1 and r2 and r1.retcode == mt5.TRADE_RETCODE_DONE
                        and r2.retcode == mt5.TRADE_RETCODE_DONE)
        except Exception as exc:  # noqa: BLE001
            logger.error("full-close+reopen crashed: %s", exc)
            self._halt(f"reopen failed for {trade_id}: {exc}")
            return False

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
