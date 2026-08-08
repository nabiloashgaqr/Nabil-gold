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


# ── live loop ───────────────────────────────────────────────────────────────

class TickManager:
    def __init__(self, config: Dict[str, Any], telegram=None, database=None):
        self.config = config
        self.telegram = telegram
        self.database = database
        self._extremes: Dict[str, float] = {}
        self._trail = trailing_params(config)

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
                    sym = str(row.get("symbol") or "XAU/USD").replace("/", "")
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
                self.database.update_trade(tid, {"status": "OPEN"})
                self._notify(f"🧪 DEMO: pending activated @ {pos.price_open:.2f}")
            return
        if pos is None:  # broker closed it (SL/TP2)
            close = tick.bid
            self.database.update_trade(
                tid, {"status": "SL_HIT", "close_price": round(close, 2)})
            self._notify(f"🧪 DEMO: position closed by broker @ {close:.2f}")
            return

        entry = float(row.get("entry_price") or 0)
        stop = float(row.get("stop_loss") or 0)
        tp1 = float(row.get("tp1") or 0)
        risk = abs(entry - float(row.get("initial_stop_loss") or stop))
        point_value = 0.01  # XAU/USD MT5 point; convert pts via *0.01*10? pts conv below
        pv = 0.10  # codebase point = $0.10 on gold
        price = tick.bid if side == "SELL" else tick.ask
        extreme = self._extremes.get(tid, price)
        extreme = min(extreme, price) if side == "BUY" else max(extreme, price)
        self._extremes[tid] = extreme

        # 1) breakeven
        if decide_be(side, entry, price, risk / 10.0,
                     self._trail["early_breakeven_points"], 0.5,
                     bool(row.get("sl_moved_to_entry"))):
            executor.apply_stop(tid, entry, float(row.get("tp2") or 0),
                                row.get("symbol"))
            self.database.update_trade(tid, {"sl_moved_to_entry": True,
                                             "stop_loss": entry})
            self._notify(f"🧪 DEMO: breakeven armed @ {entry:.2f}")

        # 2) TP1 partial
        if decide_tp1(side, tp1, tick.bid, tick.ask,
                      bool(row.get("partial_close"))):
            executor.partial_close_at_tp1(tid, 0.5, row.get("symbol"))
            self.database.update_trade(tid, {"partial_close": True,
                                             "status": "TP1_HIT"})
            self._notify(f"🧪 DEMO: TP1 partial booked @ {tp1:.2f}")

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
