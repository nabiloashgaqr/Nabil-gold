"""Persistent setup-memory service.

Sprint 2 foundation: turns structured setup candidates into a lightweight state
machine persisted across stateless analysis cycles.

State ladder:
    DETECTED -> SWEEP_CONFIRMED -> POI_MARKED -> ENTRY_ARMED -> ENTRY_TRIGGERED
    INVALIDATED / EXPIRED are terminal.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from utils.instruments import points_to_price

logger = logging.getLogger(__name__)


class SetupMemoryService:
    STATE_ORDER = {
        "DETECTED": 0,
        "SWEEP_CONFIRMED": 1,
        "POI_MARKED": 2,
        "ENTRY_ARMED": 3,
        "ENTRY_TRIGGERED": 4,
        "INVALIDATED": 5,
        "EXPIRED": 5,
    }
    TERMINAL_STATES = {"ENTRY_TRIGGERED", "INVALIDATED", "EXPIRED"}

    def __init__(self, database, config: Dict[str, Any] | None = None) -> None:
        self.db = database
        self.config = config or {}
        sm = self.config.get("setup_memory", {}) or {}
        self.enabled = bool(sm.get("enabled", True))
        self.arm_zone_buffer_points = float(sm.get("arm_zone_buffer_points", 40) or 40)
        self.invalidate_buffer_points = float(sm.get("invalidate_buffer_points", 20) or 20)
        self.missing_cycles_before_expire = int(sm.get("missing_cycles_before_expire", 6) or 6)
        self.expire_after_hours = float(sm.get("expire_after_hours", 12) or 12)
        self.symbol = str(self.config.get("symbol", "XAU/USD"))

    def process_candidates(
        self,
        candidates: List[Dict[str, Any]],
        current_price: float,
        symbol: str | None = None,
    ) -> List[Dict[str, Any]]:
        """Merge current-cycle candidates with persisted state and emit transitions."""
        if not self.enabled:
            return candidates
        symbol = str(symbol or self.symbol)
        previous_rows = self.db.get_active_setup_candidates(symbol=symbol)
        previous_by_key = {str(row.get("state_key") or row.get("id")): row for row in previous_rows}
        seen_keys: set[str] = set()
        processed: List[Dict[str, Any]] = []
        for candidate in candidates:
            state_key = str(candidate.get("state_key") or candidate.get("id") or "")
            if not state_key:
                continue
            seen_keys.add(state_key)
            previous = previous_by_key.get(state_key)
            merged = self._merge_candidate(previous, candidate, current_price)
            self.db.save_setup_candidate(merged)
            if previous is None or str(previous.get("setup_state") or "") != str(merged.get("setup_state") or ""):
                self._record_transition(previous, merged, current_price)
            processed.append(merged)

        # Expire / invalidate previously active setups that disappeared this cycle.
        for state_key, previous in previous_by_key.items():
            if state_key in seen_keys:
                continue
            updated = self._handle_missing_candidate(previous, current_price)
            if not updated:
                continue
            self.db.save_setup_candidate(updated)
            if str(updated.get("setup_state") or "") != str(previous.get("setup_state") or ""):
                self._record_transition(previous, updated, current_price)
        return processed

    def mark_entry_triggered(
        self,
        setup_id: str | None,
        state_key: str | None,
        trade_id: str,
        current_price: float | None = None,
        symbol: str | None = None,
    ) -> None:
        """Move a setup into ENTRY_TRIGGERED after a trade is committed."""
        if not self.enabled:
            return
        symbol = str(symbol or self.symbol)
        rows = self.db.get_recent_setup_candidates(limit=50, symbol=symbol)
        target = None
        for row in rows:
            if setup_id and str(row.get("id")) == str(setup_id):
                target = row
                break
            if state_key and str(row.get("state_key") or "") == str(state_key):
                target = row
                break
        if not target:
            return
        old_state = str(target.get("setup_state") or "DETECTED")
        if old_state == "ENTRY_TRIGGERED":
            return
        target = dict(target)
        target.update(
            {
                "setup_state": "ENTRY_TRIGGERED",
                "is_active": False,
                "last_trade_id": trade_id,
                "last_transition_at": self._now_iso(),
                "updated_at": self._now_iso(),
            }
        )
        self.db.save_setup_candidate(target)
        self._record_transition({**target, "setup_state": old_state}, target, current_price or float(target.get("entry_price") or 0))

    def _merge_candidate(self, previous: Dict[str, Any] | None, candidate: Dict[str, Any], current_price: float) -> Dict[str, Any]:
        merged = dict(previous or {})
        merged.update(candidate)
        if previous and previous.get("id"):
            merged["id"] = previous.get("id")
        zone = candidate.get("poi_zone") or candidate.get("zone") or previous.get("poi_zone") if previous else candidate.get("poi_zone")
        if zone and "poi_zone" not in merged:
            merged["poi_zone"] = zone
        desired_state = self._desired_state(candidate, current_price)
        previous_state = str((previous or {}).get("setup_state") or "")
        if previous_state in self.TERMINAL_STATES:
            final_state = previous_state
        else:
            final_state = self._max_state(previous_state or "DETECTED", desired_state)
        if self._is_invalidated(merged, current_price):
            final_state = "INVALIDATED"
            merged["is_active"] = False
        else:
            merged["is_active"] = final_state not in self.TERMINAL_STATES
        merged["setup_state"] = final_state
        merged["symbol"] = str(merged.get("symbol") or self.symbol)
        merged["state_key"] = str(merged.get("state_key") or merged.get("id"))
        merged["missing_cycles"] = 0
        if previous:
            merged["first_seen_at"] = previous.get("first_seen_at") or candidate.get("first_seen_at") or candidate.get("created_at") or self._now_iso()
            prev_transitions = int(previous.get("transition_count", 0) or 0)
            if previous_state != final_state:
                merged["transition_count"] = prev_transitions + 1
                merged["last_transition_at"] = self._now_iso()
            else:
                merged["transition_count"] = prev_transitions
                merged["last_transition_at"] = previous.get("last_transition_at") or previous.get("updated_at") or self._now_iso()
        else:
            merged["first_seen_at"] = candidate.get("first_seen_at") or candidate.get("created_at") or self._now_iso()
            merged["transition_count"] = 0
            merged["last_transition_at"] = self._now_iso()
        merged["last_seen_at"] = self._now_iso()
        return merged

    def _handle_missing_candidate(self, previous: Dict[str, Any], current_price: float) -> Dict[str, Any] | None:
        state = str(previous.get("setup_state") or "DETECTED")
        if state in self.TERMINAL_STATES:
            return None
        updated = dict(previous)
        updated["missing_cycles"] = int(previous.get("missing_cycles", 0) or 0) + 1
        updated["last_seen_at"] = self._now_iso()
        if self._is_invalidated(updated, current_price):
            updated["setup_state"] = "INVALIDATED"
            updated["is_active"] = False
            updated["last_transition_at"] = self._now_iso()
            updated["transition_count"] = int(previous.get("transition_count", 0) or 0) + 1
            return updated
        if self._is_expired(updated):
            updated["setup_state"] = "EXPIRED"
            updated["is_active"] = False
            updated["last_transition_at"] = self._now_iso()
            updated["transition_count"] = int(previous.get("transition_count", 0) or 0) + 1
            return updated
        return updated

    def _desired_state(self, candidate: Dict[str, Any], current_price: float) -> str:
        explicit = str(candidate.get("setup_state") or "").upper()
        if explicit in self.TERMINAL_STATES:
            return explicit
        sweep_side = str(candidate.get("sweep_side") or "")
        poi_type = str(candidate.get("poi_type") or "")
        in_arm_zone = self._near_poi(candidate, current_price)
        state = "DETECTED"
        if sweep_side:
            state = "SWEEP_CONFIRMED"
        if poi_type:
            state = self._max_state(state, "POI_MARKED")
        if in_arm_zone or explicit == "ENTRY_ARMED":
            state = self._max_state(state, "ENTRY_ARMED")
        return state

    def _near_poi(self, candidate: Dict[str, Any], current_price: float) -> bool:
        low = self._f(candidate.get("poi_low"))
        high = self._f(candidate.get("poi_high"))
        if low <= 0 and high <= 0:
            zone = candidate.get("poi_zone") or {}
            low = self._f(zone.get("bottom"))
            high = self._f(zone.get("top"))
        if low <= 0 or high <= 0:
            return False
        low, high = min(low, high), max(low, high)
        buffer = points_to_price(self.arm_zone_buffer_points, candidate.get("symbol") or self.symbol)
        return low - buffer <= current_price <= high + buffer

    def _is_invalidated(self, candidate: Dict[str, Any], current_price: float) -> bool:
        direction = str(candidate.get("direction") or "").upper()
        stop = self._f(candidate.get("stop_loss"))
        if stop <= 0 or direction not in {"BUY", "SELL"}:
            return False
        buffer = points_to_price(self.invalidate_buffer_points, candidate.get("symbol") or self.symbol)
        if direction == "BUY":
            return current_price <= stop - buffer
        return current_price >= stop + buffer

    def _is_expired(self, candidate: Dict[str, Any]) -> bool:
        missing_cycles = int(candidate.get("missing_cycles", 0) or 0)
        if missing_cycles >= self.missing_cycles_before_expire:
            return True
        first_seen = self._parse_dt(candidate.get("first_seen_at"))
        if not first_seen:
            return False
        hours = (datetime.now(timezone.utc) - first_seen).total_seconds() / 3600.0
        return hours >= self.expire_after_hours

    def _record_transition(self, previous: Dict[str, Any] | None, current: Dict[str, Any], current_price: float) -> None:
        from_state = str((previous or {}).get("setup_state") or "") or None
        to_state = str(current.get("setup_state") or "") or None
        if not to_state or from_state == to_state:
            return
        reason = self._transition_reason(from_state, to_state, current)
        try:
            self.db.save_setup_state_event(
                {
                    "setup_id": current.get("id"),
                    "state_key": current.get("state_key"),
                    "symbol": current.get("symbol") or self.symbol,
                    "from_state": from_state,
                    "to_state": to_state,
                    "reason": reason,
                    "price": round(float(current_price or 0), 2) if current_price else None,
                    "payload": {
                        "setup_type": current.get("setup_type"),
                        "poi_type": current.get("poi_type"),
                        "lead_agent": current.get("lead_agent"),
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to record setup transition %s -> %s for %s: %s", from_state, to_state, current.get("id"), exc)

    def _transition_reason(self, from_state: str | None, to_state: str, current: Dict[str, Any]) -> str:
        reasons = {
            "SWEEP_CONFIRMED": "liquidity_sweep_detected",
            "POI_MARKED": "poi_identified",
            "ENTRY_ARMED": "price_near_poi",
            "ENTRY_TRIGGERED": "trade_committed",
            "INVALIDATED": "price_breached_invalidation",
            "EXPIRED": "setup_stale_or_missing",
        }
        return reasons.get(to_state, f"state_{str(from_state or 'none').lower()}_to_{str(to_state).lower()}")

    def _max_state(self, a: str, b: str) -> str:
        return a if self.STATE_ORDER.get(str(a).upper(), -1) >= self.STATE_ORDER.get(str(b).upper(), -1) else b

    def _parse_dt(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            text = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _f(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
