"""Scenario family governor.

Phase 6 goal:
- manage PRIMARY / STANDBY ladder orders as one scenario family
- cancel sibling pending orders when one family member activates
- allow a newer, stronger session-plan family to replace an older pending family
  for the same symbol/direction
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


class ScenarioGovernor:
    LIVE_STATUSES = {"OPEN", "PARTIAL", "TP1_HIT"}

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        cfg = (self.config.get("scenario_governor") or {}) if isinstance(self.config, dict) else {}
        self.enabled = bool(cfg.get("enabled", True))
        self.cancel_siblings_on_activation = bool(cfg.get("cancel_siblings_on_activation", True))
        self.allow_replace_older_pending_scenarios = bool(cfg.get("allow_replace_older_pending_scenarios", True))
        self.min_plan_score_improvement = float(cfg.get("min_plan_score_improvement", 4) or 4)
        self.min_primary_dominance_improvement = float(cfg.get("min_primary_dominance_improvement", 5) or 5)

    def review_new_plan(
        self,
        plan: Dict[str, Any],
        open_trades: List[Dict[str, Any]],
        *,
        database: Any | None = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"action": "ALLOW_NEW_FAMILY", "reason": None, "cancelled_ids": []}
        if not isinstance(plan, dict) or not plan.get("plan_ready"):
            return {"action": "ALLOW_NEW_FAMILY", "reason": None, "cancelled_ids": []}

        symbol = str(plan.get("symbol") or self.config.get("symbol", "XAU/USD")).upper()
        side = str(plan.get("session_bias") or "").upper()
        new_sid = str(plan.get("scenario_id") or "")
        if side not in {"BUY", "SELL"} or not new_sid:
            return {"action": "ALLOW_NEW_FAMILY", "reason": None, "cancelled_ids": []}

        relevant = [
            t for t in (open_trades or [])
            if str(t.get("symbol") or "").upper() == symbol
            and str(t.get("type") or t.get("side") or "").upper() == side
        ]
        if not relevant:
            return {"action": "ALLOW_NEW_FAMILY", "reason": None, "cancelled_ids": []}

        if any(str(t.get("status") or "").upper() in self.LIVE_STATUSES for t in relevant):
            return {
                "action": "KEEP_EXISTING_FAMILY",
                "reason": "live scenario trade already exists for this symbol/direction",
                "cancelled_ids": [],
            }

        pending = [t for t in relevant if str(t.get("status") or "").upper() == "PENDING"]
        if not pending:
            return {"action": "ALLOW_NEW_FAMILY", "reason": None, "cancelled_ids": []}

        same_family = [t for t in pending if self.scenario_id_from_trade(t) == new_sid]
        if same_family:
            return {
                "action": "KEEP_EXISTING_FAMILY",
                "reason": "pending scenario family already exists",
                "cancelled_ids": [],
            }

        if not self.allow_replace_older_pending_scenarios:
            return {
                "action": "KEEP_EXISTING_FAMILY",
                "reason": "older pending family exists and replacement is disabled",
                "cancelled_ids": [],
            }

        families: dict[str, list[Dict[str, Any]]] = {}
        for trade in pending:
            sid = self.scenario_id_from_trade(trade)
            if not sid:
                sid = f"LEGACY::{trade.get('id')}"
            families.setdefault(sid, []).append(trade)

        incumbent_id, incumbent_trades = max(families.items(), key=lambda item: self._family_priority(item[1]))
        incumbent_plan = self.plan_from_trade(incumbent_trades[0])
        incumbent_setup = self.setup_from_trade(incumbent_trades[0])
        new_score = self._plan_score(plan)
        old_score = self._plan_score(incumbent_plan)
        new_dom = self._primary_dominance(plan)
        old_dom = self._setup_dominance(incumbent_setup)
        score_gap = new_score - old_score
        dom_gap = new_dom - old_dom
        incumbent_states = {str(((self.setup_from_trade(t).get("pending_runtime") if isinstance(self.setup_from_trade(t), dict) else {}) or {})) for t in incumbent_trades}
        stale_family = all(self._freshness_state(t) in {"STALE", "REVALIDATION_REQUIRED"} for t in incumbent_trades)

        replace = stale_family or score_gap >= self.min_plan_score_improvement or dom_gap >= self.min_primary_dominance_improvement
        if not replace:
            return {
                "action": "KEEP_EXISTING_FAMILY",
                "reason": (
                    f"existing pending family still acceptable (old_score={old_score:.1f}, new_score={new_score:.1f}, "
                    f"old_dom={old_dom:.1f}, new_dom={new_dom:.1f})"
                ),
                "cancelled_ids": [],
                "old_scenario_id": incumbent_id,
                "new_scenario_id": new_sid,
            }

        cancelled_ids = self.cancel_family(
            incumbent_trades,
            database=database,
            reason=(
                f"Scenario governor replaced older pending family. old_score={old_score:.1f}, new_score={new_score:.1f}, "
                f"old_dom={old_dom:.1f}, new_dom={new_dom:.1f}, stale_family={stale_family}"
            ),
        )
        return {
            "action": "REPLACE_PENDING_FAMILY",
            "reason": "new session-plan family is stronger or the old family is stale",
            "cancelled_ids": cancelled_ids,
            "old_scenario_id": incumbent_id,
            "new_scenario_id": new_sid,
        }

    def handle_activation(
        self,
        activated_trade: Dict[str, Any],
        *,
        database: Any | None,
        open_trades: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        if not self.enabled or not self.cancel_siblings_on_activation:
            return {"action": "NOOP", "cancelled_ids": []}
        status = str(activated_trade.get("status") or "").upper()
        if status != "PENDING":
            return {"action": "NOOP", "cancelled_ids": []}
        scenario_id = self.scenario_id_from_trade(activated_trade)
        if not scenario_id:
            return {"action": "NOOP", "cancelled_ids": []}
        symbol = str(activated_trade.get("symbol") or self.config.get("symbol", "XAU/USD")).upper()
        side = str(activated_trade.get("type") or activated_trade.get("side") or "").upper()
        trade_id = str(activated_trade.get("id") or "")
        if database is None:
            return {"action": "NOOP", "cancelled_ids": []}
        trades = open_trades if open_trades is not None else (database.get_open_trades() if hasattr(database, "get_open_trades") else [])
        siblings = [
            t for t in (trades or [])
            if str(t.get("id") or "") != trade_id
            and str(t.get("status") or "").upper() == "PENDING"
            and str(t.get("symbol") or "").upper() == symbol
            and str(t.get("type") or t.get("side") or "").upper() == side
            and self.scenario_id_from_trade(t) == scenario_id
        ]
        if not siblings:
            return {"action": "NOOP", "cancelled_ids": []}
        cancelled_ids = self.cancel_family(
            siblings,
            database=database,
            reason="Scenario governor cancelled sibling pending orders after one family member activated",
        )
        return {
            "action": "CANCELLED_SIBLINGS_ON_ACTIVATION",
            "cancelled_ids": cancelled_ids,
            "scenario_id": scenario_id,
        }

    def cancel_family(self, trades: List[Dict[str, Any]], *, database: Any | None, reason: str) -> List[str]:
        if database is None:
            return []
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        cancelled_ids: List[str] = []
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

    @staticmethod
    def scenario_id_from_trade(trade: Dict[str, Any]) -> str:
        plan = ScenarioGovernor.plan_from_trade(trade)
        if isinstance(plan, dict) and plan.get("scenario_id"):
            return str(plan.get("scenario_id"))
        setup = ScenarioGovernor.setup_from_trade(trade)
        if isinstance(setup, dict) and setup.get("scenario_id"):
            return str(setup.get("scenario_id"))
        return ""

    @staticmethod
    def plan_from_trade(trade: Dict[str, Any]) -> Dict[str, Any]:
        snap = trade.get("signal_snapshot") or {}
        if isinstance(snap, str):
            try:
                import json
                snap = json.loads(snap)
            except Exception:
                snap = {}
        if not isinstance(snap, dict):
            snap = {}
        plan = snap.get("session_plan") or trade.get("session_plan") or {}
        return dict(plan) if isinstance(plan, dict) else {}

    @staticmethod
    def setup_from_trade(trade: Dict[str, Any]) -> Dict[str, Any]:
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
    def _family_priority(trades: List[Dict[str, Any]]) -> float:
        if not trades:
            return 0.0
        best = trades[0]
        plan = ScenarioGovernor.plan_from_trade(best)
        setup = ScenarioGovernor.setup_from_trade(best)
        return ScenarioGovernor._plan_score(plan) + ScenarioGovernor._setup_dominance(setup)

    @staticmethod
    def _plan_score(plan: Dict[str, Any] | None) -> float:
        if not isinstance(plan, dict):
            return 0.0
        try:
            return float(plan.get("planner_confidence") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _primary_dominance(plan: Dict[str, Any] | None) -> float:
        if not isinstance(plan, dict):
            return 0.0
        primary = plan.get("primary_poi") or {}
        try:
            return float((primary or {}).get("thesis_dominance_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _setup_dominance(setup: Dict[str, Any] | None) -> float:
        if not isinstance(setup, dict):
            return 0.0
        try:
            return float(setup.get("thesis_dominance_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _freshness_state(trade: Dict[str, Any]) -> str:
        snap = trade.get("signal_snapshot") or {}
        if isinstance(snap, str):
            try:
                import json
                snap = json.loads(snap)
            except Exception:
                snap = {}
        if not isinstance(snap, dict):
            snap = {}
        runtime = snap.get("pending_runtime") or {}
        return str((runtime or {}).get("freshness_state") or "FRESH").upper()
