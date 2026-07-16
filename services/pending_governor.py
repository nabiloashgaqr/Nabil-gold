"""Pending thesis governor.

Stage 2 of the pending-thesis model:
- keep the current pending thesis if it still dominates
- replace it when a clearly stronger thesis appears
- cancel weak/stale pending theses instead of stacking same-direction orders
- guard against same-zone re-entry / replacement unless the thesis is
  materially new (new POI, stronger setup state, fresh sweep/displacement)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from utils.instruments import price_to_points


class PendingGovernor:
    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        cfg = (self.config.get("pending_governor") or {}) if isinstance(self.config, dict) else {}
        self.enabled = bool(cfg.get("enabled", True))
        self.replace_min_dominance_delta = float(cfg.get("replace_min_dominance_delta", 8) or 8)
        self.keep_if_return_probability_above = float(cfg.get("keep_if_return_probability_above", 45) or 45)
        self.cancel_if_return_probability_below = float(cfg.get("cancel_if_return_probability_below", 25) or 25)
        self.require_quality_improvement = bool(cfg.get("require_quality_improvement", True))

        dup = (self.config.get("duplicate_signal_filter") or {}) if isinstance(self.config, dict) else {}
        cooldown = (dup.get("cooldown") or {}) if isinstance(dup, dict) else {}
        self.price_zone_points = float(dup.get("price_zone_points", dup.get("same_direction_price_zone_points", 50)) or 50)
        self.lookback_hours = float(cooldown.get("lookback_hours", 6) or 6)
        legacy_cooldown = float(dup.get("lookback_minutes", 90) or 90)
        self.cooldown_after_loss = float(cooldown.get("after_loss_minutes", legacy_cooldown) or legacy_cooldown)
        self.cooldown_after_breakeven = float(cooldown.get("after_breakeven_minutes", max(legacy_cooldown * 0.5, 30)) or max(legacy_cooldown * 0.5, 30))
        self.cooldown_after_win = float(cooldown.get("after_win_minutes", max(legacy_cooldown * 0.33, 20)) or max(legacy_cooldown * 0.33, 20))

        reval = (self.config.get("post_exit_revalidation") or {}) if isinstance(self.config, dict) else {}
        self.post_exit_enabled = bool(reval.get("enabled", True))
        self.new_poi_min_distance_points = float(reval.get("new_poi_min_distance_points", 80) or 80)
        self.min_state_progress_steps = int(reval.get("min_state_progress_steps", 1) or 1)
        self.min_trigger_score_improvement = float(reval.get("min_trigger_score_improvement", 8) or 8)
        self.min_displacement_improvement = float(reval.get("min_displacement_improvement", 5) or 5)
        self.min_dominance_improvement = float(reval.get("min_dominance_improvement", 6) or 6)

    def review(
        self,
        decision: Dict[str, Any],
        open_trades: List[Dict[str, Any]],
        database: Any | None = None,
    ) -> Dict[str, Any]:
        side = str(decision.get("decision") or "").upper()
        if not self.enabled or side not in {"BUY", "SELL"}:
            return {"action": "ALLOW_NEW", "reason": None, "cancelled_ids": []}

        symbol = str(decision.get("symbol") or self.config.get("symbol", "XAU/USD")).upper()
        pending_same_direction = [
            t for t in (open_trades or [])
            if str(t.get("status") or "").upper() == "PENDING"
            and str(t.get("symbol") or "").upper() == symbol
            and str(t.get("type") or t.get("side") or "").upper() == side
        ]
        if not pending_same_direction:
            return {"action": "ALLOW_NEW", "reason": None, "cancelled_ids": []}

        new_ctx = self._setup_context_from_decision(decision)
        if not new_ctx:
            return {"action": "KEEP_EXISTING_PENDING", "reason": "pending thesis already exists and the new signal has no richer setup context", "cancelled_ids": []}

        primary_pending = max(pending_same_direction, key=self._pending_priority)
        old_ctx = self._setup_context_from_trade(primary_pending)

        old_dom = float(old_ctx.get("thesis_dominance_score") or 0)
        new_dom = float(new_ctx.get("thesis_dominance_score") or 0)
        old_rp = float(old_ctx.get("return_probability_score") or 0)
        new_rp = float(new_ctx.get("return_probability_score") or 0)
        old_q = float(old_ctx.get("poi_quality_score") or 0)
        new_q = float(new_ctx.get("poi_quality_score") or 0)

        quality_improved = (new_q > old_q) or (new_rp > old_rp)
        dominance_gap = new_dom - old_dom
        materially_new = self.materially_new_thesis(new_ctx, old_ctx, symbol=symbol)

        if dominance_gap >= self.replace_min_dominance_delta and (quality_improved or not self.require_quality_improvement):
            if not materially_new.get("allow"):
                return {
                    "action": "KEEP_EXISTING_PENDING",
                    "reason": f"replacement blocked: {materially_new.get('reason')}",
                    "cancelled_ids": [],
                    "old_trade_id": primary_pending.get("id"),
                    "old_context": old_ctx,
                    "new_context": new_ctx,
                }
            cancelled = self._cancel_pending_group(
                pending_same_direction,
                database=database,
                reason=(
                    f"Replaced by stronger pending thesis. old_dom={old_dom:.1f}, new_dom={new_dom:.1f}, "
                    f"old_rp={old_rp:.1f}, new_rp={new_rp:.1f}, old_q={old_q:.1f}, new_q={new_q:.1f}"
                ),
            )
            return {
                "action": "REPLACE_PENDING",
                "reason": "new thesis dominates the existing pending order",
                "cancelled_ids": cancelled,
                "old_trade_id": primary_pending.get("id"),
                "old_context": old_ctx,
                "new_context": new_ctx,
            }

        if old_rp <= self.cancel_if_return_probability_below and new_dom <= old_dom:
            cancelled = self._cancel_pending_group(
                pending_same_direction,
                database=database,
                reason=(
                    f"Cancelled stale pending thesis. old_rp={old_rp:.1f} below {self.cancel_if_return_probability_below:.1f}"
                ),
            )
            return {
                "action": "CANCEL_PENDING_ALLOW_NEW",
                "reason": "existing pending return probability collapsed",
                "cancelled_ids": cancelled,
                "old_trade_id": primary_pending.get("id"),
                "old_context": old_ctx,
                "new_context": new_ctx,
            }

        if old_rp >= self.keep_if_return_probability_above and old_dom >= new_dom:
            return {
                "action": "KEEP_EXISTING_PENDING",
                "reason": (
                    f"existing pending thesis still dominates (old_dom={old_dom:.1f}, new_dom={new_dom:.1f}, old_rp={old_rp:.1f})"
                ),
                "cancelled_ids": [],
                "old_trade_id": primary_pending.get("id"),
                "old_context": old_ctx,
                "new_context": new_ctx,
            }

        return {
            "action": "KEEP_EXISTING_PENDING",
            "reason": "existing pending thesis remains acceptable; no replacement triggered",
            "cancelled_ids": [],
            "old_trade_id": primary_pending.get("id"),
            "old_context": old_ctx,
            "new_context": new_ctx,
        }

    def allow_market_conversion(
        self,
        trade: Dict[str, Any],
        recent_trades: List[Dict[str, Any]],
        *,
        current_price: float,
        now: datetime | None = None,
    ) -> Dict[str, Any]:
        side = str(trade.get("type") or trade.get("side") or "").upper()
        symbol = str(trade.get("symbol") or self.config.get("symbol", "XAU/USD")).upper()
        if not self.post_exit_enabled or side not in {"BUY", "SELL"}:
            return {"allow": True, "reason": None}
        setup = self._setup_context_from_trade(trade)
        now = now or datetime.now(timezone.utc)

        for recent in recent_trades or []:
            if str(recent.get("status") or "").upper() in {"OPEN", "PARTIAL", "TP1_HIT", "PENDING"}:
                continue
            if str(recent.get("symbol") or "").upper() != symbol:
                continue
            if str(recent.get("type") or recent.get("side") or "").upper() != side:
                continue
            ref_price = self._trade_reference_price(recent)
            if ref_price <= 0:
                continue
            if abs(price_to_points(current_price - ref_price, symbol=symbol)) > self.price_zone_points:
                continue
            age_minutes = (now - self._trade_reference_time(recent, now)).total_seconds() / 60.0
            if age_minutes > self.lookback_hours * 60.0:
                continue
            outcome = self._trade_outcome(recent)
            cooldown = {"LOSS": self.cooldown_after_loss, "WIN": self.cooldown_after_win}.get(outcome, self.cooldown_after_breakeven)
            if age_minutes > cooldown:
                continue
            review = self.materially_new_thesis(
                setup,
                self._setup_context_from_trade(recent),
                symbol=symbol,
                reference_time=self._trade_reference_time(recent, now),
            )
            if review.get("allow"):
                continue
            return {
                "allow": False,
                "reason": f"recent closed {outcome} trade in same zone; {review.get('reason')}",
                "recent_trade_id": recent.get("id"),
            }
        return {"allow": True, "reason": None}

    def materially_new_thesis(
        self,
        new_ctx: Dict[str, Any],
        old_ctx: Dict[str, Any],
        *,
        symbol: str,
        reference_time: datetime | None = None,
    ) -> Dict[str, Any]:
        if not self.post_exit_enabled:
            return {"allow": True, "reason": None}
        if not isinstance(new_ctx, dict) or not new_ctx:
            return {"allow": False, "reason": "new signal has no rich setup context"}
        if not isinstance(old_ctx, dict) or not old_ctx:
            return {"allow": False, "reason": "previous setup has no context, so a fresh thesis is not proven"}

        new_key = str(new_ctx.get("state_key") or "")
        old_key = str(old_ctx.get("state_key") or "")
        new_type = str(new_ctx.get("setup_type") or "")
        old_type = str(old_ctx.get("setup_type") or "")
        new_poi = str(new_ctx.get("poi_type") or "")
        old_poi = str(old_ctx.get("poi_type") or "")

        zone_shift_pts = 0.0
        new_mid = self._setup_zone_midpoint(new_ctx)
        old_mid = self._setup_zone_midpoint(old_ctx)
        if new_mid is not None and old_mid is not None:
            zone_shift_pts = abs(price_to_points(new_mid - old_mid, symbol=symbol))
        different_poi = bool(
            new_key and old_key and new_key != old_key and (
                zone_shift_pts >= self.new_poi_min_distance_points or new_type != old_type or new_poi != old_poi
            )
        )

        old_state_rank = self._setup_state_rank(old_ctx.get("setup_state"))
        new_state_rank = self._setup_state_rank(new_ctx.get("setup_state"))
        state_progressed = new_state_rank >= old_state_rank + self.min_state_progress_steps

        old_trigger_score = self._safe_float(old_ctx.get("trigger_score"), 0.0)
        new_trigger_score = self._safe_float(new_ctx.get("trigger_score"), 0.0)
        trigger_improved = new_trigger_score >= old_trigger_score + self.min_trigger_score_improvement
        new_trigger_state = str(new_ctx.get("trigger_state") or "").upper()
        old_trigger_state = str(old_ctx.get("trigger_state") or "").upper()
        rejection_upgrade = new_trigger_state == "REJECTION_CONFIRMED" and old_trigger_state != "REJECTION_CONFIRMED"

        old_disp = self._safe_float(old_ctx.get("displacement_score"), 0.0)
        new_disp = self._safe_float(new_ctx.get("displacement_score"), 0.0)
        displacement_improved = new_disp >= old_disp + self.min_displacement_improvement

        old_dom = self._safe_float(old_ctx.get("thesis_dominance_score"), 0.0)
        new_dom = self._safe_float(new_ctx.get("thesis_dominance_score"), 0.0)
        dominance_improved = new_dom >= old_dom + self.min_dominance_improvement

        new_sweep_time = self._setup_sweep_time(new_ctx)
        old_sweep_time = self._setup_sweep_time(old_ctx)
        fresh_sweep = bool(
            new_sweep_time
            and (reference_time is None or new_sweep_time > reference_time)
            and (old_sweep_time is None or new_sweep_time > old_sweep_time)
        )

        if different_poi:
            return {"allow": True, "reason": f"new POI / state_key detected (zone shift {zone_shift_pts:.0f} pts)"}
        if state_progressed and (trigger_improved or rejection_upgrade or dominance_improved):
            return {"allow": True, "reason": "setup state progressed with stronger trigger / thesis quality"}
        if fresh_sweep and (displacement_improved or rejection_upgrade or dominance_improved):
            return {"allow": True, "reason": "fresh sweep / displacement created a new thesis"}

        blockers = []
        if not different_poi:
            blockers.append("no materially new POI")
        if not state_progressed:
            blockers.append("no stronger setup-state progression")
        if not fresh_sweep:
            blockers.append("no fresh sweep after the previous reference")
        if not (trigger_improved or rejection_upgrade):
            blockers.append("trigger did not improve enough")
        if not dominance_improved:
            blockers.append("thesis dominance did not improve enough")
        return {"allow": False, "reason": "; ".join(blockers[:3])}

    def _cancel_pending_group(self, trades: List[Dict[str, Any]], *, database: Any | None, reason: str) -> List[str]:
        cancelled_ids: List[str] = []
        if database is None:
            return cancelled_ids
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        for trade in trades:
            trade_id = str(trade.get("id") or "")
            if not trade_id:
                continue
            database.update_trade(
                trade_id,
                {
                    "status": "CANCELLED",
                    "result": "CANCELLED",
                    "closed_at": now_iso,
                    "close_time": now_iso,
                    "reasons": [reason],
                    "last_updated": now_iso,
                },
            )
            cancelled_ids.append(trade_id)
        return cancelled_ids

    def _pending_priority(self, trade: Dict[str, Any]) -> float:
        ctx = self._setup_context_from_trade(trade)
        return float(ctx.get("thesis_dominance_score") or ctx.get("return_probability_score") or 0)

    @staticmethod
    def _trade_outcome(trade: Dict[str, Any]) -> str:
        status = str(trade.get("status", "")).upper()
        if status in {"OPEN", "PARTIAL", "TP1_HIT", "PENDING"}:
            return "OPEN"
        result = str(trade.get("result", "") or "").upper()
        if result in {"WIN", "LOSS", "BREAKEVEN"}:
            return result
        for key in ("final_pnl", "final_pnl_points", "current_pnl", "current_pnl_points"):
            try:
                pnl = float(trade.get(key))
            except (TypeError, ValueError):
                continue
            if pnl > 0:
                return "WIN"
            if pnl < 0:
                return "LOSS"
            return "BREAKEVEN"
        if status in {"SL_HIT"}:
            return "LOSS"
        if status in {"TP2_HIT"}:
            return "WIN"
        return "BREAKEVEN"

    def _trade_reference_price(self, trade: Dict[str, Any]) -> float:
        outcome = self._trade_outcome(trade)
        keys = ("entry_price", "current_price") if outcome == "OPEN" else ("close_price", "entry_price", "current_price")
        for key in keys:
            value = trade.get(key)
            try:
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _trade_reference_time(trade: Dict[str, Any], now: datetime) -> datetime:
        closed = PendingGovernor._parse_dt(trade.get("closed_at") or trade.get("close_time"))
        if closed:
            return closed
        opened = PendingGovernor._parse_dt(trade.get("created_at") or trade.get("entry_time") or trade.get("opened_at"))
        return opened or now

    @staticmethod
    def _setup_context_from_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
        setup = decision.get("setup_context") or {}
        return dict(setup) if isinstance(setup, dict) else {}

    @staticmethod
    def _setup_context_from_trade(trade: Dict[str, Any]) -> Dict[str, Any]:
        snap = trade.get("signal_snapshot") or {}
        if isinstance(snap, str):
            try:
                import json
                snap = json.loads(snap)
            except Exception:
                snap = {}
        if not isinstance(snap, dict):
            snap = {}
        setup = snap.get("setup_context") or trade.get("setup_context") or {}
        return dict(setup) if isinstance(setup, dict) else {}

    @staticmethod
    def _setup_state_rank(value: Any) -> int:
        mapping = {
            "DETECTED": 0,
            "SWEEP_CONFIRMED": 1,
            "POI_MARKED": 2,
            "ENTRY_ARMED": 3,
            "ENTRY_TRIGGERED": 4,
            "INVALIDATED": 5,
            "EXPIRED": 5,
        }
        return mapping.get(str(value or "").upper(), -1)

    @staticmethod
    def _setup_zone_midpoint(setup: Dict[str, Any]) -> float | None:
        zone = setup.get("poi_zone") or {}
        try:
            top = float(zone.get("top"))
            bottom = float(zone.get("bottom"))
            if top > 0 and bottom > 0:
                return (top + bottom) / 2.0
        except (TypeError, ValueError, AttributeError):
            pass
        try:
            high = float(setup.get("poi_high"))
            low = float(setup.get("poi_low"))
            if high > 0 and low > 0:
                return (high + low) / 2.0
        except (TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _setup_sweep_time(setup: Dict[str, Any]) -> datetime | None:
        details = setup.get("details") or {}
        if isinstance(details, dict):
            sweep = details.get("recent_sweep") or {}
            if isinstance(sweep, dict):
                return PendingGovernor._parse_dt(sweep.get("time"))
        return None

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
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

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
