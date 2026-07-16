"""Pending thesis governor.

Stage 2 of the pending-thesis model:
- keep the current pending thesis if it still dominates
- replace it when a clearly stronger thesis appears
- cancel weak/stale pending theses instead of stacking same-direction orders
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


class PendingGovernor:
    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        cfg = (self.config.get("pending_governor") or {}) if isinstance(self.config, dict) else {}
        self.enabled = bool(cfg.get("enabled", True))
        self.replace_min_dominance_delta = float(cfg.get("replace_min_dominance_delta", 8) or 8)
        self.keep_if_return_probability_above = float(cfg.get("keep_if_return_probability_above", 45) or 45)
        self.cancel_if_return_probability_below = float(cfg.get("cancel_if_return_probability_below", 25) or 25)
        self.require_quality_improvement = bool(cfg.get("require_quality_improvement", True))

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

        if dominance_gap >= self.replace_min_dominance_delta and (quality_improved or not self.require_quality_improvement):
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
        setup = snap.get("setup_context") or {}
        return dict(setup) if isinstance(setup, dict) else {}
