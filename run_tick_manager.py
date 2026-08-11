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

import json
import logging
import os
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.trading_rules import trailing_params  # noqa: E402
# magic_for is needed inside _handle_row (module scope); mt5_executor keeps
# its MetaTrader5 import lazy, so this top-level import is safe on any OS.
from services.mt5_executor import magic_for  # noqa: E402

logger = logging.getLogger("tick_manager")
LOOP_SLEEP = float(os.environ.get("TICK_LOOP_SLEEP", 0.25))
ROWS_REFRESH_SECONDS = float(os.environ.get("TICK_ROWS_REFRESH", 3.0))


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
    # BUY TP1 (above entry) is touched when the HIGH reaches it; SELL when
    # the LOW does. The old inverted form booked halves AT A LOSS the moment
    # price sat below an above-entry target (MT5 journal 2026-08-10 09:50).
    return (candle_high >= tp1) if side == "BUY" else (candle_low <= tp1)



def _row_fresh(row: Dict[str, Any], max_age_minutes: float = 60.0) -> bool:
    """A row is placement-eligible only within max_age of created_at.
    Live incident 2026-08-10: a finished TP1_HIT row with no ticket was
    resurrected into fresh market orders every tick."""
    from datetime import datetime, timezone
    raw = str(row.get("created_at") or "")
    if not raw:
        return False
    try:
        created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created).total_seconds() / 60.0
    return 0 <= age <= max_age_minutes


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
        self._placement_retry_at: Dict[str, float] = {}  # stop 4x/sec refusal/log floods

    @staticmethod
    def _write_heartbeat(path: str = "tick_heartbeat.json") -> None:
        """Atomically publish a valid heartbeat; never expose an empty file."""
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ts": datetime.now(timezone.utc).isoformat()}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def _placement_due(self, trade_id: str) -> bool:
        """Retry broker refusals every 5s, not four times per second."""
        now = time.monotonic()
        if now < self._placement_retry_at.get(trade_id, 0.0):
            return False
        self._placement_retry_at[trade_id] = now + 5.0
        return True

    def _send_execution_signal(
        self, row: Dict[str, Any], *, actual_entry: float | None = None,
    ) -> None:  # pragma: no cover - VPS/Telegram integration
        """Deliver the rich signal only after an MT5 position/order exists.

        New demo rows carry ``telegram_signal_sent=False``. Old rows omit the
        field and are deliberately not re-announced during deployment.
        """
        if row.get("telegram_signal_sent") is not False or not self.telegram:
            return
        decision = deepcopy(row.get("signal_snapshot") or {})
        if not isinstance(decision, dict) or not decision:
            logger.error("cannot send execution signal for %s: no snapshot", row.get("id"))
            return
        decision["trade_id"] = str(row.get("id") or decision.get("trade_id") or "")
        if actual_entry and actual_entry > 0:
            signal = decision.setdefault("signal", {})
            entry = signal.setdefault("entry", {})
            entry["price"] = round(float(actual_entry), 2)
            decision["current_price"] = round(float(actual_entry), 2)
        custom = decision.get("_telegram_pending_message")
        try:
            sent = bool(
                self.telegram.send_message(str(custom), urgent=True)
                if custom else self.telegram.send_signal(decision)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("execution signal delivery crashed for %s: %s", row.get("id"), exc)
            return
        if not sent:
            logger.warning("execution signal delivery returned False for %s; retrying", row.get("id"))
            return
        sent_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        updates = {"telegram_signal_sent": True,
                   "telegram_signal_sent_at": sent_at}
        self.database.update_trade(str(row.get("id")), updates)
        row.update(updates)  # cached row must not resend before the 3s refresh

    def _seed_extreme(self, executor, row: Dict[str, Any], pos, side: str) -> float:
        """Restart-lossless extreme: seed from the broker's M1 candles since
        the position opened (operator directive 2026-08-10: the trailing must
        respect the highest price reached, including gaps while we were down).
        """
        from services.mt5_executor import _mt5_ready
        mt5 = _mt5_ready()
        sym = executor._sym(str(row.get("symbol") or "XAU/USD"))
        seed = float(row.get("entry_price") or 0)
        try:
            open_time = int(getattr(pos, "time", 0) or 0)
            if open_time > 0:
                rates = mt5.copy_rates_range(
                    sym, getattr(mt5, "TIMEFRAME_M1", 1),
                    open_time, int(time.time()))
                if rates is not None and len(rates):
                    if side == "BUY":
                        seed = max(float(r["high"]) for r in rates)
                    else:
                        seed = min(float(r["low"]) for r in rates)
        except Exception as exc:  # noqa: BLE001
            logger.warning("extreme seeding failed (%s); using entry", exc)
        return seed

    def _pending_stale(self, row: Dict[str, Any], tick) -> bool:
        """Demo mirror of the paper pending_freshness rules (config-driven):
        cancel when older than stale_after_hours, or when price ran away
        stale_after_excursion_points without filling."""
        pf = (self.config.get("pending_freshness") or {})
        if not pf.get("enabled", True):
            return False
        from datetime import datetime, timezone
        raw = str(row.get("created_at") or "")
        try:
            created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600.0
        except Exception:  # noqa: BLE001
            return False
        if age_h >= float(pf.get("stale_after_hours", 6)):
            return True
        entry = float(row.get("entry_price") or 0)
        if entry <= 0:
            return False
        side = str(row.get("type") or row.get("side") or "").upper()
        price = float(tick.bid if side == "BUY" else tick.ask)
        excursion = (price - entry) if side == "BUY" else (entry - price)
        return excursion >= float(pf.get("stale_after_excursion_points", 250)) * 0.10


    def _reconcile_pending(self, rows, executor) -> None:  # pragma: no cover
        """Broker pending orders whose DB row is gone (cancelled/replaced)
        must be cancelled at the broker too — otherwise ghost fills."""
        active = {magic_for(str(r.get("id"))) for r in rows}
        for o in executor.open_orders():
            mg = getattr(o, "magic", None)
            if mg is not None and mg not in active:
                if executor.cancel_order(int(o.ticket)):
                    self._notify(
                        f"🧪 DEMO: stale pending #{int(o.ticket)} cancelled "
                        f"at broker (row no longer active)")

    def _magic_rows(self) -> List[Dict[str, Any]]:
        rows = self.database.get_open_trades() or []
        return [r for r in rows if str(r.get("status") or "") in
                {"PENDING", "OPEN", "TP1_HIT", "PARTIAL"}]

    def run_forever(self) -> None:  # pragma: no cover - VPS only
        from services.mt5_executor import Mt5DemoExecutor, magic_for, _mt5_ready
        mt5 = _mt5_ready()  # initialize ONCE — the terminal must be attached
        executor = Mt5DemoExecutor(self.config, telegram=self.telegram)
        # DB rows are throttled (Supabase free-tier protection — live incident:
        # usage-limit badge): ticks stay 0.25s via symbol_info_tick, while the
        # trade BOOK refreshes every ROWS_REFRESH_SECONDS. Row state changes
        # (new signal / status flip) are rare; 3s staleness is invisible.
        rows: List[Dict[str, Any]] = []
        rows_at = 0.0
        beat_at = 0.0
        while True:
            try:
                now = time.time()
                if now - rows_at >= ROWS_REFRESH_SECONDS:
                    rows = self._magic_rows()
                    rows_at = now
                    self._reconcile_pending(rows, executor)
                if now - beat_at >= 30:
                    try:
                        self._write_heartbeat()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("tick heartbeat write failed: %s", exc)
                    finally:
                        # Do not hammer the disk four times/second on failure.
                        beat_at = now
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
        if status not in {"PENDING", "OPEN", "TP1_HIT", "PARTIAL"}:
            return  # cached row was closed/cancelled before the next DB refresh
        if pos is None and row.get("close_price") is not None:
            return  # exit already booked; never manage/resurrect a closed row

        if status == "PENDING":
            if row.get("requested_exit"):
                cancel_updates = {"status": "CANCELLED", "result": "CANCELLED",
                                  "requested_exit": False,
                                  "reasons": ["thesis exit before activation"]}
                self.database.update_trade(tid, cancel_updates)
                row.update(cancel_updates)
                return
            if self._pending_stale(row, tick):
                # Stale pending (age or runaway excursion): cancel in the DB;
                # _reconcile_pending cancels the broker order and cards it.
                # If it was never placed at the broker, card here instead.
                had_ticket = bool(row.get("mt5_ticket"))
                stale_updates = {"status": "CANCELLED", "result": "CANCELLED",
                                 "reasons": ["stale pending (freshness mirror)"]}
                self.database.update_trade(tid, stale_updates)
                row.update(stale_updates)
                if not had_ticket:
                    self._notify(
                        f"🧪 DEMO: MT5 applied: stale pending cancelled "
                        f"(age/excursion) — never placed")
                return
            if pos is not None:  # broker filled the pending
                # Book the ACTUAL fill price: PnL/BE/trailing must run on the
                # broker's execution, not our planned level.
                fill_updates = {"status": "OPEN",
                                "entry_price": round(float(pos.price_open), 2),
                                "mt5_ticket": int(pos.ticket)}
                self.database.update_trade(tid, fill_updates)
                row.update(fill_updates)
                self._send_execution_signal(row, actual_entry=float(pos.price_open))
                self._notify(
                    f"🧪 DEMO: MT5 applied: pending filled @ "
                    f"{pos.price_open:.2f} · ticket {int(pos.ticket)} "
                    f"(actual fill)")
            elif not row.get("mt5_ticket"):
                # The pending order has never been sent to MT5 — send it now.
                # ensure_ticket is idempotent (position + outstanding order).
                if not self._placement_due(tid):
                    return
                kind = str(row.get("order_type") or "").upper()
                if not kind.endswith("LIMIT"):
                    kind = "BUY_LIMIT" if side == "BUY" else "SELL_LIMIT"
                ticket = executor.ensure_ticket(
                    tid, side, kind, float(row.get("entry_price") or 0),
                    float(row.get("stop_loss") or 0),
                    float(row.get("tp2") or 0), row.get("symbol"))
                if ticket:
                    self._placement_retry_at.pop(tid, None)
                    updates = {"mt5_ticket": ticket}
                    self.database.update_trade(tid, updates)
                    row.update(updates)
                    lo = executor.last_order
                    self._send_execution_signal(row)
                    if not lo.get("existing"):
                        self._notify(
                            f"🧪 DEMO: MT5 applied: {lo.get('kind', 'LIMIT')} "
                            f"order placed · vol {lo.get('volume')} @ "
                            f"{lo.get('price', float(row.get('entry_price') or 0)):.2f} "
                            f"· ticket {ticket}")
                    else:
                        logger.info("repaired missing DB ticket for existing pending %s -> %s",
                                    tid, ticket)
            # If Telegram was unavailable after broker placement, retry the
            # rich card on later ticks without touching the existing order.
            if row.get("mt5_ticket"):
                self._send_execution_signal(row)
            return
        if pos is None:
            # Never-placed OPEN rows (no ticket, no close) are ALWAYS
            # placement-eligible whatever their age — the card already went
            # to Telegram; silently starving them (old 60-min gate) is the
            # "card but no MT5 order" gap. Resurrection protection applies
            # only to rows that already have a close/ticket history.
            never_placed = (not row.get("mt5_ticket")) and \
                row.get("close_price") is None
            eligible = (status == "PENDING") or \
                (status == "OPEN" and (never_placed or _row_fresh(row)))
            if (not row.get("mt5_ticket")) and eligible:
                # PENDING rows stay placement-eligible for their whole life
                # (paper keeps pendings alive for hours; staleness cancella-
                # tion happens in the DB and _reconcile_pending mirrors it at
                # the broker). OPEN rows only while fresh — finished or stale
                # rows must NEVER be resurrected into new orders.
                if not self._placement_due(tid):
                    return
                ticket = executor.ensure_ticket(
                    tid, side, "MARKET", 0.0,
                    float(row.get("stop_loss") or 0),
                    float(row.get("tp2") or 0), row.get("symbol"))
                if ticket:
                    self._placement_retry_at.pop(tid, None)
                    pos2 = executor._position_by_magic(magic)
                    upd: Dict[str, Any] = {"mt5_ticket": ticket}
                    if pos2 is not None:
                        upd["entry_price"] = round(float(pos2.price_open), 2)
                        upd["mt5_ticket"] = int(pos2.ticket)
                    elif float((executor.last_order or {}).get("price") or 0) > 0:
                        upd["entry_price"] = round(float(executor.last_order["price"]), 2)
                    self.database.update_trade(tid, upd)
                    row.update(upd)
                    actual_entry = float(upd.get("entry_price") or 0)
                    self._send_execution_signal(row, actual_entry=actual_entry or None)
                    if not (executor.last_order or {}).get("existing"):
                        if actual_entry:
                            self._notify(
                                f"🧪 DEMO: MT5 applied: MARKET filled · vol "
                                f"{executor.lot} @ {actual_entry:.2f} · "
                                f"ticket {upd['mt5_ticket']}")
                        else:
                            self._notify(
                                f"🧪 DEMO: MT5 applied: MARKET order sent · "
                                f"ticket {ticket}")
                    else:
                        logger.info("repaired missing DB ticket for existing position %s -> %s",
                                    tid, upd["mt5_ticket"])
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
                close_updates = {"status": status, "close_price": round(close, 2),
                                 "pnl_points": round(pnl_pts, 1)}
                self.database.update_trade(tid, close_updates)
                row.update(close_updates)
                self._notify(
                    f"🧪 DEMO: MT5 applied: position closed @ {close:.2f} "
                    f"({status}) · vol {exit_info.get('volume')} · "
                    f"ticket {exit_info.get('position')} "
                    f"{pnl_pts:+.0f} pts / {exit_info['profit']:+.2f}$")
            else:
                keep = status if status in {"TP1_HIT", "PARTIAL"} else "SL_HIT"
                close = tick.bid if side == "BUY" else tick.ask
                fallback_updates = {"status": keep, "close_price": round(close, 2)}
                self.database.update_trade(tid, fallback_updates)
                row.update(fallback_updates)
                self._notify(
                    f"🧪 DEMO: position closed by broker @ {close:.2f} "
                    f"(no exit deal found — labelled {keep}, verify)")
            return

        # A market DEAL can be accepted one terminal tick before positions_get
        # exposes it. Reconcile the broker's actual fill on every sight instead
        # of leaving the planned Telegram quote in the book forever.
        actual_entry = round(float(getattr(pos, "price_open", 0.0) or 0.0), 2)
        actual_ticket = int(getattr(pos, "ticket", 0) or 0)
        mirror_updates: Dict[str, Any] = {}
        may_reconcile_entry = bool(row.get("mt5_ticket")) or \
            row.get("telegram_signal_sent") is False
        if (may_reconcile_entry and actual_entry > 0
                and abs(actual_entry - float(row.get("entry_price") or 0)) > 0.01):
            mirror_updates["entry_price"] = actual_entry
        may_reconcile_ticket = bool(row.get("mt5_ticket")) or \
            row.get("telegram_signal_sent") is False
        if (may_reconcile_ticket and actual_ticket
                and int(row.get("mt5_ticket") or 0) != actual_ticket):
            mirror_updates["mt5_ticket"] = actual_ticket
        if mirror_updates:
            self.database.update_trade(tid, mirror_updates)
            row.update(mirror_updates)
            logger.info("reconciled broker fill for %s: %s", tid, mirror_updates)
        self._send_execution_signal(row, actual_entry=actual_entry or None)

        # thesis-exit handoff: analysis requested, broker executes first.
        if row.get("requested_exit"):
            if pos is None:
                self.database.update_trade(tid, {"requested_exit": False})
            elif executor.close_position(tid, row.get("symbol")):
                lc = executor.last_close
                pnl = (lc["price"] - float(row.get("entry_price") or 0)) * 10.0
                if side == "SELL":
                    pnl = -pnl
                thesis_updates = {"status": "THESIS_EXIT", "requested_exit": False,
                                  "close_price": lc["price"],
                                  "pnl_points": round(pnl, 1)}
                self.database.update_trade(tid, thesis_updates)
                row.update(thesis_updates)
                self._notify(
                    f"🧪 DEMO: MT5 applied: thesis exit closed @ "
                    f"{lc['price']:.2f} · vol {lc['volume']} · "
                    f"ticket {lc['ticket']} · {pnl:+.0f} pts")
            return
        if row.get("requested_partial") and pos is not None \
                and not row.get("partial_close"):
            if executor.partial_close_at_tp1(tid, 0.5, row.get("symbol")):
                lp = executor.last_partial
                scale_updates = {"requested_partial": False,
                                 "partial_close": True, "status": "PARTIAL"}
                self.database.update_trade(tid, scale_updates)
                row.update(scale_updates)
                if not lp.get("existing"):
                    self._notify(
                        f"🧪 DEMO: MT5 applied: thesis scale-out closed · vol "
                        f"{lp.get('volume')} @ {lp.get('price', 0):.2f} · "
                        f"remaining {lp.get('remaining')} · ticket "
                        f"{lp.get('ticket')}")

        entry = float(row.get("entry_price") or 0)
        stop = float(row.get("stop_loss") or 0)
        tp1 = float(row.get("tp1") or 0)
        risk = abs(entry - float(row.get("initial_stop_loss") or stop))
        pv = 0.10  # codebase point = $0.10 on gold
        risk_points = risk / pv  # price delta -> codebase points
        # Use the price at which the position can actually be closed:
        # BUY closes on Bid; SELL closes on Ask. This keeps BE/TP1/trailing
        # aligned with MT5 rather than triggering one spread too early.
        price = tick.bid if side == "BUY" else tick.ask
        if tid not in self._extremes:
            # first sight (fresh start/restart): seed from broker history so
            # peaks hit while we were down still ratchet the stop.
            self._extremes[tid] = self._seed_extreme(executor, row, pos, side)
        extreme = self._extremes[tid]
        extreme = max(extreme, price) if side == "BUY" else min(extreme, price)
        self._extremes[tid] = extreme

        # 0) SL drift guard: a manually/broker-moved stop that is WORSE than
        # the book's level is restored every tick (operator directive).
        broker_sl = float(getattr(pos, "sl", 0) or 0)
        db_stop = float(row.get("stop_loss") or 0)
        tp2 = float(row.get("tp2") or 0)
        if broker_sl > 0 and db_stop > 0 and abs(broker_sl - db_stop) > 0.5:
            worse = (broker_sl < db_stop) if side == "BUY" else \
                (broker_sl > db_stop)
            if worse and executor.apply_stop(tid, db_stop, tp2,
                                             row.get("symbol")):
                self._notify(
                    f"🧪 DEMO: MT5 applied: SL drift corrected back to "
                    f"{db_stop:.2f} (broker had {broker_sl:.2f})")

        # 1) breakeven
        if decide_be(side, entry, price, risk_points,
                     self._trail["early_breakeven_points"], 0.5,
                     bool(row.get("sl_moved_to_entry"))):
            # Move FIRST at the broker; the card is sent ONLY when the
            # broker confirms. On refusal nothing is booked, so the next
            # tick retries (operator directive: truthful messages only).
            if executor.apply_stop(tid, entry, float(row.get("tp2") or 0),
                                   row.get("symbol")):
                be_updates = {"sl_moved_to_entry": True, "stop_loss": entry}
                self.database.update_trade(tid, be_updates)
                row.update(be_updates)
                stop = entry
                self._notify(f"🧪 DEMO: breakeven armed @ {entry:.2f} "
                             f"(confirmed at broker)")

        # 2) TP1 partial — close ONLY the half (operator directive). Book it
        # only when the broker actually executed it. On refusal: keep
        # retrying every tick and report the broker's reason once per trade.
        # decide_tp1 expects candle low/high. On a live tick the executable
        # BUY high is Bid and the executable SELL low is Ask.
        if decide_tp1(side, tp1, tick.ask, tick.bid,
                      bool(row.get("partial_close"))):
            if executor.partial_close_at_tp1(tid, 0.5, row.get("symbol")):
                self._partial_alerted.discard(tid)
                partial_updates = {"partial_close": True,
                                   "status": "TP1_HIT"}
                self.database.update_trade(tid, partial_updates)
                row.update(partial_updates)
                lp = executor.last_partial
                if not lp.get("existing"):
                    self._notify(
                        f"🧪 DEMO: MT5 applied: TP1 partial closed · vol "
                        f"{lp.get('volume')} @ {lp.get('price', tp1):.2f} · "
                        f"remaining {lp.get('remaining')} · ticket "
                        f"{lp.get('ticket')}")
                else:
                    logger.info("restored TP1 mirror from broker history for %s", tid)
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
            if executor.apply_stop(tid, new_stop, float(row.get("tp2") or 0),
                                   row.get("symbol")):
                self.database.update_trade(tid, {"stop_loss": new_stop})
                row["stop_loss"] = new_stop
                self._notify(f"🧪 DEMO: trailing stop moved to {new_stop:.2f} "
                             f"(confirmed at broker)")
            # refusal: apply_stop logged the retcode; DB untouched -> retried

    def _notify(self, text: str) -> None:  # pragma: no cover
        logger.info("[card] %s", text)  # visibility: cards hit the log too
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
    if not acquire_single_instance("tick_manager.pid", "run_tick_manager.py"):
        logger.info("tick manager already running; exiting duplicate instance")
        return
    cfg = load_config()
    if os.environ.get("EXECUTION_MODE") != "mt5_demo":
        logger.info("tick manager idle (EXECUTION_MODE != mt5_demo)")
        return
    TickManager(cfg, TelegramService(cfg), DatabaseService(cfg)).run_forever()


if __name__ == "__main__":
    main()
