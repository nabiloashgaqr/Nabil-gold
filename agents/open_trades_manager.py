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


class OpenTradesManager(BaseAgent):
    """Evaluate and update open trades for the stateless GitHub Actions runner."""

    name = "open_trades_manager"
    OPEN_STATUSES = {"OPEN", "TP1_HIT"}
    CLOSED_STATUSES = {"TP2_HIT", "SL_HIT", "BE_HIT", "EXPIRED", "MANUAL_CLOSE"}

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
        self.trailing_distance = float(ts_config.get("trailing_distance", 20.0))
        self.trailing_step = float(ts_config.get("trailing_step", 5.0))
        self.trailing_min_profit_lock = float(ts_config.get("min_profit_lock", 0.0))

        # Early breakeven: move SL to entry once the trade is +N points in
        # profit, WITHOUT waiting for TP1. 0/absent disables it (legacy
        # behaviour = breakeven only after TP1). Points convention: 10 pts = $1.
        self.early_breakeven_points = float(ts_config.get("early_breakeven_points", 0.0))

    def update_trades(
        self,
        open_trades: List[Dict[str, Any]],
        current_price: float,
        database: Any | None = None,
        telegram: Any | None = None,
        now: datetime | None = None,
    ) -> List[Dict[str, Any]]:
        """Evaluate all open trades, persist updates and send Telegram events."""
        evaluations: List[Dict[str, Any]] = []
        now = now or datetime.now(timezone.utc)
        for trade in open_trades:
            evaluation = self.evaluate_trade(trade, current_price, now=now)
            evaluations.append(evaluation)
            trade_id = str(trade.get("id", ""))
            if trade_id and database and evaluation.get("updates"):
                database.update_trade(trade_id, evaluation["updates"])
            if telegram:
                events = evaluation.get("events", []) or []
                if events:
                    # Send ONE combined message per trade per cycle instead of a
                    # separate message per event (avoids duplicate near-identical
                    # messages when e.g. LONG_RUNNING + EXIT_WARNING fire together).
                    if hasattr(telegram, "send_trade_events"):
                        telegram.send_trade_events(trade, events, current_price, evaluation.get("pnl_points", 0), evaluation)
                    else:  # backward-compatible fallback
                        for event in events:
                            telegram.send_trade_event(trade, event, current_price, evaluation.get("pnl_points", 0), evaluation)
        return evaluations

    def evaluate_trade(self, trade: Dict[str, Any], current_price: float, now: datetime | None = None) -> Dict[str, Any]:
        """Return updates/events for a single trade without external side effects."""
        now = now or datetime.now(timezone.utc)
        trade_type = str(trade.get("type", "BUY")).upper()
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

        pnl_points = calculate_pips(entry, current_price, trade_type)
        max_favorable_excursion = max(previous_mfe, pnl_points)
        max_adverse_excursion = min(previous_mae, pnl_points)
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

        # 1) Hard outcomes first: TP2, BE/SL. TP2 has priority if price is already beyond full target.
        if self._hit_tp2(trade_type, current_price, tp2):
            new_status = "TP2_HIT"
            events.append("TP2_HIT")
            result = "WIN"
            close_price = current_price
            final_pnl = pnl_points
        elif (
            sl_moved_to_entry
            and self._beyond_breakeven(trade_type, stop_loss, entry)
            and self._hit_sl(trade_type, current_price, stop_loss)
        ):
            # The persisted stop_loss has been trailed past breakeven (see the
            # progressive-trailing branch below) and price has now pulled back
            # to it - this locks in the trailed profit rather than a plain
            # breakeven exit, and rather than the original far-away hard SL.
            # Applies whether the move-to-BE happened after TP1 or via the
            # early-breakeven mechanism while still OPEN.
            new_status = "SL_HIT"
            events.append("TRAILING_SL_HIT")
            trailing_exit_pnl = calculate_pips(entry, stop_loss, trade_type)
            result = "WIN" if trailing_exit_pnl > 0 else "BREAKEVEN"
            close_price = stop_loss
            final_pnl = round(trailing_exit_pnl, 1)
        elif sl_moved_to_entry and self._hit_break_even(trade_type, current_price, entry):
            new_status = "BE_HIT"
            events.append("BE_HIT")
            result = "BREAKEVEN"
            close_price = entry
            final_pnl = 0.0
        elif self._hit_sl(trade_type, current_price, stop_loss):
            new_status = "SL_HIT"
            events.append("SL_HIT")
            result = "LOSS"
            close_price = current_price
            final_pnl = pnl_points
        elif old_status == "OPEN" and self._hit_tp1(trade_type, current_price, tp1):
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
                trailing_candidate = self._compute_trailing_stop(trade_type, current_price, base_stop, entry)
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
            updates["final_pnl"] = round(final_pnl, 1)

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
        self, trade_type: str, current_price: float, current_stop_loss: float, entry: float
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
        distance = self.trailing_distance / 10.0
        step = self.trailing_step / 10.0
        min_lock = self.trailing_min_profit_lock / 10.0
        if trade_type == "BUY":
            candidate = current_price - distance
            candidate = max(candidate, entry + min_lock)
            if candidate > current_stop_loss + step:
                return candidate
        else:
            candidate = current_price + distance
            candidate = min(candidate, entry - min_lock)
            if candidate < current_stop_loss - step:
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
