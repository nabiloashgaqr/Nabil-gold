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
        self.auto_be = bool(self.management.get("auto_move_sl_to_entry_after_tp1", True))

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
                for event in evaluation.get("events", []):
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

        pnl_points = calculate_pips(entry, current_price, trade_type)
        new_status = old_status
        events: List[str] = []
        result: str | None = trade.get("result")
        close_price = None
        final_pnl = None

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
        elif sl_moved_to_entry and old_status == "TP1_HIT" and self._hit_break_even(trade_type, current_price, entry):
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
                events.append("MOVE_SL_TO_BE")
        else:
            # 2) Informational events only if no status-changing event happened.
            progress = self._progress_to_tp1(trade_type, entry, tp1, current_price)
            if old_status == "OPEN" and progress >= self.near_tp1_progress and "NEAR_TP1" not in updates_sent:
                events.append("NEAR_TP1")
            hours_open = self._hours_open(trade, now)
            if old_status == "OPEN" and hours_open >= self.time_warning_hours and "LONG_RUNNING" not in updates_sent:
                events.append("LONG_RUNNING")
            if self.expire_after_hours > 0 and old_status == "OPEN" and hours_open >= self.expire_after_hours:
                new_status = "EXPIRED"
                events.append("EXPIRED")
                result = "EXPIRED"
                close_price = current_price
                final_pnl = pnl_points

        # Avoid repeating informational events already sent. Status events are naturally one-time after status changes.
        filtered_events: List[str] = []
        for event in events:
            if event in {"NEAR_TP1", "LONG_RUNNING", "MOVE_SL_TO_BE"} and event in updates_sent:
                continue
            if event not in filtered_events:
                filtered_events.append(event)
        events = filtered_events
        updates_sent = self._append_updates_sent(updates_sent, events)

        updates: Dict[str, Any] = {
            "current_price": round(current_price, 2),
            "current_pnl": round(pnl_points, 1),
            "current_pnl_points": round(pnl_points, 1),
            "status": new_status,
            "sl_moved_to_entry": sl_moved_to_entry,
            "partial_close": partial_close,
            "updates_sent": updates_sent,
            "last_updated": self._iso(now),
        }
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
        return {"agent": self.name, "evaluated": len(evaluations), "results": evaluations, "summary": f"تم تقييم {len(evaluations)} صفقة مفتوحة"}

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
