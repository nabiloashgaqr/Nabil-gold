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
        # Minutes a freshly placed order is protected from replacement.
        #
        # A LIMIT order needs TIME to be reached -- that is its whole purpose.
        # Cancelling one minutes after birth means it never had the chance the
        # plan was built around, and the map churns instead of trading.
        #
        # 2026-08-03: order 03ed828a was published at 14:03 with quality B
        # 74.0, dominance 77.3 and freshness FRESH, and was cancelled five
        # minutes later by a plan scoring four points higher. Nothing in this
        # class looked at how long it had been resting.
        #
        # A stale or invalidated order is still replaceable immediately: the
        # grace protects orders that are merely young, not orders that are
        # wrong.
        self.replace_grace_minutes = float(cfg.get("replace_grace_minutes", 30) or 0)

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
        # Score the incumbent on its own evidence, not only on a session_plan
        # it may never have had. See _incumbent_score.
        old_score = self._incumbent_score(incumbent_trades[0])
        new_dom = self._primary_dominance(plan)
        old_dom = self._setup_dominance(incumbent_setup)
        score_gap = new_score - old_score
        dom_gap = new_dom - old_dom
        incumbent_states = {str(((self.setup_from_trade(t).get("pending_runtime") if isinstance(self.setup_from_trade(t), dict) else {}) or {})) for t in incumbent_trades}
        stale_family = all(self._freshness_state(t) in {"STALE", "REVALIDATION_REQUIRED"} for t in incumbent_trades)

        # A stale family is always replaceable. Otherwise the newcomer must be
        # better on one axis WITHOUT being materially worse on the other.
        #
        # The old rule was a plain OR, so a plan whose dominance rose by 6
        # could evict an incumbent even while its quality score fell: on
        # 2026-07-30 that cancelled an A 88.9 order for a D 59.0 one, and
        # again cancelled a 59.0 order for another 59.0 that happened to sit
        # 6.2 points higher on dominance. Churn, not improvement.
        #
        # `max_regression` gives the same tolerance in reverse: an axis may
        # dip slightly, but not collapse.
        max_regression = max(self.min_plan_score_improvement,
                             self.min_primary_dominance_improvement)
        better_on_score = score_gap >= self.min_plan_score_improvement
        better_on_dominance = dom_gap >= self.min_primary_dominance_improvement
        not_worse_on_score = score_gap > -max_regression
        not_worse_on_dominance = dom_gap > -max_regression
        replace = stale_family or (
            (better_on_score and not_worse_on_dominance)
            or (better_on_dominance and not_worse_on_score)
        )

        # A young order keeps its place unless it is genuinely stale.
        #
        # Checked after the score comparison so the reason can quote the real
        # numbers, and deliberately AFTER `stale_family`: an order that has
        # already expired, been invalidated, or watched the market walk away
        # is replaceable at any age. This only protects an order that simply
        # has not been given time to fill.
        if replace and not stale_family and self.replace_grace_minutes > 0:
            youngest = self._youngest_age_minutes(incumbent_trades)
            if youngest is not None and youngest < self.replace_grace_minutes:
                return {
                    "action": "KEEP_EXISTING_FAMILY",
                    "reason": (
                        f"existing pending family is only {youngest:.0f} min old and still "
                        f"fresh; it keeps its place for {self.replace_grace_minutes:.0f} min "
                        f"(old_score={old_score:.1f}, new_score={new_score:.1f}, "
                        f"old_dom={old_dom:.1f}, new_dom={new_dom:.1f})"
                    ),
                    "cancelled_ids": [],
                    "old_scenario_id": incumbent_id,
                    "new_scenario_id": new_sid,
                }

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

    @classmethod
    def _youngest_age_minutes(cls, trades: List[Dict[str, Any]]) -> float | None:
        """Age of the newest order in a family, in minutes.

        The YOUNGEST is the right measure: a family is protected while any of
        its legs still has a fair chance to fill. Returns None when no
        timestamp can be read, so an unreadable row falls through to the
        normal comparison rather than being protected on trust.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        ages: List[float] = []
        for trade in trades or []:
            if not isinstance(trade, dict):
                continue
            for key in ("created_at", "entry_time", "opened_at"):
                raw = str(trade.get(key) or "")
                if not raw:
                    continue
                try:
                    stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                ages.append(max(0.0, (now - stamp).total_seconds() / 60.0))
                break
        return min(ages) if ages else None

    @staticmethod
    def _plan_score(plan: Dict[str, Any] | None) -> float:
        if not isinstance(plan, dict):
            return 0.0
        try:
            return float(plan.get("planner_confidence") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _incumbent_score(cls, trade: Dict[str, Any]) -> float:
        """Score a sitting order on whatever evidence it actually carries.

        Not every pending order is planner-led. A 3-AGENT CONSENSUS signal is
        admitted through a different path and stores no `session_plan`, so
        `_plan_score` read it as 0.0 -- and on 2026-07-30 that let a D 59.0
        dual-agent plan evict an A 88.9 consensus order with 4/5 qualified
        agents and 99.3 dominance, three minutes after it was placed. The
        "improvement" was 59 - 0.

        Fall back to the quality score the signal was actually graded with,
        so a non-planner order defends itself with its own number instead of
        with a zero it never earned.
        """
        plan_score = cls._plan_score(cls.plan_from_trade(trade))
        if plan_score > 0:
            return plan_score
        snap = trade.get("signal_snapshot") or {}
        if isinstance(snap, str):
            try:
                import json
                snap = json.loads(snap)
            except Exception:
                snap = {}
        if not isinstance(snap, dict):
            snap = {}
        for source in (snap.get("quality"), trade.get("quality")):
            if isinstance(source, dict):
                try:
                    value = float(source.get("score") or 0.0)
                except (TypeError, ValueError):
                    value = 0.0
                if value > 0:
                    return value
        for key in ("quality_score", "confidence"):
            for holder in (snap, trade):
                try:
                    value = float((holder or {}).get(key) or 0.0)
                except (TypeError, ValueError):
                    value = 0.0
                if value > 0:
                    return value
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
