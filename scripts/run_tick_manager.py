"""Tick-level manager (VPS, mt5_demo only).

Analysis stays on the 5-minute cycle; EXECUTION management runs EVERY TICK:
pending activation, breakeven, TP1 partial, trailing, and broker-side closes
are detected and applied tick-by-tick through MT5, with DB writes and cards
only on state changes. MT5 is the execution authority; the DB mirrors it.

Pure decision helpers are unit-tested (tests/test_tick_manager_logic.py).
"""
from __future__ import annotations

# --- VPS: load .env if present (real env vars ALWAYS win over .env) ---
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()  # override=False: task-wrapper vars take precedence
except Exception:
    pass

import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.trading_rules import trailing_params  # noqa: E402
# magic_for is needed inside _handle_row (module scope); mt5_executor keeps
# its MetaTrader5 import lazy, so this top-level import is safe on any OS.
from services.mt5_executor import magic_for  # noqa: E402

logger = logging.getLogger("tick_manager")
LOOP_SLEEP = float(os.environ.get("TICK_LOOP_SLEEP", 0.25))


# ── pure decisions (unit-tested) ────────────────────────────────────────────

def decide_be(
    side: str, entry: float, current: float, risk_points: float,
    be_points: float, min_be_rr: float, already: bool, point_value: float = 0.10,
) -> bool:
    """Arm breakeven once price travelled >= be_points AND >= min_be_rr
    (both in codebase points; fav converted from price)."""
    if already or entry <= 0 or risk_points <= 0:
        return False
    fav_points = ((entry - current) if side == "SELL" else (current - entry)) / point_value
    return fav_points >= be_points and fav_points >= min_be_rr * risk_points


def decide_trailing(
    side: str, entry: float, current_stop: float, favorable_extreme: float,
    gap_points: float, step_points: float, point_value: float,
) -> Optional[float]:
    """Ratchet the stop behind the favorable extreme (gap), in steps."""
    if side == "BUY":
        candidate = favorable_extreme - gap_points * point_value
        if candidate > entry and candidate >= current_stop + step_points * point_value:
            return round(candidate, 2)
    else:
        candidate = favorable_extreme + gap_points * point_value
        if candidate < entry and candidate <= current_stop - step_points * point_value:
            return round(candidate, 2)
    return None


def decide_tp1(side: str, tp1: float, candle_low: float, candle_high: float,
               done: bool) -> bool:
    if done or tp1 <= 0:
        return False
    return (candle_low <= tp1) if side == "BUY" else (candle_high >= tp1)


def classify_broker_exit(side: str, deal_price: float, entry: float,
                         stop: float, tp2: float) -> str:
    """Map a broker-side exit price to our status labels.

    The broker only closes on SL/TP, so an exit between stop and TP2 means
    the (already moved) trailing/breakeven stop was the trigger. Pure +
    unit-tested (tests/test_mt5_execution_correctness.py).
    """
    eps = 1e-9
    if side == "BUY":
        if tp2 and deal_price >= tp2 - eps:
            return "TP2_HIT"
        if deal_price <= (stop or entry) + eps:
            return "SL_HIT"
        return "TRAILING_SL_HIT"
    if tp2 and deal_price <= tp2 + eps:
        return "TP2_HIT"
    if deal_price >= (stop or entry) - eps:
        return "SL_HIT"
    return "TRAILING_SL_HIT"


# ── live loop ───────────────────────────────────────────────────────────────

class TickManager:
    def __init__(self, config: Dict[str, Any], telegram=None, database=None):
        self.config = config
        self.telegram = telegram
        self.database = database
        self._extremes: Dict[str, float] = {}
        self._trail = trailing_params(config)
        self._partial_alerted: set = set()  # refused-partial reported once/trade

    def _magic_rows(self) -> List[Dict[str, Any]]:
        rows = self.database.get_open_trades() or []
        return [r for r in rows if str(r.get("status") or "") in
                {"PENDING", "OPEN", "TP1_HIT", "PARTIAL"}]

    def run_forever(self) -> None:  # pragma: no cover - VPS only
        import MetaTrader5 as mt5
        from services.mt5_executor import Mt5DemoExecutor, magic_for
        executor = Mt5DemoExecutor(self.config, telegram=self.telegram)
        while True:
            try:
                rows = self._magic_rows()
                for row in rows:
                    # Route through the broker symbol map (XAU/USD -> XAUUSD.s);
                    # a raw slash-strip goes blind on suffix brokers.
                    sym = executor._sym(str(row.get("symbol") or "XAU/USD"))
                    tick = mt5.symbol_info_tick(sym)
                    if tick is None:
                        continue
                    self._handle_row(row, tick, executor, mt5)
            except Exception as exc:  # noqa: BLE001 - loop must survive
                logger.warning("tick cycle error: %s", exc)
            time.sleep(LOOP_SLEEP)

    def _handle_row(self, row, tick, executor, mt5) -> None:  # pragma: no cover
        tid = str(row.get("id"))
        side = str(row.get("type") or row.get("side") or "").upper()
        magic = magic_for(tid)
        pos = executor._position_by_magic(magic)
        status = str(row.get("status") or "")

        if status == "PENDING":
            if pos is not None:  # broker filled the pending
                # Book the ACTUAL fill price: PnL/BE/trailing must run on the
                # broker's execution, not our planned level.
                self.database.update_trade(
                    tid, {"status": "OPEN",
                          "entry_price": round(float(pos.price_open), 2)})
                self._notify(
                    f"🧪 DEMO: pending activated @ {pos.price_open:.2f} "
                    f"(actual fill)")
            elif not row.get("mt5_ticket"):
                # The pending order has never been sent to MT5 — send it now.
                # ensure_ticket is idempotent (position + outstanding order).
                kind = str(row.get("order_type") or "").upper()
                if not kind.endswith("LIMIT"):
                    kind = "BUY_LIMIT" if side == "BUY" else "SELL_LIMIT"
                ticket = executor.ensure_ticket(
                    tid, side, kind, float(row.get("entry_price") or 0),
                    float(row.get("stop_loss") or 0),
                    float(row.get("tp2") or 0), row.get("symbol"))
                if ticket:
                    self.database.update_trade(tid, {"mt5_ticket": ticket})
                    self._notify(
                        f"🧪 DEMO: pending sent to MT5 @ "
                        f"{float(row.get('entry_price') or 0):.2f} "
                        f"(ticket {ticket})")
            return
        if pos is None:
            if not row.get("mt5_ticket"):
                # New signal that never reached MT5 — open it NOW (market).
                ticket = executor.ensure_ticket(
                    tid, side, "MARKET", 0.0,
                    float(row.get("stop_loss") or 0),
                    float(row.get("tp2") or 0), row.get("symbol"))
                if ticket:
                    pos2 = executor._position_by_magic(magic)
                    upd: Dict[str, Any] = {"mt5_ticket": ticket}
                    if pos2 is not None:
                        upd["entry_price"] = round(float(pos2.price_open), 2)
                    self.database.update_trade(tid, upd)
                    if "entry_price" in upd:
                        self._notify(
                            f"🧪 DEMO: MARKET filled @ {upd['entry_price']:.2f} "
                            f"(ticket {ticket})")
                    else:
                        self._notify(f"🧪 DEMO: MARKET sent (ticket {ticket})")
                return
            # broker closed it (SL / TP2 / trailing stop)
            # Mirror the REAL broker exit: deal price + realized P&L, not
            # the live tick at the moment we noticed the position was gone.
            exit_info = executor.last_exit(tid)
            entry = float(row.get("entry_price") or 0)
            stop = float(row.get("stop_loss") or 0)
            tp2 = float(row.get("tp2") or 0)
            if exit_info and exit_info["price"] > 0:
                close = exit_info["price"]
                status = classify_broker_exit(side, close, entry, stop, tp2)
                pnl_pts = (close - entry) * 10.0
                if side == "SELL":
                    pnl_pts = -pnl_pts
                self.database.update_trade(
                    tid, {"status": status, "close_price": round(close, 2),
                          "pnl_points": round(pnl_pts, 1)})
                self._notify(
                    f"🧪 DEMO: closed by broker @ {close:.2f} ({status}) "
                    f"{pnl_pts:+.0f} pts / {exit_info['profit']:+.2f}$")
            else:
                close = tick.bid
                self.database.update_trade(
                    tid, {"status": "SL_HIT", "close_price": round(close, 2)})
                self._notify(
                    f"🧪 DEMO: position closed by broker @ {close:.2f} "
                    f"(no exit deal found — labelled SL_HIT, verify)")
            return

        entry = float(row.get("entry_price") or 0)
        stop = float(row.get("stop_loss") or 0)
        tp1 = float(row.get("tp1") or 0)
        risk = abs(entry - float(row.get("initial_stop_loss") or stop))
        pv = 0.10  # codebase point = $0.10 on gold
        risk_points = risk / pv  # price delta -> codebase points
        price = tick.bid if side == "SELL" else tick.ask
        extreme = self._extremes.get(tid, price)
        extreme = min(extreme, price) if side == "BUY" else max(extreme, price)
        self._extremes[tid] = extreme

        # 1) breakeven
        if decide_be(side, entry, price, risk_points,
                     self._trail["early_breakeven_points"], 0.5,
                     bool(row.get("sl_moved_to_entry"))):
            executor.apply_stop(tid, entry, float(row.get("tp2") or 0),
                                row.get("symbol"))
            self.database.update_trade(tid, {"sl_moved_to_entry": True,
                                             "stop_loss": entry})
            self._notify(f"🧪 DEMO: breakeven armed @ {entry:.2f}")

        # 2) TP1 partial — close ONLY the half (operator directive). Book it
        # only when the broker actually executed it. On refusal: keep
        # retrying every tick and report the broker's reason once per trade.
        if decide_tp1(side, tp1, tick.bid, tick.ask,
                      bool(row.get("partial_close"))):
            if executor.partial_close_at_tp1(tid, 0.5, row.get("symbol")):
                self._partial_alerted.discard(tid)
                self.database.update_trade(tid, {"partial_close": True,
                                                 "status": "TP1_HIT"})
                self._notify(f"🧪 DEMO: TP1 partial booked @ {tp1:.2f}")
            elif tid not in self._partial_alerted:
                self._partial_alerted.add(tid)
                self._notify(
                    f"🧪 DEMO: TP1 partial REJECTED for {tid}: "
                    f"{getattr(executor, 'last_error', 'unknown')} — "
                    f"retrying every tick")

        # 3) trailing ratchet
        new_stop = decide_trailing(side, entry, stop, extreme,
                                   self._trail["distance_points"],
                                   self._trail["step_points"], pv)
        if new_stop:
            executor.apply_stop(tid, new_stop, float(row.get("tp2") or 0),
                                row.get("symbol"))
            self.database.update_trade(tid, {"stop_loss": new_stop})

    def _notify(self, text: str) -> None:  # pragma: no cover
        if self.telegram:
            try:
                self.telegram.send_message(text)
            except Exception:  # noqa: BLE001
                pass


def main() -> None:  # pragma: no cover - VPS only
    from services.database import DatabaseService
    from services.telegram_bot import TelegramService
    from utils.helpers import load_config
    from utils.single_instance import acquire_single_instance

    logging.basicConfig(level=logging.INFO)
    if not acquire_single_instance("tick_manager.pid"):
        logger.info("tick manager already running; exiting duplicate instance")
        return
    cfg = load_config()
    if os.environ.get("EXECUTION_MODE") != "mt5_demo":
        logger.info("tick manager idle (EXECUTION_MODE != mt5_demo)")
        return
    TickManager(cfg, TelegramService(cfg), DatabaseService(cfg)).run_forever()


if __name__ == "__main__":
    main()
