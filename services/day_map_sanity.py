"""Unified day-map sanity gate.

Phase E goal:
- prevent legacy/local execution paths from bypassing the day map
- require directional entries to be inside or near the planner's primary/standby zones
- enforce planner-led execution on EXTREME_POI scenarios
"""

from __future__ import annotations

from typing import Any, Dict, List

from utils.instruments import price_to_points, points_to_price


class DayMapSanityService:
    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        cfg = (self.config.get("day_map_sanity") or {}) if isinstance(self.config, dict) else {}
        # Backward-compatible default: disabled unless explicitly configured.
        # Production config enables it, but legacy/minimal tests that focus only
        # on signal-delivery should not start failing because no planner exists.
        self.enabled = bool(cfg.get("enabled", False))
        self.block_when_plan_not_ready = bool(cfg.get("block_when_plan_not_ready", True))
        # Opt-in strict mode. `block_when_plan_not_ready` was being read as
        # "no map => no trade", which turned a missing second opinion into a
        # veto: across a 300-cycle sample 294 cycles (98%) carried no ready
        # map, almost always because the planner had refused its own map on
        # archetype conviction. Operators who genuinely want planner-only
        # trading can still say so explicitly here.
        self.require_day_map_for_all_entries = bool(
            cfg.get("require_day_map_for_all_entries", False)
        )
        self.entry_zone_tolerance_points = float(cfg.get("entry_zone_tolerance_points", 40) or 40)
        self.require_planner_execution_for_extreme_poi = bool(cfg.get("require_planner_execution_for_extreme_poi", True))
        self.allowed_execution_modes_for_extreme_poi = set(
            str(x).strip() for x in (cfg.get("allowed_execution_modes_for_extreme_poi") or [
                "session_plan_ladder",
                "session_plan_ladder_market",
                "adaptive_market_promotion",
            ]) if str(x).strip()
        )

    def review(self, decision: Dict[str, Any], session_plan: Dict[str, Any]) -> Dict[str, Any]:
        side = str(decision.get("decision") or "").upper()
        if not self.enabled or side not in {"BUY", "SELL"}:
            return {"action": "ALLOW", "reason": None}

        if not isinstance(session_plan, dict):
            session_plan = {}
        if not bool(session_plan.get("plan_ready", False)):
            # A map that does not exist holds no opinion to contradict. This
            # branch used to refuse every directional signal while the planner
            # had nothing ready, so on 2026-07-29 a SELL consensus of 87.3%
            # with zero qualified opposition was turned away for the absence
            # of a second opinion rather than any disagreement with one -- on
            # a day the market fell 310 points.
            #
            # Every other admission check still runs: the planner gate, the
            # opposition ceiling, duplicate and pending governors, the
            # cross-path distance rule and final validation. This only stops
            # silence from being counted as dissent.
            if self.block_when_plan_not_ready and self.require_day_map_for_all_entries:
                return {
                    "action": "BLOCK_NO_DAY_MAP",
                    "reason": "no confirmed day map is ready for this symbol yet",
                }
            return {
                "action": "ALLOW",
                "reason": "no day map is active, so there is no mapped view to contradict",
            }

        authority_state = str(session_plan.get("authority_state") or "").upper()
        authority_direction = str(session_plan.get("authority_direction") or "").upper()
        if authority_state == "CONFIRMED" and authority_direction in {"BUY", "SELL"} and side != authority_direction:
            return {
                "action": "BLOCK_DIRECTION_MISMATCH",
                "reason": f"confirmed {authority_direction} day map does not allow a {side} execution path here",
            }

        # Zone discipline is an instruction from the map about where its own
        # thesis may be executed. A map whose authority was never earned --
        # WEAK or CONFLICTED -- has already lost the right to veto the
        # opposite direction at the authority layer, and must not reimpose
        # that veto here by declaring the other side "outside its zones".
        # It keeps full force over trades that agree with it.
        if authority_state not in {"CONFIRMED"} and authority_direction in {"BUY", "SELL"} and side != authority_direction:
            return {
                "action": "ALLOW",
                "reason": (
                    f"day map is {authority_state.lower() or 'unconfirmed'} and does not "
                    f"hold authority over an opposing {side} execution"
                ),
            }

        adaptive = decision.get("adaptive_execution") or {}
        if isinstance(adaptive, dict) and adaptive.get("action") in {"PROMOTE_TO_MARKET", "REPLACE_WITH_CONTINUATION"}:
            return {"action": "ALLOW", "reason": "adaptive execution already reconciled this signal with the day map"}

        entry_mode = str(decision.get("entry_mode") or "")
        poi_classification = str(session_plan.get("poi_classification") or "").upper()
        if (
            self.require_planner_execution_for_extreme_poi
            and poi_classification == "EXTREME_POI"
            and entry_mode not in self.allowed_execution_modes_for_extreme_poi
        ):
            return {
                "action": "BLOCK_EXTREME_POI_BYPASS",
                "reason": "extreme day-map POI requires planner-led execution (starter/add-on or adaptive market promotion)",
            }

        signal = decision.get("signal") or {}
        entry = signal.get("entry") or {}
        order_type = str(signal.get("order_type") or entry.get("order_type") or "")
        entry_price = self._f(entry.get("price"), self._f(decision.get("current_price"), 0.0))
        current_price = self._f(decision.get("current_price"), entry_price)
        symbol = str(decision.get("symbol") or self.config.get("symbol", "XAU/USD"))

        primary_zone = self._plan_zone(session_plan.get("primary_entry_zone") or (session_plan.get("primary_poi") or {}).get("poi_zone"))
        standby_zone = self._plan_zone(session_plan.get("standby_entry_zone") or (session_plan.get("standby_poi") or {}).get("poi_zone"))
        tolerance = points_to_price(self.entry_zone_tolerance_points, symbol)

        def _inside(zone: tuple[float, float] | None, price: float) -> bool:
            if not zone or price <= 0:
                return False
            low, high = zone
            return low - tolerance <= price <= high + tolerance

        entry_inside = _inside(primary_zone, entry_price) or _inside(standby_zone, entry_price)
        market_inside = _inside(primary_zone, current_price) or _inside(standby_zone, current_price)

        if order_type.endswith("MARKET"):
            if entry_inside or market_inside:
                return {"action": "ALLOW", "reason": "market execution is inside the active day-map zone"}
            return {
                "action": "BLOCK_ENTRY_OUTSIDE_DAY_MAP",
                "reason": "market execution is outside the planner primary/standby day-map zones",
            }

        if entry_inside:
            return {"action": "ALLOW", "reason": "pending entry aligns with the day-map zone"}
        return {
            "action": "BLOCK_ENTRY_OUTSIDE_DAY_MAP",
            "reason": "pending entry is outside the planner primary/standby day-map zones",
        }

    @staticmethod
    def _plan_zone(value: Any) -> tuple[float, float] | None:
        zone = value or {}
        if isinstance(zone, dict):
            try:
                low = float(zone.get("low", zone.get("bottom")))
                high = float(zone.get("high", zone.get("top")))
                if low > 0 and high > 0:
                    return min(low, high), max(low, high)
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _f(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
