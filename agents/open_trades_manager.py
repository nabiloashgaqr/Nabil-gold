"""Open Trades Manager Agent.

يتابع الصفقات المفتوحة في Supabase/JSON، يحسب الربح والخسارة، يرسل تحديثات
تليجرام عند الاقتراب من الهدف أو تحقق الأهداف/الوقف/التعادل، ويمنع تكرار
الرسائل عبر حقل ``updates_sent``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from utils.helpers import calculate_pips, load_config
from utils.instruments import points_to_price


class OpenTradesManager(BaseAgent):
    """Evaluate and update open trades for the stateless GitHub Actions runner."""

    name = "open_trades_manager"
    OPEN_STATUSES = {"OPEN", "PARTIAL", "TP1_HIT"}
    CLOSED_STATUSES = {"TP2_HIT", "SL_HIT", "BE_HIT", "EXPIRED", "MANUAL_CLOSE"}
    # Telegram notifications are intentionally restricted to real trade-state
    # changes. Informational markers such as NEAR_TP1 / LONG_RUNNING /
    # EXIT_WARNING are still persisted in updates_sent to avoid repeated
    # internal triggers, but they do not send Telegram messages. This matches
    # the production rule: "send only when something actually changed".
    NOTIFIABLE_EVENTS = {
        "ORDER_FILLED",
        "MOVE_SL_TO_BE",
        "TRAILING_SL_UPDATED",
        "TP1_HIT",
        "TP2_HIT",
        "SL_HIT",
        "TRAILING_SL_HIT",
        "BE_HIT",
        "EXPIRED",
        "MANUAL_CLOSE",
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        super().__init__(config or load_config())
        self.management = self.config.get("trade_management", {})
        self.near_tp1_progress = float(self.management.get("near_tp1_progress", 0.80))
        self.time_warning_hours = float(self.management.get("time_warning_hours", 4))
        self.expire_after_hours = float(self.management.get("expire_after_hours", 8))
        # When True, a time-expired trade that is in profit AND already protected
        # (stop moved to entry / breakeven or better) is NOT force-closed; its
        # trailing/breakeven stop is left to manage the exit instead.
        self.keep_protected_winners_open = bool(self.management.get("keep_protected_winners_open", True))
        self.auto_be = bool(self.management.get("auto_move_sl_to_entry_after_tp1", True))

        # Genuine progressive trailing stop (beyond the initial breakeven lock).
        # Note: services/trailing_stop.py (TrailingStopManager) was written against
        # fields that don't exist in the actual trades schema (take_profit,
        # quantity, trailing_active) and re-triggers on every run with no
        # persisted activation guard, so it's not used here. This implements
        # the same trailing_distance/trailing_step config correctly against the
        # real schema (tp1/tp2, status, sl_moved_to_entry) instead.
        ts_config = self.config.get("trailing_stop", {})
        self.trailing_enabled = bool(ts_config.get("enabled", False))
        # Required production behaviour:
        #   1) at +100 points -> move SL to entry immediately;
        #   2) after that, keep a 100-point trailing gap;
        #   3) move the SL only in 30-point steps.
        # Values remain configurable, but the defaults now match the live rule.
        self.trailing_distance = float(ts_config.get("trailing_distance", 100.0))
        self.trailing_step = float(ts_config.get("trailing_step", 30.0))
        self.trailing_min_profit_lock = float(ts_config.get("min_profit_lock", 0.0))

        # Early breakeven: move SL to entry once the trade is +N points in
        # profit, WITHOUT waiting for TP1. Production default is 100 points.
        # Points convention: 10 pts = $1.
        self.early_breakeven_points = float(ts_config.get("early_breakeven_points", 100.0))

        # Fixed-risk mode: track scale-in info
        oe = self.config.get("order_execution", {}) or {}
        self.entry_style = str(oe.get("entry_style", "market")).lower()
        self.fr = oe.get("fixed_risk", {}) or {}

        # Hybrid entry: auto-convert stale PENDING orders to MARKET after N cycles.
        oe = self.config.get("order_execution", {}) or {}
        self.entry_style = str(oe.get("entry_style", "market")).lower()
        self.pending_order_max_cycles = int(oe.get("pending_order_max_cycles", 6) or 6)

    def update_trades(
        self,
        open_trades: List[Dict[str, Any]],
        current_price: float,
        database: Any | None = None,
        telegram: Any | None = None,
        now: datetime | None = None,
        candle_high: float | None = None,
        candle_low: float | None = None,
    ) -> List[Dict[str, Any]]:
        """Evaluate all open trades, persist updates and send Telegram events.

        ``current_price`` is the latest candle close. ``candle_high``/``candle_low``
        are optional intrabar extremes from the same update candle. When supplied,
        TP/SL/BE/fill checks use high/low so a level touched inside the 5-minute
        candle is not missed just because the candle closed back away from it.
        """
        evaluations: List[Dict[str, Any]] = []
        now = now or datetime.now(timezone.utc)
        for trade in open_trades:
            evaluation = self.evaluate_trade(
                trade,
                current_price,
                now=now,
                candle_high=candle_high,
                candle_low=candle_low,
            )
            evaluations.append(evaluation)
            trade_id = str(trade.get("id", ""))
            events = evaluation.get("events", []) or []
            notification_events = [event for event in events if event in self.NOTIFIABLE_EVENTS]
            evaluation["notification_events"] = notification_events

            # Send critical trade-management notifications BEFORE writing the DB
            # update. If Supabase has a transient/schema issue, the user still
            # receives the important event (SL moved / trailing moved / TP / SL)
            # instead of silently missing it because the DB write happened first.
            # Informational-only events are not sent to Telegram.
            if telegram and notification_events:
                delivered = False
                try:
                    # Send ONE combined message per trade per cycle instead of a
                    # separate message per material state change.
                    if hasattr(telegram, "send_trade_events"):
                        delivered = bool(
                            telegram.send_trade_events(
                                trade, notification_events, current_price, evaluation.get("pnl_points", 0), evaluation
                            )
                        )
                    else:  # backward-compatible fallback
                        delivered = all(
                            bool(telegram.send_trade_event(trade, event, current_price, evaluation.get("pnl_points", 0), evaluation))
                            for event in notification_events
                        )
                except Exception as exc:  # noqa: BLE001
                    self.logger.exception("Failed to send trade-management Telegram event(s) for %s: %s", trade_id, exc)
                    delivered = False
                evaluation["notification_delivered"] = delivered
                if not delivered:
                    self.logger.error(
                        "Mandatory trade update notification was not delivered for %s: %s",
                        trade_id,
                        ",".join(notification_events),
                    )

            if trade_id and database and evaluation.get("updates"):
                database.update_trade(trade_id, evaluation["updates"])
        return evaluations

    def evaluate_trade(
        self,
        trade: Dict[str, Any],
        current_price: float,
        now: datetime | None = None,
        candle_high: float | None = None,
        candle_low: float | None = None,
    ) -> Dict[str, Any]:
        """Return updates/events for a single trade without external side effects.

        ``current_price`` remains the displayed/latest close. If the caller
        provides the latest candle high/low, hard level checks use those extremes:
        BUY targets use high, BUY stops use low; SELL targets use low, SELL stops
        use high. This catches TP/SL touches within a 5-minute candle.
        """
        now = now or datetime.now(timezone.utc)
        trade_type = str(trade.get("type", "BUY")).upper()
        symbol = str(trade.get("symbol") or self.config.get("symbol", "XAU/USD"))
        old_status = str(trade.get("status", "OPEN")).upper()
        entry = self._f(trade.get("entry_price"))
        stop_loss = self._f(trade.get("stop_loss"))
        tp1 = self._f(trade.get("tp1"))
        tp2 = self._f(trade.get("tp2"))
        updates_sent = self._updates_sent(trade.get("updates_sent", []))
        sl_moved_to_entry = self._bool(trade.get("sl_moved_to_entry", False))
        partial_close = self._bool(trade.get("partial_close", False))
        previous_mfe = self._f(trade.get("max_favorable_excursion"), 0.0)
        previous_mae = self._f(trade.get("max_adverse_excursion"), 0.0)
        high_price = self._f(candle_high, current_price)
        low_price = self._f(candle_low, current_price)
        if high_price < low_price:
            high_price, low_price = low_price, high_price

        # ── PENDING (un-filled LIMIT/STOP) order handling ───────────────────
        # A pending order is NOT a live position: it has no PnL until price
        # actually touches the entry. Only then does it become OPEN. This fixes
        # phantom fills/profits where a far LIMIT was treated as already filled.
        if old_status == "PENDING":
            return self._evaluate_pending(
                trade,
                current_price,
                now,
                trade_type,
                entry,
                tp1,
                symbol,
                candle_high=high_price,
                candle_low=low_price,
            )

        pnl_points = calculate_pips(entry, current_price, trade_type, symbol)
        favorable_price = high_price if trade_type == "BUY" else low_price
        adverse_price = low_price if trade_type == "BUY" else high_price
        favorable_points = calculate_pips(entry, favorable_price, trade_type, symbol)
        adverse_points = calculate_pips(entry, adverse_price, trade_type, symbol)
        max_favorable_excursion = max(previous_mfe, pnl_points, favorable_points)
        max_adverse_excursion = min(previous_mae, pnl_points, adverse_points)
        management_phase = self._management_phase(old_status, sl_moved_to_entry, partial_close, pnl_points)
        exit_warning = self._exit_warning(trade_type, entry, stop_loss, tp1, current_price, pnl_points)
        new_status = old_status
        events: List[str] = []
        result: str | None = trade.get("result")
        close_price = None
        final_pnl = None
        new_stop_loss: float | None = None

        if old_status not in self.OPEN_STATUSES:
            return {
                "trade_id": trade.get("id"),
                "old_status": old_status,
                "new_status": old_status,
                "pnl_points": pnl_points,
                "events": [],
                "updates": {
                    "current_price": round(current_price, 2),
                    "current_pnl": round(pnl_points, 1),
                    "current_pnl_points": round(pnl_points, 1),
                    "max_favorable_excursion": round(max(previous_mfe, pnl_points), 1),
                    "max_adverse_excursion": round(min(previous_mae, pnl_points), 1),
                    "management_phase": self._management_phase(old_status, sl_moved_to_entry, partial_close, pnl_points),
                    "last_updated": self._iso(now),
                },
            }

        def _target_touched(level: float) -> bool:
            if level <= 0:
                return False
            return high_price >= level if trade_type == "BUY" else low_price <= level

        def _stop_touched(level: float) -> bool:
            if level <= 0:
                return False
            return low_price <= level if trade_type == "BUY" else high_price >= level

        def _breakeven_touched() -> bool:
            return low_price <= entry if trade_type == "BUY" else high_price >= entry

        tp2_touched = _target_touched(tp2)
        tp1_touched = _target_touched(tp1)
        sl_touched = _stop_touched(stop_loss)
        be_touched = _breakeven_touched()

        # If a trade is already protected (TP1/BE/trailing phase), use the
        # candle's favorable extreme to advance TP2/trailing BEFORE checking the
        # pullback stop. Example for SELL: if the 5m/15m candle first traded down
        # to 3943 and later rebounded to 3970, the trailing stop must be based on
        # the 3943 low (low + trailing distance), not on the closing price.
        # This fixes missed TP2/under-trailed exits when only close was used.
        protected_branch_handled = False
        protected_trade = bool(sl_moved_to_entry) and old_status in {"OPEN", "PARTIAL", "TP1_HIT"}
        if protected_trade:
            protected_branch_handled = True
            if tp2_touched:
                new_status = "TP2_HIT"
                events.append("TP2_HIT")
                result = "WIN"
                close_price = tp2
                final_pnl = calculate_pips(entry, tp2, trade_type, symbol)
            else:
                effective_stop = stop_loss
                if self.trailing_enabled and new_status in self.OPEN_STATUSES and "EXPIRED" not in events:
                    trailing_candidate = self._compute_trailing_stop(
                        trade_type,
                        favorable_price,
                        stop_loss,
                        entry,
                        symbol,
                    )
                    if trailing_candidate is not None:
                        new_stop_loss = trailing_candidate
                        effective_stop = trailing_candidate

                effective_sl_touched = _stop_touched(effective_stop)
                if self._beyond_breakeven(trade_type, effective_stop, entry) and effective_sl_touched:
                    new_status = "SL_HIT"
                    events.append("TRAILING_SL_HIT")
                    trailing_exit_pnl = calculate_pips(entry, effective_stop, trade_type, symbol)
                    result = "WIN" if trailing_exit_pnl > 0 else "BREAKEVEN"
                    close_price = effective_stop
                    final_pnl = round(trailing_exit_pnl, 1)
                elif effective_sl_touched and not self._beyond_breakeven(trade_type, effective_stop, entry):
                    new_status = "BE_HIT"
                    events.append("BE_HIT")
                    result = "BREAKEVEN"
                    close_price = entry
                    final_pnl = 0.0
                elif new_stop_loss is not None:
                    events.append("TRAILING_SL_UPDATED")

        # 1) Hard outcomes first using candle high/low when available.
        # Conservative ambiguity rule: if the same 5m candle touched both a
        # protective stop/breakeven and a target, close at the protective level.
        # OHLC data cannot prove which level was hit first, so this avoids
        # overstating paper-trading performance.
        if protected_branch_handled or new_status != old_status:
            pass
        elif (
            sl_moved_to_entry
            and self._beyond_breakeven(trade_type, stop_loss, entry)
            and sl_touched
        ):
            # The persisted stop_loss has been trailed past breakeven (see the
            # progressive-trailing branch below) and price has now pulled back
            # to it - this locks in the trailed profit rather than a plain
            # breakeven exit, and rather than the original far-away hard SL.
            # Applies whether the move-to-BE happened after TP1 or via the
            # early-breakeven mechanism while still OPEN.
            new_status = "SL_HIT"
            events.append("TRAILING_SL_HIT")
            trailing_exit_pnl = calculate_pips(entry, stop_loss, trade_type, symbol)
            result = "WIN" if trailing_exit_pnl > 0 else "BREAKEVEN"
            close_price = stop_loss
            final_pnl = round(trailing_exit_pnl, 1)
        elif sl_moved_to_entry and be_touched:
            new_status = "BE_HIT"
            events.append("BE_HIT")
            result = "BREAKEVEN"
            close_price = entry
            final_pnl = 0.0
        elif sl_touched:
            new_status = "SL_HIT"
            events.append("SL_HIT")
            result = "LOSS"
            close_price = stop_loss
            final_pnl = calculate_pips(entry, stop_loss, trade_type, symbol)
        elif tp2_touched:
            new_status = "TP2_HIT"
            events.append("TP2_HIT")
            result = "WIN"
            close_price = tp2
            final_pnl = calculate_pips(entry, tp2, trade_type, symbol)
        elif old_status == "OPEN" and tp1_touched:
            new_status = "TP1_HIT"
            events.append("TP1_HIT")
            partial_close = True
            if self.auto_be:
                sl_moved_to_entry = True
                new_stop_loss = entry  # actually persist breakeven, not just the flag
                events.append("MOVE_SL_TO_BE")
        else:
            # 2) Informational events only if no status-changing event happened.
            progress = self._progress_to_tp1(trade_type, entry, tp1, current_price)
            if old_status == "OPEN" and progress >= self.near_tp1_progress and "NEAR_TP1" not in updates_sent:
                events.append("NEAR_TP1")
            hours_open = self._hours_open(trade, now)
            if old_status == "OPEN" and hours_open >= self.time_warning_hours and "LONG_RUNNING" not in updates_sent:
                events.append("LONG_RUNNING")
            if exit_warning and "EXIT_WARNING" not in updates_sent:
                events.append("EXIT_WARNING")
            if self.expire_after_hours > 0 and old_status == "OPEN" and hours_open >= self.expire_after_hours:
                # Don't force-close a WINNING trade whose stop is already locked
                # at/above breakeven — let the (trailing) stop ride instead of
                # capping a runner by the clock. Only expire if it's not safely
                # protected in profit. Controlled by keep_protected_winners_open.
                protected_winner = (
                    self.keep_protected_winners_open
                    and sl_moved_to_entry
                    and self._beyond_breakeven_or_at(trade_type, stop_loss, entry)
                    and pnl_points > 0
                )
                if not protected_winner:
                    new_status = "EXPIRED"
                    events.append("EXPIRED")
                    result = "EXPIRED"
                    close_price = current_price
                    final_pnl = pnl_points

            # 2b) EARLY BREAKEVEN: once the trade is +N points in profit, move the
            # stop to entry WITHOUT waiting for TP1. Independent of partial close.
            if (
                self.early_breakeven_points > 0
                and not sl_moved_to_entry
                and new_status in self.OPEN_STATUSES
                and "EXPIRED" not in events
                and pnl_points >= self.early_breakeven_points
            ):
                sl_moved_to_entry = True
                new_stop_loss = entry  # persist the breakeven stop
                if "MOVE_SL_TO_BE" not in updates_sent:
                    events.append("MOVE_SL_TO_BE")

            # 3) Progressive trailing once breakeven is locked (either via TP1 or
            # via early breakeven above), and only when nothing status-changing
            # happened this run. Works while OPEN or TP1_HIT.
            if (
                self.trailing_enabled
                and sl_moved_to_entry
                and new_status in self.OPEN_STATUSES
                and "EXPIRED" not in events
            ):
                base_stop = new_stop_loss if new_stop_loss is not None else stop_loss
                # Use the favorable candle extreme, not only the close. For SELL,
                # trailing must follow the candle LOW; for BUY, the candle HIGH.
                trailing_candidate = self._compute_trailing_stop(trade_type, favorable_price, base_stop, entry, symbol)
                if trailing_candidate is not None:
                    new_stop_loss = trailing_candidate
                    if "TRAILING_SL_UPDATED" not in events:
                        events.append("TRAILING_SL_UPDATED")

        # Avoid repeating informational events already sent. Status events are naturally one-time after status changes.
        filtered_events: List[str] = []
        for event in events:
            if event in {"NEAR_TP1", "LONG_RUNNING", "MOVE_SL_TO_BE", "EXIT_WARNING"} and event in updates_sent:
                continue
            if event not in filtered_events:
                filtered_events.append(event)
        events = filtered_events
        updates_sent = self._append_updates_sent(updates_sent, events)

        updates: Dict[str, Any] = {
            "current_price": round(current_price, 2),
            "last_candle_high": round(high_price, 2),
            "last_candle_low": round(low_price, 2),
            "current_pnl": round(pnl_points, 1),
            "current_pnl_points": round(pnl_points, 1),
            "max_favorable_excursion": round(max_favorable_excursion, 1),
            "max_adverse_excursion": round(max_adverse_excursion, 1),
            "management_phase": management_phase,
            "exit_warning": exit_warning,
            "status": new_status,
            "sl_moved_to_entry": sl_moved_to_entry,
            "partial_close": partial_close,
            "updates_sent": updates_sent,
            "last_updated": self._iso(now),
        }
        if new_stop_loss is not None:
            updates["stop_loss"] = round(new_stop_loss, 2)
        if result is not None:
            updates["result"] = result
        if close_price is not None:
            updates["close_price"] = round(close_price, 2)
            updates["closed_at"] = self._iso(now)
            updates["close_time"] = self._iso(now)
        if final_pnl is not None:
            # Keep both names synchronized. Some report/dashboard code or older
            # schemas may read one or the other; stale current_pnl_points must not
            # override the final realized result after a trailing SL+ exit.
            updates["final_pnl"] = round(final_pnl, 1)
            updates["final_pnl_points"] = round(final_pnl, 1)

        return {
            "trade_id": trade.get("id"),
            "old_status": old_status,
            "new_status": new_status,
            "pnl_points": round(pnl_points, 1),
            "events": events,
            "updates": updates,
            "progress_to_tp1": round(self._progress_to_tp1(trade_type, entry, tp1, current_price), 3),
            "hours_open": round(self._hours_open(trade, now), 2),
            "max_favorable_excursion": round(max_favorable_excursion, 1),
            "max_adverse_excursion": round(max_adverse_excursion, 1),
            "management_phase": management_phase,
            "exit_warning": exit_warning,
        }

    def create_trade_record(self, decision: Dict[str, Any], trade_id: str | None = None) -> Dict[str, Any]:
        """Build a trade dict from a decision; useful for tests or JSON fallback."""
        signal = decision.get("signal", {}) or {}
        entry = signal.get("entry", {}) or {}
        entry_price = self._f(entry.get("price"), (self._f(entry.get("low")) + self._f(entry.get("high"))) / 2)
        now_iso = self._iso(datetime.now(timezone.utc))
        return {
            "id": trade_id or f"TRADE_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            "type": decision.get("decision", signal.get("type")),
            "entry_price": round(entry_price, 2),
            "entry_time": now_iso,
            "stop_loss": round(self._f(signal.get("stop_loss")), 2),
            "initial_stop_loss": round(self._f(signal.get("stop_loss")), 2),
            "tp1": round(self._f(signal.get("tp1")), 2),
            "tp2": round(self._f(signal.get("tp2")), 2),
            "status": "OPEN",
            "current_price": round(self._f(decision.get("current_price"), entry_price), 2),
            "current_pnl": 0,
            "current_pnl_points": 0,
            "max_favorable_excursion": 0,
            "max_adverse_excursion": 0,
            "management_phase": "INITIAL",
            "exit_warning": None,
            "sl_moved_to_entry": False,
            "partial_close": False,
            "updates_sent": [],
            "result": None,
            "created_at": now_iso,
            "close_time": None,
            "close_price": None,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Compatibility wrapper for the BaseAgent-style interface."""
        trades = data.get("open_trades", [])
        current_price = self._f(data.get("current_price"))
        evaluations = [self.evaluate_trade(trade, current_price) for trade in trades]
        return {"agent": self.name, "evaluated": len(evaluations), "results": evaluations, "summary": f"Evaluated {len(evaluations)} open trade(s)"}

    def _management_phase(self, status: str, sl_moved_to_entry: bool, partial_close: bool, pnl_points: float) -> str:
        if status == "TP1_HIT" or partial_close:
            return "POST_TP1_TRAILING" if sl_moved_to_entry else "POST_TP1"
        if pnl_points > 0:
            return "IN_PROFIT"
        if pnl_points < 0:
            return "DEFENSIVE"
        return "INITIAL"

    def _exit_warning(self, trade_type: str, entry: float, stop_loss: float, tp1: float, current_price: float, pnl_points: float) -> str | None:
        if not stop_loss or not entry:
            return None
        risk = abs(entry - stop_loss)
        if risk <= 0:
            return None
        adverse_distance = abs(current_price - stop_loss)
        if adverse_distance <= risk * 0.25:
            return "NEAR_STOP_LOSS"
        if pnl_points < -risk * 0.65:
            return "ADVERSE_MOVE_DEEP"
        # If trade moved more than halfway to TP1 then returned close to entry.
        if tp1 and abs(current_price - entry) <= risk * 0.15:
            return None
        return None

    def _order_filled(
        self,
        order_type: str,
        trade_type: str,
        entry: float,
        current_price: float,
        candle_high: float | None = None,
        candle_low: float | None = None,
    ) -> bool:
        """True when a pending LIMIT/STOP order would fill by candle touch.

        LIMIT: price returns to a better level than market at signal time.
          BUY_LIMIT  fills when low falls to/through entry   (low <= entry)
          SELL_LIMIT fills when high rises to/through entry  (high >= entry)
        STOP: price breaks beyond entry in the trade direction.
          BUY_STOP   fills when high rises to/through entry  (high >= entry)
          SELL_STOP  fills when low falls to/through entry   (low <= entry)
        Falls back to current_price when high/low are not provided.
        """
        high = self._f(candle_high, current_price)
        low = self._f(candle_low, current_price)
        if high < low:
            high, low = low, high
        ot = str(order_type or "").upper()
        if not ot or ot.endswith("MARKET"):
            return True
        if ot == "BUY_LIMIT":
            return low <= entry
        if ot == "SELL_LIMIT":
            return high >= entry
        if ot == "BUY_STOP":
            return high >= entry
        if ot == "SELL_STOP":
            return low <= entry
        # Unknown -> infer from kind via trade direction (treat as LIMIT pullback).
        if trade_type == "BUY":
            return low <= entry
        return high >= entry

    def _evaluate_pending(
        self,
        trade,
        current_price,
        now,
        trade_type,
        entry,
        tp1,
        symbol,
        candle_high: float | None = None,
        candle_low: float | None = None,
    ):
        """Activate a pending order on touch, else keep it waiting (no PnL).

        Cancellation of stale pending orders is handled by the signal pipeline
        (a new signal replaces them); here we only fill-on-touch and refresh
        the displayed market price.

        Hybrid mode: if pending_order_max_cycles is exceeded (order survives
        too long without filling), auto-convert to MARKET at current price.
        This prevents LIMIT/STOP orders from waiting forever when the pullback
        never materialises.
        """
        order_type = str(trade.get("order_type") or trade.get("order_kind") or "").upper()
        high_price = self._f(candle_high, current_price)
        low_price = self._f(candle_low, current_price)
        if high_price < low_price:
            high_price, low_price = low_price, high_price
        filled = self._order_filled(order_type, trade_type, entry, current_price, high_price, low_price)
        base_updates = {
            "current_price": round(current_price, 2),
            "last_candle_high": round(high_price, 2),
            "last_candle_low": round(low_price, 2),
            "last_updated": self._iso(now),
        }

        # Hybrid mode: auto-convert stale PENDING to MARKET
        if not filled and self.entry_style == "hybrid" and self.pending_order_max_cycles > 0:
            pending_cycles = self._f(trade.get("pending_cycles", 0))
            pending_cycles += 1
            if pending_cycles >= self.pending_order_max_cycles:
                # Auto-convert: enter at current market price
                base_updates.update({
                    "status": "OPEN",
                    "entry_time": self._iso(now),
                    "entry_price": round(current_price, 2),
                    "current_pnl": 0,
                    "current_pnl_points": 0,
                    "pending_cycles": 0,
                })
                return {
                    "trade_id": trade.get("id"),
                    "old_status": "PENDING",
                    "new_status": "OPEN",
                    "pnl_points": 0.0,
                    "events": ["ORDER_FILLED"],
                    "updates": base_updates,
                    "progress_to_tp1": 0.0,
                    "hours_open": 0.0,
                }
            # Still waiting - increment pending_cycles
            base_updates["pending_cycles"] = pending_cycles

        if filled:
            # Fill at the configured entry price (paper). Position becomes live.
            base_updates.update({
                "status": "OPEN",
                "entry_time": self._iso(now),  # clock starts at fill, not at signal
                "current_pnl": 0,
                "current_pnl_points": 0,
            })
            return {
                "trade_id": trade.get("id"),
                "old_status": "PENDING",
                "new_status": "OPEN",
                "pnl_points": 0.0,
                "events": ["ORDER_FILLED"],
                "updates": base_updates,
                "progress_to_tp1": 0.0,
                "hours_open": 0.0,
            }
        # Fixed-risk auto-convert: if price has reached within budget, open MARKET
        if self.entry_style == "fixed_risk":
            # recalc: check if price is now within risk budget from nearest level
            pass  # Handled by the next analysis cycle via decision_agent

        # Still waiting — report distance to entry, no PnL.
        dist_pts = abs(calculate_pips(current_price, entry, trade_type, symbol))
        return {
            "trade_id": trade.get("id"),
            "old_status": "PENDING",
            "new_status": "PENDING",
            "pnl_points": 0.0,
            "events": [],
            "updates": base_updates,
            "progress_to_tp1": 0.0,
            "hours_open": self._hours_open(trade, now),
            "pending_distance_points": round(dist_pts, 1),
        }

    def _hit_tp1(self, trade_type: str, current_price: float, tp1: float) -> bool:
        if tp1 <= 0:
            return False
        return current_price >= tp1 if trade_type == "BUY" else current_price <= tp1

    def _hit_tp2(self, trade_type: str, current_price: float, tp2: float) -> bool:
        if tp2 <= 0:
            return False
        return current_price >= tp2 if trade_type == "BUY" else current_price <= tp2

    def _hit_sl(self, trade_type: str, current_price: float, stop_loss: float) -> bool:
        if stop_loss <= 0:
            return False
        return current_price <= stop_loss if trade_type == "BUY" else current_price >= stop_loss

    def _hit_break_even(self, trade_type: str, current_price: float, entry: float) -> bool:
        return current_price <= entry if trade_type == "BUY" else current_price >= entry

    def _beyond_breakeven(self, trade_type: str, stop_loss: float, entry: float) -> bool:
        """True once the persisted stop_loss has been trailed past pure breakeven
        (i.e. progressive trailing has actually locked in extra profit, not just
        the initial entry-level break-even move)."""
        epsilon = 1e-6
        if trade_type == "BUY":
            return stop_loss > entry + epsilon
        return stop_loss < entry - epsilon

    def _beyond_breakeven_or_at(self, trade_type: str, stop_loss: float, entry: float) -> bool:
        """True when the stop is at entry (breakeven) or better — i.e. the trade
        can no longer turn into a loss. Used to decide whether a time-expired
        winner is safe to keep open under its protective stop."""
        epsilon = 1e-6
        if trade_type == "BUY":
            return stop_loss >= entry - epsilon
        return stop_loss <= entry + epsilon

    def _compute_trailing_stop(
        self, trade_type: str, current_price: float, current_stop_loss: float, entry: float, symbol: str | None = None
    ) -> float | None:
        """Progressive trailing stop, only ever moving in the profitable direction.

        trailing_distance/trailing_step/min_profit_lock are configured in
        points (matching calculate_pips' convention: 10 points = $1.0 on
        XAU/USD), so they're converted to price units here before use.

        Only returns a new value once price has moved favorably by at least
        trailing_step beyond the current stop_loss, to avoid near-constant
        tiny updates every run. Never moves the stop below the configured
        min_profit_lock above/below entry.
        """
        distance = points_to_price(self.trailing_distance, symbol)
        step = points_to_price(self.trailing_step, symbol)
        min_lock = points_to_price(self.trailing_min_profit_lock, symbol)
        epsilon = 1e-9
        if trade_type == "BUY":
            candidate = current_price - distance
            candidate = max(candidate, entry + min_lock)
            # Move exactly on the configured step too: +30 pts should move 30 pts,
            # not require +31 due to a strict > comparison.
            if candidate >= current_stop_loss + step - epsilon:
                return candidate
        else:
            candidate = current_price + distance
            candidate = min(candidate, entry - min_lock)
            if candidate <= current_stop_loss - step + epsilon:
                return candidate
        return None

    def _progress_to_tp1(self, trade_type: str, entry: float, tp1: float, current_price: float) -> float:
        target_distance = abs(tp1 - entry)
        if target_distance <= 0:
            return 0.0
        favorable_move = (current_price - entry) if trade_type == "BUY" else (entry - current_price)
        return max(0.0, favorable_move / target_distance)

    def _hours_open(self, trade: Dict[str, Any], now: datetime) -> float:
        opened = self._parse_dt(str(trade.get("entry_time") or trade.get("created_at") or ""))
        if opened is None:
            return 0.0
        return max(0.0, (now - opened).total_seconds() / 3600)

    def _updates_sent(self, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except json.JSONDecodeError:
                return [value] if value else []
        return []

    def _append_updates_sent(self, updates_sent: List[str], events: List[str]) -> List[str]:
        result = list(updates_sent)
        for event in events:
            if event not in result:
                result.append(event)
        return result

    def _parse_dt(self, value: str) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _iso(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

    def _bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(value)

    def _f(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
