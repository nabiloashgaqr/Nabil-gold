"""Adaptive execution switch.

Phase 9 goal:
- when a morning/session pending plan already exists,
  treat later 3-agent or 2-agent+confirmation events as execution upgrades,
  not blindly as a blocked duplicate or a brand-new independent trade.

Outputs:
- KEEP_PENDING
- PROMOTE_TO_MARKET
- REPLACE_WITH_CONTINUATION
- NO_TRADE_MISSED_MOVE
- ALLOW_NEW
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from services.pending_governor import PendingGovernor
from services.scenario_governor import ScenarioGovernor
from utils.instruments import price_to_points


class AdaptiveExecutionService:
    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        cfg = (self.config.get("adaptive_execution") or {}) if isinstance(self.config, dict) else {}
        self.enabled = bool(cfg.get("enabled", True))
        self.keep_pending_max_move_points = float(cfg.get("keep_pending_max_move_points", 120) or 120)
        self.keep_pending_max_target_progress_pct = float(cfg.get("keep_pending_max_target_progress_pct", 30) or 30)
        self.promote_to_market_min_move_points = float(cfg.get("promote_to_market_min_move_points", 60) or 60)
        self.promote_to_market_max_move_points = float(cfg.get("promote_to_market_max_move_points", 220) or 220)
        self.max_target_progress_for_market_promotion_pct = float(cfg.get("max_target_progress_for_market_promotion_pct", 55) or 55)
        self.min_remaining_rr_for_market_promotion = float(
            cfg.get("min_remaining_rr_for_market_promotion", (self.config.get("risk_settings", {}) or {}).get("min_rr_ratio", 1.5))
            or (self.config.get("risk_settings", {}) or {}).get("min_rr_ratio", 1.5)
        )
        self.profile_overrides = (cfg.get("profiles") or {}) if isinstance(cfg, dict) else {}
        self.session_adjustments = (cfg.get("session_adjustments") or {}) if isinstance(cfg, dict) else {}
        self.pending_governor = PendingGovernor(self.config)
        self.scenario_governor = ScenarioGovernor(self.config)

    def review(self, decision: Dict[str, Any], open_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        side = str(decision.get("decision") or "").upper()
        if not self.enabled or side not in {"BUY", "SELL"}:
            return {"action": "ALLOW_NEW", "reason": None, "decision": decision}

        symbol = str(decision.get("symbol") or self.config.get("symbol", "XAU/USD")).upper()
        pending_same_direction = [
            t for t in (open_trades or [])
            if str(t.get("status") or "").upper() == "PENDING"
            and str(t.get("symbol") or "").upper() == symbol
            and str(t.get("type") or t.get("side") or "").upper() == side
        ]
        if not pending_same_direction:
            return {"action": "ALLOW_NEW", "reason": None, "decision": decision}

        target_family = self._select_family(decision, pending_same_direction)
        anchor = self._choose_anchor(target_family)
        if not anchor:
            return {"action": "ALLOW_NEW", "reason": None, "decision": decision}

        current_price = self._current_price(decision)
        if current_price <= 0:
            return {"action": "ALLOW_NEW", "reason": None, "decision": decision}

        old_ctx = self.pending_governor._setup_context_from_trade(anchor)
        new_ctx = self.pending_governor._setup_context_from_decision(decision)
        same_family = self._scenario_id_from_decision(decision) and self._scenario_id_from_decision(decision) == self.scenario_governor.scenario_id_from_trade(anchor)
        material = self.pending_governor.materially_new_thesis(new_ctx, old_ctx, symbol=symbol)
        move_points = self._favorable_move_points(side, self._f(anchor.get("entry_price"), 0.0), current_price, symbol)
        target_progress_pct = self._target_progress_pct(anchor, current_price, side, symbol)
        remaining_rr = self._remaining_rr(decision, anchor, current_price)
        rules = self._effective_rules(decision, anchor)

        if same_family or not material.get("allow"):
            if move_points <= rules["keep_pending_max_move_points"] and target_progress_pct <= rules["keep_pending_max_target_progress_pct"]:
                return {
                    "action": "KEEP_PENDING",
                    "reason": (
                        f"existing planned pending still valid (move {move_points:.0f} pts, progress {target_progress_pct:.0f}%, RR {remaining_rr:.2f})"
                    ),
                    "decision": decision,
                    "anchor_trade_id": anchor.get("id"),
                    "calibration": rules,
                }
            if (
                rules["promote_to_market_min_move_points"] <= move_points <= rules["promote_to_market_max_move_points"]
                and target_progress_pct <= rules["max_target_progress_for_market_promotion_pct"]
                and remaining_rr >= rules["min_remaining_rr_for_market_promotion"]
            ):
                adapted = self._promote_to_market(decision, current_price)
                adapted["adaptive_execution"]["calibration"] = rules
                return {
                    "action": "PROMOTE_TO_MARKET",
                    "reason": (
                        f"market moved {move_points:.0f} pts without fill; promote to market while remaining RR {remaining_rr:.2f} is still acceptable"
                    ),
                    "decision": adapted,
                    "anchor_trade_id": anchor.get("id"),
                    "remaining_rr": round(remaining_rr, 2),
                    "move_points": round(move_points, 1),
                    "target_progress_pct": round(target_progress_pct, 1),
                    "calibration": rules,
                }
            return {
                "action": "NO_TRADE_MISSED_MOVE",
                "reason": (
                    f"planned pending missed the move (move {move_points:.0f} pts, progress {target_progress_pct:.0f}%, remaining RR {remaining_rr:.2f})"
                ),
                "decision": decision,
                "anchor_trade_id": anchor.get("id"),
                "calibration": rules,
            }

        adapted = deepcopy(decision)
        adapted.setdefault("reasons", []).append(f"Adaptive execution: replaced old pending family because {material.get('reason')}")
        adapted["adaptive_execution"] = {
            "action": "REPLACE_WITH_CONTINUATION",
            "reason": material.get("reason"),
        }
        return {
            "action": "REPLACE_WITH_CONTINUATION",
            "reason": f"new continuation / execution thesis is materially stronger: {material.get('reason')}",
            "decision": adapted,
            "anchor_trade_id": anchor.get("id"),
        }

    def _promote_to_market(self, decision: Dict[str, Any], current_price: float) -> Dict[str, Any]:
        adapted = deepcopy(decision)
        signal = dict(adapted.get("signal") or {})
        entry = dict(signal.get("entry") or {})
        side = str(adapted.get("decision") or signal.get("type") or "").upper()
        entry.update(
            {
                "price": round(current_price, 2),
                "low": round(current_price, 2),
                "high": round(current_price, 2),
                "kind": "MARKET",
                "order_type": f"{side}_MARKET",
                "basis": "Adaptive execution switch: confirmed move without fill",
                "current_price": round(current_price, 2),
                "distance_points": 0.0,
            }
        )
        signal.update(
            {
                "type": side,
                "entry": entry,
                "order_type": f"{side}_MARKET",
                "entry_kind": "MARKET",
            }
        )
        adapted["signal"] = signal
        adapted["entry_mode"] = "adaptive_market_promotion"
        adapted.setdefault("reasons", []).append("Adaptive execution promoted the planned pending thesis to MARKET.")
        adapted["adaptive_execution"] = {
            "action": "PROMOTE_TO_MARKET",
            "reason": "confirmed move without fill",
        }
        return adapted

    def _effective_rules(self, decision: Dict[str, Any], anchor: Dict[str, Any]) -> Dict[str, float | str]:
        rules: Dict[str, float | str] = {
            "keep_pending_max_move_points": self.keep_pending_max_move_points,
            "keep_pending_max_target_progress_pct": self.keep_pending_max_target_progress_pct,
            "promote_to_market_min_move_points": self.promote_to_market_min_move_points,
            "promote_to_market_max_move_points": self.promote_to_market_max_move_points,
            "max_target_progress_for_market_promotion_pct": self.max_target_progress_for_market_promotion_pct,
            "min_remaining_rr_for_market_promotion": self.min_remaining_rr_for_market_promotion,
            "profile": "default",
            "session_label": self._session_label(decision, anchor),
        }
        profile = self._profile_key(decision, anchor)
        rules["profile"] = profile
        override = self.profile_overrides.get(profile) or {}
        for key in [
            "keep_pending_max_move_points",
            "keep_pending_max_target_progress_pct",
            "promote_to_market_min_move_points",
            "promote_to_market_max_move_points",
            "max_target_progress_for_market_promotion_pct",
            "min_remaining_rr_for_market_promotion",
        ]:
            if key in override:
                try:
                    rules[key] = float(override[key])
                except (TypeError, ValueError):
                    pass

        session_key = str(rules["session_label"] or "").upper()
        adjustment = (self.session_adjustments.get(session_key) or self.session_adjustments.get(str(rules["session_label"]) or "") or {})
        for key in [
            "keep_pending_max_move_points",
            "keep_pending_max_target_progress_pct",
            "promote_to_market_min_move_points",
            "promote_to_market_max_move_points",
            "max_target_progress_for_market_promotion_pct",
            "min_remaining_rr_for_market_promotion",
        ]:
            if key in adjustment:
                try:
                    rules[key] = float(adjustment[key])
                except (TypeError, ValueError):
                    pass

        preference = self._execution_preference(decision, anchor)
        if preference == "NEAR_MARKET_WATCH":
            rules["promote_to_market_min_move_points"] = max(0.0, float(rules["promote_to_market_min_move_points"]) - 15.0)
            rules["promote_to_market_max_move_points"] = float(rules["promote_to_market_max_move_points"]) + 20.0
            rules["max_target_progress_for_market_promotion_pct"] = float(rules["max_target_progress_for_market_promotion_pct"]) + 5.0
        elif preference == "LADDER_PENDING":
            rules["keep_pending_max_move_points"] = float(rules["keep_pending_max_move_points"]) + 10.0
        rules["execution_preference"] = preference
        return rules

    def _profile_key(self, decision: Dict[str, Any], anchor: Dict[str, Any]) -> str:
        setup = decision.get("setup_context") or {}
        if not isinstance(setup, dict):
            setup = {}
        old = self.pending_governor._setup_context_from_trade(anchor)
        setup_type = str(setup.get("setup_type") or old.get("setup_type") or "").upper()
        if setup_type in {"LIQUIDITY_REVERSAL", "REVERSAL_ATTEMPT"}:
            return "reversal"
        if setup_type in {"STRUCTURE_CONTINUATION", "TREND_CONTINUATION", "ORDER_BLOCK_PULLBACK", "PULLBACK_ENTRY", "TREND_PULLBACK"}:
            return "continuation"
        if setup_type in {"RANGE_FADE", "SMC_CONTEXT", "MIXED_ALIGNMENT"}:
            return "range"
        return "default"

    def _session_label(self, decision: Dict[str, Any], anchor: Dict[str, Any]) -> str:
        session = decision.get("session_info") or {}
        if isinstance(session, dict) and session.get("current_session"):
            return str(session.get("current_session"))
        plan = decision.get("session_plan") or {}
        if isinstance(plan, dict) and plan.get("session_label"):
            return str(plan.get("session_label"))
        snap = anchor.get("signal_snapshot") or {}
        if isinstance(snap, dict):
            if isinstance(snap.get("session_plan"), dict) and snap.get("session_plan", {}).get("session_label"):
                return str(snap.get("session_plan", {}).get("session_label"))
            if isinstance(snap.get("session_info"), dict) and snap.get("session_info", {}).get("current_session"):
                return str(snap.get("session_info", {}).get("current_session"))
        return "Unknown Session"

    def _execution_preference(self, decision: Dict[str, Any], anchor: Dict[str, Any]) -> str:
        plan = decision.get("session_plan") or {}
        if isinstance(plan, dict) and plan.get("execution_preference"):
            return str(plan.get("execution_preference"))
        snap = anchor.get("signal_snapshot") or {}
        if isinstance(snap, dict) and isinstance(snap.get("session_plan"), dict) and snap.get("session_plan", {}).get("execution_preference"):
            return str(snap.get("session_plan", {}).get("execution_preference"))
        return "UNKNOWN"

    def _select_family(self, decision: Dict[str, Any], trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        desired_sid = self._scenario_id_from_decision(decision)
        if desired_sid:
            matched = [t for t in trades if self.scenario_governor.scenario_id_from_trade(t) == desired_sid]
            if matched:
                return matched
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for trade in trades:
            sid = self.scenario_governor.scenario_id_from_trade(trade) or f"LEGACY::{trade.get('id')}"
            groups.setdefault(sid, []).append(trade)
        best_family = max(groups.values(), key=lambda rows: max(self.pending_governor._pending_priority(t) for t in rows))
        return best_family

    def _choose_anchor(self, trades: List[Dict[str, Any]]) -> Dict[str, Any] | None:
        if not trades:
            return None
        primary = [t for t in trades if str((self.pending_governor._setup_context_from_trade(t) or {}).get("pending_plan_role") or (self.pending_governor._setup_context_from_trade(t) or {}).get("selection_role") or "").upper() == "PRIMARY"]
        pool = primary or trades
        return max(pool, key=self.pending_governor._pending_priority)

    def _scenario_id_from_decision(self, decision: Dict[str, Any]) -> str:
        plan = decision.get("session_plan") or {}
        if isinstance(plan, dict) and plan.get("scenario_id"):
            return str(plan.get("scenario_id"))
        setup = decision.get("setup_context") or {}
        if isinstance(setup, dict) and setup.get("scenario_id"):
            return str(setup.get("scenario_id"))
        return ""

    def _current_price(self, decision: Dict[str, Any]) -> float:
        signal = decision.get("signal") or {}
        entry = signal.get("entry") or {}
        for value in [entry.get("current_price"), decision.get("current_price")]:
            try:
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    def _favorable_move_points(self, side: str, pending_entry: float, current_price: float, symbol: str) -> float:
        if pending_entry <= 0 or current_price <= 0:
            return 0.0
        if side == "SELL":
            return max(0.0, price_to_points(pending_entry - current_price, symbol=symbol))
        return max(0.0, price_to_points(current_price - pending_entry, symbol=symbol))

    def _target_progress_pct(self, trade: Dict[str, Any], current_price: float, side: str, symbol: str) -> float:
        entry = self._f(trade.get("entry_price"), 0.0)
        target = self._f(trade.get("tp2") or trade.get("tp1"), 0.0)
        if entry <= 0 or target <= 0:
            return 0.0
        total = abs(price_to_points(target - entry, symbol=symbol))
        if total <= 0:
            return 0.0
        move = self._favorable_move_points(side, entry, current_price, symbol)
        return min(999.0, (move / total) * 100.0)

    def _remaining_rr(self, decision: Dict[str, Any], trade: Dict[str, Any], current_price: float) -> float:
        signal = decision.get("signal") or {}
        side = str(decision.get("decision") or signal.get("type") or trade.get("type") or trade.get("side") or "").upper()
        stop_loss = self._f(signal.get("stop_loss"), self._f(trade.get("stop_loss"), 0.0))
        target = self._f(signal.get("tp2"), self._f(trade.get("tp2"), 0.0))
        if side not in {"BUY", "SELL"} or stop_loss <= 0 or target <= 0 or current_price <= 0:
            return 0.0
        risk = abs(price_to_points(stop_loss - current_price, symbol=str(decision.get("symbol") or trade.get("symbol") or self.config.get("symbol", "XAU/USD"))))
        reward = abs(price_to_points(target - current_price, symbol=str(decision.get("symbol") or trade.get("symbol") or self.config.get("symbol", "XAU/USD"))))
        if risk <= 0:
            return 0.0
        return reward / risk

    @staticmethod
    def _f(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
