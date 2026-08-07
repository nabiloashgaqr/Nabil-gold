"""Risk Management Agent.

يحسب وقف الخسارة، الأهداف، R:R، وحجم الصفقة الاختياري، ويطبق فلاتر الحماية:
ATR، السبريد، الحد الأقصى للصفقات المفتوحة، الخسائر المتتالية، عرض الوقف،
قرب الهدف، ونسبة العائد إلى المخاطرة.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from agents.base_agent import BaseAgent
from utils.helpers import calculate_pips, load_config, get_agent_weights
from utils.instruments import points_to_price, price_to_points
from services.moment_quality import MomentQualityService


#: Target methods whose reward ratio is a restatement of the stop distance
#: rather than a measurement of the market. ``rr_from_floored_sl`` rebuilds
#: both targets from the floored stop; ``atr_targets`` is the same idea one
#: step earlier -- entry +/- ATR multiples, with no level consulted. A ratio
#: produced this way carries no information about reward and must not be
#: scored as if it did. See ``_trade_risk_profile``.
_STOP_DERIVED_TARGET_METHODS = ("rr_from_floored_sl", "atr_targets")


def _safe_moment(moment: Dict[str, Any]) -> float:
    """Read the multiplier defensively; sizing must survive a bad payload."""
    try:
        value = float((moment or {}).get("multiplier", 1.0))
    except (TypeError, ValueError):
        return 1.0
    if value <= 0 or value > 1.0:
        return 1.0
    return value

class RiskManagementAgent(BaseAgent):
    """Evaluate risk parameters and approve/reject a potential trade."""

    name = "risk_management"

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        super().__init__(config or load_config())
        self.settings = self.config.get("risk_settings", {})
        self.filters = self.config.get("filters", {})
        self.weights = get_agent_weights(self.config)
        self.symbol = self.config.get("symbol", "XAU/USD")

    def evaluate(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a setup returned by analytical agents."""
        try:
            current_price = self._f(results.get("current_price"))
            direction, direction_details = self._consensus_direction(results)
            if current_price <= 0 or direction == "NEUTRAL":
                return self._rejected("No clear direction", current_price, direction_details=direction_details)

            atr = self._extract_atr(results)
            support_levels, resistance_levels = self._collect_levels(results, current_price)
            smc = results.get("smc", {}) or {}
            smc_suggestion = smc.get("entry_suggestion", {}) or {}
            liquidity_map = smc.get("liquidity", {}) or {}
            portfolio = results.get("portfolio", {}) or {}
            management_profile = self._infer_management_profile(results, direction)

            entry_price, entry_kind, entry_basis, entry_zone = self._smart_entry(
                direction, current_price, atr, smc_suggestion, support_levels, resistance_levels,
                results=results,
            )
            if entry_kind == "WAIT_FOR_LEVEL":
                return self._rejected(entry_basis, current_price, direction_details=direction_details)
            stop_loss, sl_method, buffer = self._stop_loss(direction, entry_price, atr, support_levels, resistance_levels, smc_suggestion, results, management_profile)
            # When the entry is a ZONE, the stop must sit BEHIND the zone's far
            # (distal) edge + buffer — otherwise the SL could fall inside the
            # zone and get clipped by the very wick that fills the order.
            if entry_zone:
                distal = self._f(entry_zone.get("distal"))
                if distal > 0:
                    zone_buffer = max(buffer, atr * 0.10, 0.30)
                    if direction == "BUY":
                        stop_loss = min(stop_loss, distal - zone_buffer)
                    else:
                        stop_loss = max(stop_loss, distal + zone_buffer)
                    sl_method = f"{sl_method}+behind_zone"
            tp1, tp2, tp3, target_method, target_map = self._take_profits(
                direction,
                entry_price,
                atr,
                support_levels,
                resistance_levels,
                liquidity_map,
                management_profile,
            )

            # Gold can move 50-100+ points within seconds; a too-tight
            # ATR-based stop gets clipped by ordinary noise/spread rather than
            # an actual reversal. min_sl_distance_points sets a floor on how
            # close SL may sit to entry. When the floor widens the stop,
            # TP1/TP2/TP3 are rescaled from the SAME R:R ratios implied by the
            # configured ATR multipliers (tp_mult/sl_mult) applied to the new,
            # wider stop distance - otherwise R:R would shrink and min_rr_ratio
            # would start rejecting trades purely because SL got floored.
            #
            # ONE FLOOR, BOTH DOORS.
            #
            # `dynamic_sl_floor` was added to stop the flat 400 from setting
            # the risk on every gold plan, and `_planner_trade_levels` in
            # scripts/run_analysis.py honours it. This agent did not: it read
            # `min_sl_distance_points` raw, so the CONSENSUS/two-agent route
            # -- the route that actually builds the shipped order at
            # run_analysis.py:3894 -- kept flooring every stop to the full
            # 400 while the map and the planner priced the same leg at 150.
            #
            # Measured on the real 2026-08-03 signal 2f72579f (SELL 4037.48,
            # zone 4034.48-4040.48): structural stop 33-50 pts, this door
            # shipped 400 pts, the other door returns 150 pts.
            #
            # The consequence is the whole liquidity map being unreachable.
            # Against a 400-pt stop the analyst's own levels score
            # 4028.20=0.23R, 4022.31=0.38R, 4020.00=0.44R, 4000.00=0.94R --
            # every one below min_rr_ratio 1.5, so the mapped target is
            # refused and the ratio fallback ships the -400/+500/+900
            # signature. Against the scaled floor 4000.00 is 2.50R and the
            # chain has something real to aim at.
            #
            # No risk setting is changed here: min_sl_distance_points stays
            # 400 and remains the ceiling (dynamic_sl_floor.max_points), and
            # min_rr_ratio is untouched. This only makes the second door read
            # the floor the first door already uses.
            # Operator directive (2026-08-07b) — the liquidity rule for stops:
            # liquidity closer than min_liquidity_points (200) is swept noise:
            # ignore it and look farther. The stop sits safety_buffer_points
            # (70) beyond the first eligible level; if that level is past
            # max_stop_points (400), or nothing qualifies, the stop ships the
            # 400 cap directly. The permitted band is [270, 400] points.
            # Targets are HYBRID (operator choice, same day): the liquidity
            # chain keeps the map when it clears min_rr_ratio against this
            # stop; otherwise the targets are multiples of the stop itself.
            min_sl_points = self._f(self.settings.get("min_sl_distance_points"), 0.0)
            structural_points = abs(price_to_points(entry_price - stop_loss, self.symbol))
            rule_cfg = self.settings.get("stop_from_liquidity") or {}
            rule_active = bool(rule_cfg.get("enabled", False))
            if rule_active:
                structural_points = self._stop_from_liquidity_points(
                    direction, entry_price, liquidity_map, rule_cfg)
                sl_method = "liquidity_rule_200_70_400"
            min_sl_distance = points_to_price(
                structural_points if rule_active else min_sl_points, self.symbol)
            if rule_active:
                stop_loss = (entry_price - min_sl_distance) if direction == "BUY" \
                    else (entry_price + min_sl_distance)

            if min_sl_distance > 0 and (rule_active or abs(entry_price - stop_loss) < min_sl_distance):
                sl_mult = self._f(self.settings.get("atr_multiplier_sl"), 2.0) or 2.0
                tp1_ratio = self._f(self.settings.get("atr_multiplier_tp1"), 2.5) / sl_mult
                tp2_ratio = self._f(self.settings.get("atr_multiplier_tp2"), 4.5) / sl_mult
                tp3_ratio = max(tp2_ratio + 1.0, tp2_ratio * 1.2)
                if direction == "BUY":
                    stop_loss = entry_price - min_sl_distance
                else:
                    stop_loss = entry_price + min_sl_distance
                if not rule_active:
                    sl_method = f"{sl_method}+min_floor"

                # Widening the stop must not delete the map.
                #
                # This block used to rebuild BOTH targets from the floored
                # stop: tp1 = floor x 1.25, tp2 = floor x 2.25. Because the
                # floor is a flat 400 on XAU, every floored plan shipped the
                # identical geometry -400/+500/+900 and the liquidity the
                # analysis had identified was discarded. Measured over 300
                # cycles: 100% of orders written under the current floor
                # carried stop-derived targets, and in 35 of 35 recorded cases
                # the mapped objective was nearer than the shipped TP2 -- the
                # order was aiming somewhere the analysis never pointed.
                #
                # The floor is a statement about RISK: how close a stop may
                # sit before noise takes it. It says nothing about where price
                # is going. So the stop is still floored, and the targets are
                # kept on the levels price is actually drawn to.
                #
                # The ratio rebuild survives only as a fallback for when the
                # map offers nothing usable, so a plan is never left with
                # targets too close to clear min_rr_ratio.
                chained_tp1, chained_tp2, chain_method = self._liquidity_chain_targets(
                    direction=direction,
                    entry=entry_price,
                    stop_loss=stop_loss,
                    liquidity_map=liquidity_map,
                    supports=support_levels,
                    resistances=resistance_levels,
                    atr=atr,
                    # The distance structure actually argues for, before the
                    # noise floor padded it. Used only as a fallback when the
                    # padded risk vetoes every mapped level.
                    structural_risk=points_to_price(structural_points, self.symbol),
                )
                if chained_tp1 is not None and chained_tp2 is not None:
                    tp1, tp2 = chained_tp1, chained_tp2
                    tp3 = max(tp2 + atr, tp2) if direction == "BUY" else min(tp2 - atr, tp2)
                    target_method = chain_method
                    target_map.update({
                        "tp1_basis": "liquidity_after_floor",
                        "tp2_basis": "liquidity_after_floor",
                        "floored_stop_kept_map": True,
                    })
                else:
                    if direction == "BUY":
                        tp1 = entry_price + min_sl_distance * tp1_ratio
                        tp2 = entry_price + min_sl_distance * tp2_ratio
                        tp3 = entry_price + min_sl_distance * tp3_ratio
                    else:
                        tp1 = entry_price - min_sl_distance * tp1_ratio
                        tp2 = entry_price - min_sl_distance * tp2_ratio
                        tp3 = entry_price - min_sl_distance * tp3_ratio
                    target_method = "rr_from_floored_sl"
                    target_map.update({"tp1_basis": "rr_fallback", "tp2_basis": "rr_fallback"})

            # 2026-08-07c: the max_rr cap is gone -- the operator's rule is
            # "compare liquidity x ratio and ship the farther objective";
            # capping would veto the far pools the rule exists to aim at.
            risk_distance = abs(entry_price - stop_loss)
            tp1_distance = abs(tp1 - entry_price)
            tp2_distance = abs(tp2 - entry_price)
            rr_tp1 = tp1_distance / risk_distance if risk_distance else 0.0
            rr_tp2 = tp2_distance / risk_distance if risk_distance else 0.0
            rr_tp3 = abs(tp3 - entry_price) / risk_distance if risk_distance else 0.0

            checks = self._run_filters(
                atr=atr,
                spread_points=results.get("spread_points"),
                risk_distance=risk_distance,
                tp1_distance=tp1_distance,
                rr_tp2=rr_tp2,
                portfolio=portfolio,
            )
            risk_profile = self._trade_risk_profile(
                rr_tp2=rr_tp2,
                risk_distance=risk_distance,
                atr=atr,
                direction=direction,
                direction_details=direction_details,
                results=results,
                checks=checks,
                target_method=target_method,
            )
            checks["trade_grade_filter"] = risk_profile["grade"] not in {"D", "F"}
            # 2026-08-07b (operator chose HYBRID): stop-derived targets are a
            # designed fallback for maps that cannot pay the honest wide stop
            # (270-400 pts), not a defect -- so they no longer veto approval.
            # They still earn no R:R score points (_trade_risk_profile), so a
            # ratio can never BUY a grade; the 16:41 protection now lives in
            # the scoring, not in a blanket refusal.
            approved = all(checks.values())
            rejection_reason = None if approved else self._first_failed_reason(checks)
            # Grade sizes the setup; moment quality sizes the conditions it is
            # being taken in. A dead Asia range and the London open are not the
            # same trade even with an identical structure. Sizing only -- this
            # can never open or refuse a position, because a sizing input with
            # veto power becomes an untested second admission gate.
            moment = MomentQualityService(self.config).review(results)
            combined_multiplier = risk_profile["risk_multiplier"] * _safe_moment(moment)
            position_size = self._position_size(
                entry_price, stop_loss, risk_multiplier=combined_multiplier
            )

            return {
                "agent": self.name,
                "approved": approved,
                "rejection_reason": rejection_reason,
                "direction": direction,
                "direction_details": direction_details,
                "entry": {
                    "price": round(entry_price, 2),
                    # Entry ZONE: the order fills when price touches entry_price
                    # (the zone MIDPOINT). low/high are the zone edges; the SL is
                    # placed behind the distal edge (see above).
                    #
                    # The published edges respect
                    # session_planner.min_entry_zone_width_points. That floor
                    # exists because the order rests at ONE price inside the
                    # area: on 2026-07-30 a BUY zone was touched 21 points from
                    # the reference entry without filling, and price then ran
                    # through TP1.
                    #
                    # run_analysis applies it on the planner-ladder path, but
                    # this agent builds the signal for the consensus and
                    # dual-agent paths and read the raw POI instead. On
                    # 2026-08-03 trade bdde9a5f published "Entry zone
                    # 4063.55 - 4067.02" -- 34.7 points against a 60-point
                    # floor. Same setting, same intent, one path honouring it.
                    #
                    # Widening is symmetric around the reference entry, so the
                    # mapped price keeps its place and no risk moves:
                    # zone_touch_activation carries the stop the same distance
                    # it moves the entry.
                    "zone": self._entry_zone_with_floor(
                        entry_zone, entry_price=entry_price, atr=atr
                    ),
                    # Smart execution metadata (see _smart_entry / _classify_order):
                    #   kind        -> MARKET / LIMIT / STOP (human concept)
                    #   order_type  -> BUY_MARKET / SELL_LIMIT / ... (broker style)
                    #   basis       -> short text explaining the entry choice
                    #   current_price -> market price at evaluation time
                    "kind": entry_kind,
                    "order_type": self._classify_order(direction, entry_price, current_price),
                    "basis": entry_basis,
                    "current_price": round(current_price, 2),
                    "distance_points": abs(calculate_pips(current_price, entry_price, direction, self.symbol)) if entry_price != current_price else 0.0,
                },
                "stop_loss": {
                    "price": round(stop_loss, 2),
                    "distance_points": abs(calculate_pips(entry_price, stop_loss, direction, self.symbol)),
                    "method": sl_method,
                    "buffer_added": round(buffer, 2),
                },
                "take_profit": {
                    "tp1": {"price": round(tp1, 2), "distance_points": abs(calculate_pips(entry_price, tp1, direction, self.symbol)), "rr_ratio": round(rr_tp1, 2)},
                    "tp2": {"price": round(tp2, 2), "distance_points": abs(calculate_pips(entry_price, tp2, direction, self.symbol)), "rr_ratio": round(rr_tp2, 2)},
                    "tp3": {"price": round(tp3, 2), "distance_points": abs(calculate_pips(entry_price, tp3, direction, self.symbol)), "rr_ratio": round(rr_tp3, 2)},
                },
                "risk_metrics": {
                    "atr": round(atr, 2),
                    "risk_distance_price": round(risk_distance, 2),
                    "tp1_distance_price": round(tp1_distance, 2),
                    "tp2_distance_price": round(tp2_distance, 2),
                    "target_method": target_method,
                    # The floor that was actually applied on this path, so a
                    # later audit can tell a scaled floor from the flat one
                    # without re-deriving it.
                    "min_sl_distance_points": round(min_sl_points, 1),
                    "structural_sl_points": round(structural_points, 1),
                    "max_rr_ratio": self._f(self.settings.get("max_rr_ratio"), 4.0),
                    "checks": checks,
                    "portfolio": portfolio,
                    "trade_grade": risk_profile,
                    "risk_multiplier": risk_profile["risk_multiplier"],
                    "management_profile": management_profile,
                    "target_map": target_map,
                },
                "trade_grade": risk_profile,
                "management_profile": management_profile,
                "target_map": target_map,
                "position_size": position_size,
                "trailing_stop": {"activate_at": "TP1", "move_sl_to": "entry", "trail_distance": round(max(price_to_points(atr, self.symbol), 10), 1)},
                "summary": self._summary(approved, rejection_reason, stop_loss, tp1, tp2, rr_tp2),
            }
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Risk evaluation failed")
            return self._rejected(f"Risk error: {exc}", self._f(results.get("current_price")))

    def _consensus_direction(self, results: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        score = 0.0
        buy_count = 0
        sell_count = 0
        details: Dict[str, Any] = {}
        for agent in ["technical", "classical", "smc", "price_action", "multitimeframe"]:
            result = results.get(agent, {}) or {}
            direction = str(result.get("direction", result.get("signal", "NEUTRAL"))).upper()
            confidence = max(0.0, min(100.0, self._f(result.get("confidence"))))
            weight = self._f(self.weights.get(agent), 0.0)
            multiplier = 1 if direction == "BUY" else -1 if direction == "SELL" else 0
            agent_score = confidence * weight * multiplier
            score += agent_score
            if direction == "BUY":
                buy_count += 1
            elif direction == "SELL":
                sell_count += 1
            details[agent] = {"direction": direction, "confidence": confidence, "weight": weight, "score": round(agent_score, 2)}

        # FIX: prioritise weighted score direction, then use agent count as
        # tiebreaker. The old logic could pick the WRONG direction when
        # score > 0 but sell_count > buy_count (or vice-versa), producing
        # an inverted risk-management evaluation.
        if score > 0:
            direction = "BUY"
        elif score < 0:
            direction = "SELL"
        elif buy_count > sell_count:
            direction = "BUY"
        elif sell_count > buy_count:
            direction = "SELL"
        else:
            direction = "NEUTRAL"
        return direction, {"weighted_score": round(score, 2), "buy_count": buy_count, "sell_count": sell_count, "agents": details}

    def _extract_atr(self, results: Dict[str, Any]) -> float:
        """Extract ATR robustly from all known locations before using fallback."""
        candidates = [
            results.get("atr"),
            results.get("indicators", {}).get("atr") if isinstance(results.get("indicators"), dict) else None,
            results.get("technical", {}).get("indicators_raw", {}).get("atr"),
            results.get("technical", {}).get("technical", {}).get("indicators_raw", {}).get("atr"),
            results.get("technical", {}).get("technical", {}).get("atr"),
            results.get("technical", {}).get("atr"),
            results.get("risk", {}).get("risk_metrics", {}).get("atr") if isinstance(results.get("risk"), dict) else None,
        ]
        for payload in (results.get("timeframes", {}) or {}).values() if isinstance(results.get("timeframes"), dict) else []:
            if isinstance(payload, dict):
                candidates.append(payload.get("atr"))
                indicators = payload.get("indicators", {}) or {}
                if isinstance(indicators, dict):
                    candidates.append(indicators.get("atr"))
        for candidate in candidates:
            atr = self._f(candidate, 0.0)
            if atr > 0:
                return atr
        # Conservative fallback for gold when indicator is unavailable.
        return self._f(self.settings.get("fallback_atr"), 1.5)

    def _collect_levels(self, results: Dict[str, Any], current_price: float) -> Tuple[List[float], List[float]]:
        """Collect support/resistance from technical, classical, SMC, and raw fields."""
        supports: List[float] = []
        resistances: List[float] = []

        def add_support(value: Any) -> None:
            v = self._f(value, 0.0)
            if v > 0:
                supports.append(v)

        def add_resistance(value: Any) -> None:
            v = self._f(value, 0.0)
            if v > 0:
                resistances.append(v)

        for key in ("support", "nearest_support"):
            add_support(results.get(key))
        for key in ("resistance", "nearest_resistance"):
            add_resistance(results.get(key))

        tech = results.get("technical", {}) or {}
        tech_levels = tech.get("key_levels", {}) or {}
        add_support(tech_levels.get("nearest_support"))
        add_resistance(tech_levels.get("nearest_resistance"))
        tech_nested = tech.get("technical", {}) or {}
        add_support(tech_nested.get("support"))
        add_resistance(tech_nested.get("resistance"))
        nested_levels = tech_nested.get("key_levels", {}) or {}
        add_support(nested_levels.get("nearest_support"))
        add_resistance(nested_levels.get("nearest_resistance"))

        classical = results.get("classical", {}) or {}
        supports.extend(self._f(x) for x in classical.get("support_levels", []) if self._f(x) > 0)
        resistances.extend(self._f(x) for x in classical.get("resistance_levels", []) if self._f(x) > 0)

        smc = results.get("smc", {}) or {}
        dealing_range = smc.get("dealing_range", {}) or {}
        add_support(dealing_range.get("low"))
        add_resistance(dealing_range.get("high"))
        liquidity = smc.get("liquidity", {}) or {}
        supports.extend(self._f(x) for x in liquidity.get("sell_side", []) if self._f(x) > 0)
        resistances.extend(self._f(x) for x in liquidity.get("buy_side", []) if self._f(x) > 0)

        # Deduplicate and keep logical side levels.
        supports = sorted({round(x, 2) for x in supports if x < current_price}, reverse=True)
        resistances = sorted({round(x, 2) for x in resistances if x > current_price})
        return supports, resistances

    def _classify_order(self, direction: str, entry: float, current_price: float | None) -> str:
        """Broker-style order classification from entry vs current price.

        BUY  below price -> BUY_LIMIT ; above price -> BUY_STOP
        SELL above price -> SELL_LIMIT; below price -> SELL_STOP
        within threshold  -> *_MARKET

        Respects entry_style config:
          - "market" / "fixed_risk":  always *_MARKET (no pending orders).
          - "smart":   uses pending_threshold_points.
          - "hybrid":  uses market_threshold_points.
        """
        oe = self.config.get("order_execution", {}) or {}
        entry_style = str(oe.get("entry_style", "market")).lower()

        # market and fixed_risk -> always MARKET entry
        if entry_style in ("market", "fixed_risk"):
            return f"{direction}_MARKET"

        try:
            entry = float(entry)
            current = float(current_price if current_price is not None else entry)
        except (TypeError, ValueError):
            return f"{direction}_MARKET"

        if entry_style == "hybrid":
            threshold = points_to_price(self._f(oe.get("market_threshold_points", 30), 30), self.symbol)
            if abs(entry - current) <= max(threshold, 0.01):
                return f"{direction}_MARKET"
        else:
            # smart mode
            threshold = points_to_price(self._f(oe.get("pending_threshold_points", 1.0), 1.0), self.symbol)
            if abs(entry - current) <= max(threshold, 0.01):
                return f"{direction}_MARKET"

        # Entries are MARKET or LIMIT. Never STOP -- see the note in
        # scripts/run_analysis.py::_planned_order_type.
        if direction == "BUY":
            return "BUY_LIMIT" if entry < current else "BUY_MARKET"
        if direction == "SELL":
            return "SELL_LIMIT" if entry > current else "SELL_MARKET"
        return f"{direction}_MARKET"

    def _smart_entry(
        self,
        direction: str,
        current_price: float,
        atr: float,
        smc_suggestion: Dict[str, Any],
        support_levels: List[float],
        resistance_levels: List[float],
        results: Dict[str, Any] | None = None,
    ) -> Tuple[float, str, str, Dict[str, Any]]:
        """Decide smart entry based on entry_style.

        Returns (entry_price, kind, basis, zone).

        Fixed-Risk mode (fixed_risk):
          - Find the NEAREST key level in the trade direction.
            For SELL: nearest resistance above price.
            For BUY:  nearest support below price.
          - Calculate distance from current_price to that level (in points).
          - If distance <= max_risk_distance_points (e.g. 300):
              -> MARKET entry at current_price.
              -> SL is placed just beyond the level + buffer.
              -> kind = "MARKET", basis explains the level.
          - If distance > max_risk_distance_points:
              -> kind = "WAIT_FOR_LEVEL", entry at current_price (won't be used).
              -> The evaluate() method will reject the trade with a clear reason.
              -> Next analysis cycle (10 min) will re-check.

        Market mode (market):
          - Always MARKET at current_price.

        Smart/hybrid modes:
          - Uses old logic: SMC order blocks, support/resistance levels, LIMIT/STOP.
        """
        oe = self.config.get("order_execution", {}) or {}
        entry_style = str(oe.get("entry_style", "market")).lower()
        se = oe.get("smart_entry", {}) or {}
        results = results or {}

        def _market(reason: str = "Immediate market entry") -> Tuple[float, str, str, Dict[str, Any]]:
            z = {"low": round(current_price, 2), "high": round(current_price, 2),
                 "proximal": round(current_price, 2), "distal": round(current_price, 2),
                 "fill_at": "market", "source": "market"}
            return round(current_price, 2), "MARKET", reason, z

        def _wait(reason: str) -> Tuple[float, str, str, Dict[str, Any]]:
            z = {"low": round(current_price, 2), "high": round(current_price, 2),
                 "proximal": round(current_price, 2), "distal": round(current_price, 2),
                 "fill_at": "market", "source": "wait"}
            return round(current_price, 2), "WAIT_FOR_LEVEL", reason, z

        # ── market mode ─────────────────────────────────────────────────
        if entry_style == "market":
            return _market("Market entry (entry_style=market)")

        # ── fixed_risk mode ─────────────────────────────────────────────
        if entry_style == "fixed_risk":
            fr = oe.get("fixed_risk", {}) or {}
            max_risk_points = int(fr.get("max_risk_distance_points", 300) or 300)

            to_price = lambda p: points_to_price(p, self.symbol)

            if direction == "SELL":
                # Find nearest resistance above current price
                best_level = None
                best_distance = None
                for lvl in resistance_levels:
                    if lvl > current_price:
                        d = lvl - current_price
                        if best_distance is None or d < best_distance:
                            best_distance = d
                            best_level = lvl
                # Also check SMC bearish order blocks
                smc = results.get("smc", {}) or {}
                for ob in (smc.get("order_blocks", []) or []):
                    if str(ob.get("type", "")).lower() == "bearish":
                        z = ob.get("zone", {}) or {}
                        top = self._f(z.get("top"))
                        bottom = self._f(z.get("bottom"))
                        zone_high = max(top, bottom)
                        if zone_high > current_price:
                            d = zone_high - current_price
                            if best_distance is None or d < best_distance:
                                best_distance = d
                                best_level = zone_high

                if best_level is not None and best_distance is not None:
                    dist_points = abs(price_to_points(best_distance, self.symbol))
                    if dist_points <= max_risk_points:
                        sl_price = best_level + (points_to_price(fr.get("sl_buffer_points", 20), self.symbol))
                        return _market(
                            f"SELL fixed_risk: resistance at {best_level:.2f} "
                            f"({dist_points:.0f}pts away ≤ {max_risk_points}pts). "
                            f"SL@{sl_price:.2f} (above level+buffer)"
                        )
                    else:
                        target_price = best_level - to_price(max_risk_points)
                        return _wait(
                            f"SELL waiting: resistance at {best_level:.2f} is "
                            f"{dist_points:.0f}pts away > {max_risk_points}pts. "
                            f"Will enter when price rises to ~{target_price:.2f}"
                        )
                return _wait("SELL waiting: no resistance level found above price")

            else:  # BUY
                best_level = None
                best_distance = None
                for lvl in support_levels:
                    if lvl < current_price:
                        d = current_price - lvl
                        if best_distance is None or d < best_distance:
                            best_distance = d
                            best_level = lvl
                # SMC bullish order blocks
                smc = results.get("smc", {}) or {}
                for ob in (smc.get("order_blocks", []) or []):
                    if str(ob.get("type", "")).lower() == "bullish":
                        z = ob.get("zone", {}) or {}
                        top = self._f(z.get("top"))
                        bottom = self._f(z.get("bottom"))
                        zone_low = min(top, bottom)
                        if zone_low < current_price:
                            d = current_price - zone_low
                            if best_distance is None or d < best_distance:
                                best_distance = d
                                best_level = zone_low

                if best_level is not None and best_distance is not None:
                    dist_points = abs(price_to_points(best_distance, self.symbol))
                    if dist_points <= max_risk_points:
                        sl_price = best_level - (points_to_price(fr.get("sl_buffer_points", 20), self.symbol))
                        return _market(
                            f"BUY fixed_risk: support at {best_level:.2f} "
                            f"({dist_points:.0f}pts away ≤ {max_risk_points}pts). "
                            f"SL@{sl_price:.2f} (below level+buffer)"
                        )
                    else:
                        target_price = best_level + to_price(max_risk_points)
                        return _wait(
                            f"BUY waiting: support at {best_level:.2f} is "
                            f"{dist_points:.0f}pts away > {max_risk_points}pts. "
                            f"Will enter when price falls to ~{target_price:.2f}"
                        )
                return _wait("BUY waiting: no support level found below price")

        # ── smart / hybrid modes (existing logic) ────────────────────────
        enabled = bool(se.get("enabled", True))
        fill_at = str(se.get("fill_at", "mid")).lower()
        zone_width = points_to_price(self._f(se.get("zone_width_points", 50), 50), self.symbol)
        min_pts = points_to_price(self._f(se.get("min_pullback_points", 60), 60), self.symbol)
        max_pts = points_to_price(self._f(se.get("max_pullback_points", 350), 350), self.symbol)

        if not enabled:
            return _market("Smart entry disabled")

        def _build_zone(proximal, distal, source, basis, kind):
            low, high = min(proximal, distal), max(proximal, distal)
            if fill_at == "edge":
                entry = proximal
            elif fill_at == "far":
                entry = distal
            else:
                entry = (proximal + distal) / 2.0
            zone = {"low": round(low, 2), "high": round(high, 2),
                    "proximal": round(proximal, 2), "distal": round(distal, 2),
                    "fill_at": fill_at, "source": source}
            return round(entry, 2), kind, basis, zone

        smc = results.get("smc", {}) or {}
        setup_structure = smc.get("setup_structure", {}) or {}
        preferred_zone = setup_structure.get("poi_zone") or {}
        trigger_state = str(setup_structure.get("trigger_state") or "")
        trigger_ready = bool(setup_structure.get("trigger_ready", False))
        trigger_score = self._f(setup_structure.get("trigger_score"), 0.0)
        execution_hint = str(setup_structure.get("execution_hint") or "LIMIT").upper()

        if entry_style == "hybrid":
            market_threshold = points_to_price(self._f(oe.get("market_threshold_points", 30), 30), self.symbol)
            if preferred_zone and trigger_ready and execution_hint == "MARKET":
                zone_mid = (self._f(preferred_zone.get("top")) + self._f(preferred_zone.get("bottom"))) / 2.0
                if zone_mid > 0 and abs(zone_mid - current_price) <= max(market_threshold, 0.01):
                    return _market(
                        f"SMC trigger confirmed at ranked POI ({trigger_state}, score {trigger_score:.0f})"
                    )
            order_blocks = smc.get("order_blocks", []) or []
            want_type = "bullish" if direction == "BUY" else "bearish"
            for ob in reversed(order_blocks):
                if str(ob.get("type", "")).lower() != want_type:
                    continue
                z = ob.get("zone", {}) or {}
                top = self._f(z.get("top"))
                bottom = self._f(z.get("bottom"))
                if top <= 0 or bottom <= 0:
                    continue
                if direction == "BUY":
                    if top >= current_price:
                        continue
                    proximity = current_price - top
                else:
                    if bottom <= current_price:
                        continue
                    proximity = bottom - current_price
                if proximity <= market_threshold:
                    return _market(f"Price at SMC zone (within {market_threshold*10:.0f}pts)")
            if direction == "BUY":
                for lvl in support_levels:
                    proximity = current_price - lvl
                    if 0 < proximity <= market_threshold:
                        return _market(f"Price at support (within {market_threshold*10:.0f}pts)")
            else:
                for lvl in resistance_levels:
                    proximity = lvl - current_price
                    if 0 < proximity <= market_threshold:
                        return _market(f"Price at resistance (within {market_threshold*10:.0f}pts)")

        # Preferred ranked SMC POI from setup_structure
        if preferred_zone:
            top = self._f(preferred_zone.get("top"))
            bottom = self._f(preferred_zone.get("bottom"))
            if top > 0 and bottom > 0:
                if direction == "BUY" and top < current_price:
                    proximal, distal = top, bottom
                    dist = abs(proximal - current_price)
                    if dist <= max_pts:
                        return _build_zone(proximal, distal, "smc_ranked", f"Ranked {setup_structure.get('poi_type', 'SMC')} zone ({trigger_state or 'await trigger'})", "LIMIT")
                elif direction == "SELL" and bottom > current_price:
                    proximal, distal = bottom, top
                    dist = abs(proximal - current_price)
                    if dist <= max_pts:
                        return _build_zone(proximal, distal, "smc_ranked", f"Ranked {setup_structure.get('poi_type', 'SMC')} zone ({trigger_state or 'await trigger'})", "LIMIT")

        # SMC order blocks
        order_blocks = smc.get("order_blocks", []) or []
        want_type = "bullish" if direction == "BUY" else "bearish"
        obs = [ob for ob in order_blocks if str(ob.get("type", "")).lower() == want_type]
        for ob in reversed(obs):
            z = ob.get("zone", {}) or {}
            top = self._f(z.get("top"))
            bottom = self._f(z.get("bottom"))
            if top <= 0 or bottom <= 0:
                continue
            if direction == "BUY":
                if top >= current_price:
                    continue
                proximal, distal = top, bottom
            else:
                if bottom <= current_price:
                    continue
                proximal, distal = bottom, top
            dist = abs(proximal - current_price)
            if dist > max_pts:
                continue
            kind = "LIMIT"
            return _build_zone(proximal, distal, "smc", "SMC order block zone", kind)

        # Support/resistance pullback
        half = max(zone_width / 2.0, 0.10)
        if direction == "BUY":
            belows = [s for s in support_levels if 0 < s < current_price]
            for lvl in sorted(belows, reverse=True):
                if min_pts <= (current_price - lvl) <= max_pts:
                    return _build_zone(lvl + half, lvl - half, "level", "Buy zone at nearest support", "LIMIT")
        else:
            aboves = [r for r in resistance_levels if r > current_price]
            for lvl in sorted(aboves):
                if min_pts <= (lvl - current_price) <= max_pts:
                    return _build_zone(lvl - half, lvl + half, "level", "Sell zone at nearest resistance", "LIMIT")

        return _market("No pullback zone nearby")
    def _infer_management_profile(self, results: Dict[str, Any], direction: str) -> str:
        smc = results.get("smc", {}) or {}
        smc_structure = smc.get("setup_structure") or {}
        setup_type = str(
            (results.get("setup_context") or {}).get("setup_type")
            or smc_structure.get("setup_type")
            or (results.get("multitimeframe", {}) or {}).get("setup_type")
            or ""
        ).upper()
        if setup_type in {"LIQUIDITY_REVERSAL", "REVERSAL_ATTEMPT"}:
            return "reversal_profile"
        if setup_type in {"ORDER_BLOCK_PULLBACK", "STRUCTURE_CONTINUATION", "TREND_CONTINUATION", "PULLBACK_ENTRY"}:
            return "continuation_profile"
        if setup_type in {"RANGE_FADE", "SMC_CONTEXT", "MIXED_ALIGNMENT"}:
            return "range_profile"
        # Fallback from direction + MTF context when setup labels are absent.
        mtf = results.get("multitimeframe", {}) or {}
        if str(mtf.get("timing_state") or "").upper() in {"EARLY", "VALID"} and str(mtf.get("alignment") or "").upper() in {"FULL", "PARTIAL"}:
            return "continuation_profile"
        return "default_profile"

    def _stop_from_liquidity_points(
        self,
        direction: str,
        entry: float,
        liquidity_map: Dict[str, Any],
        rule_cfg: Dict[str, Any],
    ) -> float:
        """Operator directive 2026-08-07b: the stop distance in points.

        Liquidity closer than ``min_liquidity_points`` (200) is swept noise:
        ignored. The stop sits ``safety_buffer_points`` (70) beyond the first
        eligible opposing level; a first level past ``max_stop_points`` (400)
        -- or no eligible level at all -- ships the 400 cap directly.
        Examples: first pool at 350 -> 420 -> capped 400; at 250 -> 320;
        none >= 200 -> 400. Minimum possible stop: 200 + 70 = 270.
        """
        min_liq = self._f(rule_cfg.get("min_liquidity_points"), 200.0)
        buffer = self._f(rule_cfg.get("safety_buffer_points"), 70.0)
        max_stop = self._f(rule_cfg.get("max_stop_points"), 400.0)
        liquidity_map = liquidity_map or {}
        side = "sell_side" if direction == "BUY" else "buy_side"
        distances = []
        for raw in list(liquidity_map.get(side) or []):
            level = self._f(raw, 0.0)
            if level <= 0:
                continue
            on_the_right_side = (level < entry) if direction == "BUY" else (level > entry)
            if on_the_right_side:
                distances.append(abs(price_to_points(entry - level, self.symbol)))
        eligible = sorted(d for d in distances if d >= min_liq)
        if not eligible:
            return max_stop
        first = eligible[0]
        if first > max_stop:
            return max_stop
        return min(first + buffer, max_stop)

    def _stop_loss(
        self,
        direction: str,
        entry: float,
        atr: float,
        supports: List[float],
        resistances: List[float],
        smc_suggestion: Dict[str, Any],
        results: Dict[str, Any],
        management_profile: str = "default_profile",
    ) -> Tuple[float, str, float]:
        sl_mult = self._f(self.settings.get("atr_multiplier_sl"), 2.0)
        buffer = max(0.30, atr * 0.12)
        if management_profile == "reversal_profile":
            buffer = max(buffer, atr * 0.18)
        elif management_profile == "range_profile":
            buffer = max(0.25, atr * 0.10)
        min_distance = max(atr * 0.60, 0.50)
        candidates: List[Tuple[float, str]] = []

        if direction == "BUY":
            candidates.append((entry - atr * sl_mult, "atr_1_5x"))
            if supports:
                candidates.append((supports[0] - buffer, "below_nearest_support"))
            smc_sl = self._f(smc_suggestion.get("sl"), 0.0)
            if smc_sl > 0:
                candidates.append((smc_sl - buffer * 0.25, "smc_order_block_or_sweep"))
            bullish_obs = [ob for ob in results.get("smc", {}).get("order_blocks", []) if ob.get("type") == "bullish"]
            if bullish_obs:
                candidates.append((self._f(bullish_obs[-1].get("zone", {}).get("bottom")) - buffer, "below_bullish_order_block"))
            logical = [(price, method) for price, method in candidates if price < entry and abs(entry - price) >= min_distance]
            if not logical:
                return entry - atr * sl_mult, "atr_fallback", buffer
            if management_profile == "reversal_profile":
                preferred = [item for item in logical if item[1] in {"smc_order_block_or_sweep", "below_bullish_order_block"}]
                if preferred:
                    selected_price, selected_method = min(preferred, key=lambda item: item[0])
                    return selected_price, f"{selected_method}+reversal_profile", buffer
            # Closest logical stop below entry.
            selected_price, selected_method = max(logical, key=lambda item: item[0])
            return selected_price, selected_method, buffer

        candidates.append((entry + atr * sl_mult, "atr_1_5x"))
        if resistances:
            candidates.append((resistances[0] + buffer, "above_nearest_resistance"))
        smc_sl = self._f(smc_suggestion.get("sl"), 0.0)
        if smc_sl > 0:
            candidates.append((smc_sl + buffer * 0.25, "smc_order_block_or_sweep"))
        bearish_obs = [ob for ob in results.get("smc", {}).get("order_blocks", []) if ob.get("type") == "bearish"]
        if bearish_obs:
            candidates.append((self._f(bearish_obs[-1].get("zone", {}).get("top")) + buffer, "above_bearish_order_block"))
        logical = [(price, method) for price, method in candidates if price > entry and abs(entry - price) >= min_distance]
        if not logical:
            return entry + atr * sl_mult, "atr_fallback", buffer
        if management_profile == "reversal_profile":
            preferred = [item for item in logical if item[1] in {"smc_order_block_or_sweep", "above_bearish_order_block"}]
            if preferred:
                selected_price, selected_method = max(preferred, key=lambda item: item[0])
                return selected_price, f"{selected_method}+reversal_profile", buffer
        # Closest logical stop above entry.
        selected_price, selected_method = min(logical, key=lambda item: item[0])
        return selected_price, selected_method, buffer

    def _take_profits(
        self,
        direction: str,
        entry: float,
        atr: float,
        supports: List[float],
        resistances: List[float],
        liquidity_map: Dict[str, Any] | None = None,
        management_profile: str = "default_profile",
    ) -> Tuple[float, float, float, str, Dict[str, Any]]:
        liquidity_map = liquidity_map or {}
        tp1_mult = self._f(self.settings.get("atr_multiplier_tp1"), 2.5)
        tp2_mult = self._f(self.settings.get("atr_multiplier_tp2"), 4.5)
        tp3_mult = 5.0
        if management_profile == "range_profile":
            tp1_mult = min(tp1_mult, 1.8)
            tp2_mult = min(tp2_mult, 3.0)
        min_tp1_distance = max(atr, 0.80)
        method = "atr_targets"
        target_map: Dict[str, Any] = {"profile": management_profile, "tp1_basis": "atr", "tp2_basis": "atr"}
        if direction == "BUY":
            atr_tp1 = entry + atr * tp1_mult
            atr_tp2 = entry + atr * tp2_mult
            liquidity_targets = [self._f(level) for level in liquidity_map.get("buy_side", []) if self._f(level) - entry >= min_tp1_distance]
            valid_res = [level for level in resistances if level - entry >= min_tp1_distance]
            if management_profile == "reversal_profile" and liquidity_targets:
                tp1 = liquidity_targets[0]
                tp2 = liquidity_targets[1] if len(liquidity_targets) > 1 else max(atr_tp2, tp1 + atr * 1.2)
                method = "liquidity_map_reversal"
                target_map.update({"tp1_basis": "internal_liquidity", "tp2_basis": "external_liquidity" if len(liquidity_targets) > 1 else "atr_extension"})
            elif valid_res:
                tp1 = min(valid_res[0], atr_tp1) if valid_res[0] >= entry + min_tp1_distance else atr_tp1
                tp2_candidates = [level for level in valid_res[1:] if level > tp1]
                tp2 = tp2_candidates[0] if tp2_candidates else max(atr_tp2, tp1 + atr * 1.2)
                method = "resistance_and_atr"
                target_map.update({"tp1_basis": "resistance", "tp2_basis": "resistance_or_atr"})
            else:
                tp1, tp2 = atr_tp1, atr_tp2
            tp3 = max(entry + atr * tp3_mult, tp2 + atr)
        else:
            atr_tp1 = entry - atr * tp1_mult
            atr_tp2 = entry - atr * tp2_mult
            liquidity_targets = [self._f(level) for level in liquidity_map.get("sell_side", []) if entry - self._f(level) >= min_tp1_distance]
            liquidity_targets = sorted(liquidity_targets, reverse=True)
            valid_sup = [level for level in supports if entry - level >= min_tp1_distance]
            if management_profile == "reversal_profile" and liquidity_targets:
                tp1 = liquidity_targets[0]
                tp2 = liquidity_targets[1] if len(liquidity_targets) > 1 else min(atr_tp2, tp1 - atr * 1.2)
                method = "liquidity_map_reversal"
                target_map.update({"tp1_basis": "internal_liquidity", "tp2_basis": "external_liquidity" if len(liquidity_targets) > 1 else "atr_extension"})
            elif valid_sup:
                tp1 = max(valid_sup[0], atr_tp1) if valid_sup[0] <= entry - min_tp1_distance else atr_tp1
                tp2_candidates = [level for level in valid_sup[1:] if level < tp1]
                tp2 = tp2_candidates[0] if tp2_candidates else min(atr_tp2, tp1 - atr * 1.2)
                method = "support_and_atr"
                target_map.update({"tp1_basis": "support", "tp2_basis": "support_or_atr"})
            else:
                tp1, tp2 = atr_tp1, atr_tp2
            tp3 = min(entry - atr * tp3_mult, tp2 - atr)
        return tp1, tp2, tp3, method, target_map

    def _entry_zone_with_floor(
        self,
        entry_zone: Dict[str, Any],
        *,
        entry_price: float,
        atr: float,
    ) -> Dict[str, Any]:
        """Publish the entry area at no less than the configured floor.

        Reads ``session_planner.min_entry_zone_width_points`` -- the same
        setting SessionPlannerService enforces -- so both paths describe the
        same map. Set it to 0 to disable widening entirely.

        The area is widened symmetrically around the reference entry, which
        keeps the mapped price where the analysis put it. ``proximal`` and
        ``distal`` are carried with the edges they belong to, so the stop
        (placed behind the distal edge) moves with the zone rather than
        landing inside it.
        """
        default_half = max(0.20, atr * 0.07)
        low = self._f(entry_zone.get("low", entry_price - default_half), entry_price - default_half)
        high = self._f(entry_zone.get("high", entry_price + default_half), entry_price + default_half)
        if high < low:
            low, high = high, low
        proximal = self._f(entry_zone.get("proximal", entry_price), entry_price)
        distal = self._f(entry_zone.get("distal", entry_price), entry_price)

        planner_cfg = (self.config.get("session_planner") or {}) if isinstance(self.config, dict) else {}
        floor_points = self._f(planner_cfg.get("min_entry_zone_width_points"), 0.0)
        widened = False
        if floor_points > 0 and low > 0 and high > 0:
            floor_price = points_to_price(floor_points, self.symbol)
            width = high - low
            if width < floor_price:
                missing = floor_price - width
                anchor = entry_price if low <= entry_price <= high else (low + high) / 2.0
                upper_share = ((high - anchor) / width) if width > 0 else 0.5
                upper_share = min(max(upper_share, 0.0), 1.0)
                new_low = low - missing * (1.0 - upper_share)
                new_high = high + missing * upper_share
                # Keep proximal/distal on the edges they described.
                if abs(proximal - low) < abs(proximal - high):
                    proximal = new_low
                    distal = new_high
                else:
                    proximal = new_high
                    distal = new_low
                low, high, widened = new_low, new_high, True

        payload = {
            "low": round(low, 2),
            "high": round(high, 2),
            "proximal": round(proximal, 2),
            "distal": round(distal, 2),
            "fill_at": entry_zone.get("fill_at", "mid"),
            "source": entry_zone.get("source", "atr"),
        }
        if widened:
            payload["widened_to_min_width"] = True
        return payload

    def _liquidity_chain_targets(
        self,
        *,
        direction: str,
        entry: float,
        stop_loss: float,
        liquidity_map: Dict[str, Any] | None,
        supports: List[float],
        resistances: List[float],
        atr: float,
        structural_risk: float | None = None,
    ) -> Tuple[float | None, float | None, str]:
        """Targets = the FARTHER of (stop ratio, liquidity), per target.

        Operator directive 2026-08-07c: a plan the agents approved is never
        refused on reward; the minimums are enforced BY CONSTRUCTION:
            TP1 = farther(0.8R of the stop, nearest pool ahead)
            TP2 = farther(1.5R of the stop, farthest pool ahead)
        The operator's own example: stop 270 -> ratio TP1 ~216 pts; a pool
        at 250 pts is farther, so TP1 = 250. With no pools the ratios ship,
        labelled stop-derived so they earn no R:R score points.
        """
        risk = abs(entry - stop_loss)
        if risk <= 0 or entry <= 0:
            return None, None, ""
        min_rr = self._f(self.settings.get("min_rr_ratio"), 1.5) or 1.5
        min_tp1_rr = self._f(self.settings.get("min_tp1_rr"), 0.8) or 0.8
        liquidity_map = liquidity_map or {}
        side_key = "buy_side" if direction == "BUY" else "sell_side"
        structure = resistances if direction == "BUY" else supports

        def _ahead(level: float) -> bool:
            return (level > entry) if direction == "BUY" else (level < entry)

        def _dist(level: float) -> float:
            return abs(level - entry)

        levels: List[float] = []
        for raw in list(liquidity_map.get(side_key) or []) + list(structure or []):
            value = self._f(raw, 0.0)
            if value > 0 and _ahead(value):
                levels.append(value)
        ordered = sorted(set(levels), key=_dist)

        def _level(rr_r: float) -> float:
            return entry + risk * rr_r if direction == "BUY" else entry - risk * rr_r

        tp1 = _level(min_tp1_rr)
        tp2 = _level(min_rr)
        used_pool = False
        if ordered:
            if _dist(ordered[0]) > _dist(tp1):
                tp1 = ordered[0]
                used_pool = True
            if _dist(ordered[-1]) > _dist(tp2):
                tp2 = ordered[-1]
                used_pool = True
        if _dist(tp2) <= _dist(tp1):
            tp2 = (tp1 + risk * 0.5) if direction == "BUY" else (tp1 - risk * 0.5)
        method = "liquidity_chain" if used_pool else "rr_from_floored_sl"
        return round(tp1, 2), round(tp2, 2), method

    def _entry_zone_with_floor(
        self,
        entry_zone: Dict[str, Any],
        *,
        entry_price: float,
        atr: float,
    ) -> Dict[str, Any]:
        """Publish the entry area at no less than the configured floor.

        Reads ``session_planner.min_entry_zone_width_points`` -- the same
        setting SessionPlannerService enforces -- so both paths describe the
        same map. Set it to 0 to disable widening entirely.

        The area is widened symmetrically around the reference entry, which
        keeps the mapped price where the analysis put it. ``proximal`` and
        ``distal`` are carried with the edges they belong to, so the stop
        (placed behind the distal edge) moves with the zone rather than
        landing inside it.
        """
        default_half = max(0.20, atr * 0.07)
        low = self._f(entry_zone.get("low", entry_price - default_half), entry_price - default_half)
        high = self._f(entry_zone.get("high", entry_price + default_half), entry_price + default_half)
        if high < low:
            low, high = high, low
        proximal = self._f(entry_zone.get("proximal", entry_price), entry_price)
        distal = self._f(entry_zone.get("distal", entry_price), entry_price)

        planner_cfg = (self.config.get("session_planner") or {}) if isinstance(self.config, dict) else {}
        floor_points = self._f(planner_cfg.get("min_entry_zone_width_points"), 0.0)
        widened = False
        if floor_points > 0 and low > 0 and high > 0:
            floor_price = points_to_price(floor_points, self.symbol)
            width = high - low
            if width < floor_price:
                missing = floor_price - width
                anchor = entry_price if low <= entry_price <= high else (low + high) / 2.0
                upper_share = ((high - anchor) / width) if width > 0 else 0.5
                upper_share = min(max(upper_share, 0.0), 1.0)
                new_low = low - missing * (1.0 - upper_share)
                new_high = high + missing * upper_share
                # Keep proximal/distal on the edges they described.
                if abs(proximal - low) < abs(proximal - high):
                    proximal = new_low
                    distal = new_high
                else:
                    proximal = new_high
                    distal = new_low
                low, high, widened = new_low, new_high, True

        payload = {
            "low": round(low, 2),
            "high": round(high, 2),
            "proximal": round(proximal, 2),
            "distal": round(distal, 2),
            "fill_at": entry_zone.get("fill_at", "mid"),
            "source": entry_zone.get("source", "atr"),
        }
        if widened:
            payload["widened_to_min_width"] = True
        return payload

    def _liquidity_chain_targets(
        self,
        *,
        direction: str,
        entry: float,
        stop_loss: float,
        liquidity_map: Dict[str, Any] | None,
        supports: List[float],
        resistances: List[float],
        atr: float,
        structural_risk: float | None = None,
    ) -> Tuple[float | None, float | None, str]:
        """TP1 and TP2 from the levels price is actually drawn to.

        The nearest pool is where price is heading next; the pool beyond it is
        where the move ends. Taking both is what separates a 456-point trade
        from the 677 the manual analyst booked on the same map: on 2026-07-30
        his chart marked tp-1 at ~4093 and an extended target at 4132.389,
        while the system shipped TP2 at 4093.31 and left 257 points behind.

        Rules, in order:
          * a target must be ahead of entry by at least one ATR, so TP1 is not
            a level price is already sitting on;
          * TP2 must clear ``min_rr_ratio`` against the ACTUAL stop, which is
            what makes this safe to run after the floor has widened it;
          * if the pools are exhausted, structure (support/resistance) is used
            before giving up.

        Returns ``(None, None, "")`` when the map cannot produce a qualifying
        pair, so the caller keeps its existing fallback rather than inventing
        a level. Targets are never fabricated here.
        """
        liquidity_map = liquidity_map or {}
        risk = abs(entry - stop_loss)
        if risk <= 0 or entry <= 0:
            return None, None, ""

        min_rr = self._f(self.settings.get("min_rr_ratio"), 1.5) or 1.5
        min_distance = max(atr, 0.80)

        side_key = "buy_side" if direction == "BUY" else "sell_side"
        structure = resistances if direction == "BUY" else supports

        def _ahead(level: float) -> bool:
            return (level - entry) >= min_distance if direction == "BUY" else (entry - level) >= min_distance

        levels: List[float] = []
        for raw in list(liquidity_map.get(side_key) or []):
            value = self._f(raw, 0.0)
            if value > 0 and _ahead(value):
                levels.append(value)
        for raw in list(structure or []):
            value = self._f(raw, 0.0)
            if value > 0 and _ahead(value):
                levels.append(value)

        if not levels:
            return None, None, ""

        # Nearest first: the order price would meet them in.
        ordered = sorted(set(levels), key=lambda lv: abs(lv - entry))

        # TARGET POLICY (operator directive, 2026-08-04): look at FAR
        # liquidity first, then near -- far liquidity is better. TP2 is the
        # level the trade is held for, so aim it at the FURTHEST real pool
        # whose reward the risk justifies (rr >= min_rr), never beyond
        # max_rr_ratio when a cap is set, so the pick stays a level the map
        # actually drew. This is exactly the docstring's old promise --
        # "reaching for the furthest level the real risk can justify" --
        # which the previous nearest-first pick did not keep: on 2026-07-30
        # the manual analyst booked 257 points more than the shipped TP2 on
        # the same map because his eye went to the far pool first.
        max_rr = self._f(self.settings.get("max_rr_ratio"), 0.0)
        prefer_far = bool(self.settings.get("prefer_far_liquidity", True))

        def _pick(pool_risk: float) -> float | None:
            if pool_risk <= 0:
                return None
            qualifying = [lv for lv in ordered if abs(lv - entry) / pool_risk >= min_rr]
            if not qualifying:
                return None
            if not prefer_far:
                return qualifying[0]
            within_cap = [
                lv for lv in qualifying
                if max_rr <= 0 or abs(lv - entry) / pool_risk <= max_rr
            ]
            return within_cap[-1] if within_cap else qualifying[0]

        tp2 = _pick(risk)
        method = "liquidity_chain"

        if tp2 is None and structural_risk and structural_risk > 0:
            # THE FLOOR MUST NOT VETO THE MAP.
            #
            # `risk` here is the SHIPPED stop, which the noise floor has
            # widened -- on XAU by up to 3x the structural distance. Asking
            # "does this level pay for that padded risk?" is a different
            # question from "is this level a real objective", and when the
            # padding is large enough the answer is always no. The chain then
            # returns nothing and the caller rebuilds targets from the floor
            # itself: the -400/+500/+900 signature.
            #
            # Measured on the live 2026-08-03 16:11 card (d0c708d9, SELL
            # 4045.99, structural stop 132.7 pts floored to 398):
            #
            #   vs 398 pts    4022.31=0.59R 4014.11=0.80R 3996.65=1.24R  none
            #   vs 132.7 pts  4022.31=1.78R 4014.11=2.40R 3996.65=3.72R  all
            #
            # Same map, opposite verdict, decided only by which stop asked.
            #
            # So: try the shipped stop FIRST -- that keeps reaching for the
            # furthest level the real risk can justify, which is what makes
            # TP2 4000.00 rather than the nearest pool. Only when nothing
            # qualifies do we re-ask against structure, so the map is still
            # used instead of inventing a ratio target.
            #
            # This never loosens min_rr_ratio on the published plan: the
            # caller recomputes rr_tp2 against the real, floored stop and
            # `rr_filter` still refuses the trade if it does not pay. It only
            # decides WHERE to aim, never whether to trade.
            # A REAL LEVEL IS NOT REQUIRED TO BEAT AN INVENTED ONE.
            #
            # An earlier version of this branch only accepted the structural
            # pick when it was further than the caller's ratio target
            # (floored_risk x tp2_mult/sl_mult). That is backwards: the ratio
            # target is the very fiction this is meant to replace -- on the
            # 16:11 card it sat at 3956.44, which is 398 x 2.25 and not a
            # level anyone drew. Requiring the map to out-reach it guarantees
            # the fiction wins whenever the floor is large, which is exactly
            # when it matters.
            #
            # The map wins on being real. Whether the trade is worth taking
            # is a separate question, answered downstream by `rr_filter`
            # against the shipped stop -- which is why the 16:11 setup is now
            # refused outright instead of being published with invented
            # targets.
            tp2 = _pick(structural_risk)
            if tp2 is not None:
                method = "liquidity_chain_structural"

        if tp2 is None:
            return None, None, ""

        # TP1 is the nearest pool short of TP2: it books the first half and
        # arms the protection early, while the runner stays aimed at the far
        # objective -- the manual-analyst pattern of a near TP1 with a far
        # extension. When TP2 is itself the nearest level, split the distance
        # rather than stacking both targets on one price -- a TP1 equal to TP2
        # makes the partial close meaningless.
        nearer = [lv for lv in ordered if abs(lv - entry) < abs(tp2 - entry)]
        tp1 = nearer[0] if nearer else round((entry + tp2) / 2.0, 2)

        return tp1, tp2, method

    def _run_filters(
        self,
        atr: float,
        spread_points: Any,
        risk_distance: float,
        tp1_distance: float,
        rr_tp2: float,
        portfolio: Dict[str, Any],
    ) -> Dict[str, bool]:
        min_atr = self._f(self.filters.get("min_atr_for_entry"), 1.0)
        max_spread = self._f(self.filters.get("max_spread_points"), 5.0)
        min_rr = self._f(self.settings.get("min_rr_ratio"), 1.5)
        max_open_trades = int(self.settings.get("max_open_trades", 3))
        max_daily_signals = int(self.settings.get("max_daily_signals", 50))
        max_losses = int(self.filters.get("max_consecutive_losses", 3))
        open_trades_count = int(portfolio.get("open_trades_count", 0) or 0)
        today_signals_count = int(portfolio.get("today_signals_count", 0) or 0)
        consecutive_losses = int(portfolio.get("consecutive_losses", 0) or 0)
        spread_value = None if spread_points is None or str(spread_points).strip().lower() in {"", "unknown", "none"} else self._f(spread_points)

        # SL width is instrument-specific and expressed in project points:
        # - XAU/USD: wide only when > 300 pts ($30 with point_size=0.10)
        # - WTI/USD: wide only when > 120 pts ($1.20 with point_size=0.01)
        # Use max_sl_distance_points if explicitly configured; otherwise the
        # instrument min_sl_distance_points doubles as the maximum allowed width.
        max_sl_points = self._f(
            self.settings.get("max_sl_distance_points", self.settings.get("min_sl_distance_points", 0.0)),
            0.0,
        )
        risk_distance_points = abs(price_to_points(risk_distance, self.symbol))
        sl_width_ok = True if max_sl_points <= 0 else risk_distance_points <= max_sl_points

        return {
            "atr_filter": atr >= min_atr,
            "spread_filter": True if spread_value is None else spread_value <= max_spread,
            "rr_filter": rr_tp2 >= min_rr,
            "sl_width_filter": sl_width_ok,
            "target_distance_filter": tp1_distance >= atr * 1.0,
            "max_open_trades_filter": open_trades_count < max_open_trades,
            "max_daily_signals_filter": today_signals_count < max_daily_signals,
            "consecutive_losses_filter": consecutive_losses < max_losses,
        }

    def _first_failed_reason(self, checks: Dict[str, bool]) -> str:
        reasons = {
            "atr_filter": "ATR too low",
            "spread_filter": "Spread too high",
            "rr_filter": "R:R too low",
            "sl_width_filter": "SL too wide",
            "target_distance_filter": "Target too close",
            "max_open_trades_filter": "Max trades reached",
            "max_daily_signals_filter": "Max daily signals reached",
            "consecutive_losses_filter": "Cooling after consecutive losses",
            "trade_grade_filter": "Trade grade too low",
        }
        for key, passed in checks.items():
            if not passed:
                return reasons.get(key, key)
        return "Rejected"

    def _trade_risk_profile(
        self,
        rr_tp2: float,
        risk_distance: float,
        atr: float,
        direction: str,
        direction_details: Dict[str, Any],
        results: Dict[str, Any],
        checks: Dict[str, bool],
        target_method: str = "",
    ) -> Dict[str, Any]:
        """Grade trade risk quality and assign a risk multiplier."""
        score = 0.0
        notes: List[str] = []

        # A REWARD RATIO IS ONLY EVIDENCE IF SOMETHING MEASURED IT.
        #
        # When the map offers no usable level the targets are rebuilt from the
        # stop itself: tp1 = risk x (atr_tp1/atr_sl), tp2 = risk x
        # (atr_tp2/atr_sl). On XAU that is 1.25 and 2.25 by construction, so
        # `rr_tp2` is 2.25 no matter what the market looks like. Awarding +20
        # for "Good R:R" then grades the arithmetic, not the trade -- and the
        # ratio can never fail, because the question "is reward >= 1.5 x risk"
        # is being asked of a number defined as 2.25 x risk.
        #
        # The effect was backwards and measurable. Signal 36e5cc8a
        # (2026-08-03 16:41, SELL 4037.09, stop 364.2 pts):
        #
        #   liquidity map EMPTY -> tp2 3955.15, rr 2.25 -> +20 -> grade B 65
        #                          -> approved -> published
        #   same setup WITH the real levels -> tp2 4014.11, rr 0.63
        #                          -> -15 -> grade F 0 -> refused
        #
        # 3955.15 is 397 points beyond the furthest level anyone had drawn.
        # Knowing less scored better, so the only plans reaching the operator
        # were the ones the system understood least.
        #
        # So a stop-derived ratio earns nothing. It is not penalised either --
        # the setup may still be sound, and `rr_filter` still applies to it
        # unchanged. It simply stops counting as proof of reward. Targets that
        # came from structure keep their full weight.
        #
        # No risk setting is touched: min_rr_ratio stays 1.5, the floor stays
        # 400/150, and nothing here can open a trade that was refused before.
        method = str(target_method or "")
        mapped_reward = not any(
            marker in method for marker in _STOP_DERIVED_TARGET_METHODS
        )

        if not mapped_reward:
            notes.append("R:R not scored (targets derived from the stop)")
        elif rr_tp2 >= 3.0:
            score += 25; notes.append("Excellent R:R")
        elif rr_tp2 >= 2.0:
            score += 20; notes.append("Good R:R")
        elif rr_tp2 >= self._f(self.settings.get("min_rr_ratio"), 1.5):
            score += 12; notes.append("Acceptable R:R")
        else:
            score -= 15; notes.append("Weak R:R")

        if risk_distance <= atr * 1.6:
            score += 20; notes.append("Sensible stop vs ATR")
        elif risk_distance <= atr * 2.4:
            score += 12; notes.append("Moderate stop")
        else:
            score -= 10; notes.append("Wide stop")

        total_voting = int(direction_details.get("buy_count", 0) or 0) + int(direction_details.get("sell_count", 0) or 0)
        side_count = int(direction_details.get("buy_count" if direction == "BUY" else "sell_count", 0) or 0)
        if total_voting and side_count / total_voting >= 0.75:
            score += 20; notes.append("Strong agent agreement")
        elif side_count >= 3:
            score += 14; notes.append("Acceptable agent agreement")
        else:
            score -= 8; notes.append("Weak agent agreement")

        mtf = results.get("multitimeframe", {}) or {}
        if mtf.get("direction") == direction and mtf.get("alignment") in {"FULL", "PARTIAL"}:
            score += 15; notes.append("Timeframes aligned")
        elif mtf.get("counter_trend"):
            score -= 15; notes.append("Against higher timeframe")

        daily_bias = results.get("daily_bias", {}) or {}
        bias = str(daily_bias.get("bias", "NEUTRAL")).upper()
        if (direction == "BUY" and bias == "BULLISH") or (direction == "SELL" and bias == "BEARISH"):
            score += 10; notes.append("Aligned with Daily Bias")
        elif (direction == "BUY" and bias == "BEARISH") or (direction == "SELL" and bias == "BULLISH"):
            score -= 10; notes.append("Against Daily Bias")

        if all(checks.values()):
            score += 10; notes.append("All core risk filters passed")
        else:
            score -= 20; notes.append("Some risk filters failed")

        if score >= 85:
            grade, label, risk_multiplier = "A+", "Elite", 1.0
        elif score >= 75:
            grade, label, risk_multiplier = "A", "Strong", 1.0
        elif score >= 65:
            grade, label, risk_multiplier = "B", "Good", 0.85
        elif score >= 55:
            grade, label, risk_multiplier = "C", "Reduced", 0.50
        elif score >= 45:
            grade, label, risk_multiplier = "D", "Reject", 0.0
        else:
            grade, label, risk_multiplier = "F", "Reject", 0.0

        return {
            "score": round(max(0, min(100, score)), 1),
            "grade": grade,
            "label": label,
            "risk_multiplier": risk_multiplier,
            "notes": notes[:8],
            "rr_tp2": round(rr_tp2, 2),
            "risk_distance_atr": round(risk_distance / max(atr, 0.01), 2),
        }

    def _position_size(self, entry: float, stop_loss: float, risk_multiplier: float = 1.0) -> Dict[str, Any]:
        capital = self._f(self.settings.get("account_capital"), 0.0)
        base_risk_percent = self._f(self.settings.get("default_risk_percent"), 1.0)
        max_risk_percent = self._f(self.settings.get("max_risk_percent", 2.0), 2.0)
        risk_percent = max(0.0, min(max_risk_percent, base_risk_percent * max(0.0, risk_multiplier)))
        if capital <= 0:
            return {"recommended_lots": None, "risk_amount": None, "based_on_capital": None, "risk_percent": risk_percent}
        risk_amount = capital * (risk_percent / 100)
        price_distance = abs(entry - stop_loss)
        # FIX: use point_size-based calculation that works for ALL instruments,
        # not just gold. The old formula (price_distance * 100) assumed XAUUSD
        # (1 lot = 100 oz, $1 move = $100). For forex (1 lot = 100,000 units)
        # and WTI (1 lot = 1,000 barrels) we need the correct pip value.
        from utils.instruments import point_size, price_to_points
        ps = point_size(self.symbol)
        distance_points = price_to_points(price_distance, self.symbol)
        # Pip value per standard lot:
        #   XAU/USD:  1 lot = 100 oz → $10 per 0.10 move → $10 per point
        #   Forex:    1 lot = 100,000 units → $10 per pip (10 points) → $1 per point
        #   WTI:      1 lot = 1,000 barrels → $10 per 0.01 move → $10 per point
        category = "metal"
        try:
            from utils.instruments import get_instrument
            spec = get_instrument(self.config, self.symbol)
            category = str(spec.get("category", "forex")).lower()
        except Exception:
            pass
        if category == "forex":
            # 1 standard lot = 100,000 units; 1 point = 0.00001 → $1/lot
            value_per_point_per_lot = 1.0
        elif category == "oil":
            # 1 standard lot = 1,000 barrels; 1 point = 0.01 → $10/lot
            value_per_point_per_lot = 10.0
        else:
            # Gold: 1 standard lot = 100 oz; 1 point = 0.10 → $10/lot
            value_per_point_per_lot = 10.0
        risk_per_lot = max(distance_points * value_per_point_per_lot, 0.01)
        lots = risk_amount / risk_per_lot
        max_lots = self._f(self.settings.get("max_lot_size"), 10.0)
        lots = min(lots, max_lots)
        return {
            "recommended_lots": round(lots, 2),
            "risk_amount": round(risk_amount, 2),
            "risk_percent": round(risk_percent, 2),
            "risk_multiplier": round(risk_multiplier, 2),
            "based_on_capital": round(capital, 2),
            "price_risk_distance": round(price_distance, 2),
        }

    def _summary(self, approved: bool, rejection_reason: str | None, stop_loss: float, tp1: float, tp2: float, rr_tp2: float) -> str:
        if approved:
            return f"Trade approved: SL={stop_loss:.2f}, TP1={tp1:.2f}, TP2={tp2:.2f}, R:R={rr_tp2:.2f}"
        return f"Trade rejected: {rejection_reason} | SL={stop_loss:.2f}, TP2={tp2:.2f}, R:R={rr_tp2:.2f}"

    def _rejected(self, reason: str, price: float, direction_details: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "approved": False,
            "rejection_reason": reason,
            "direction": "NEUTRAL",
            "direction_details": direction_details or {},
            "entry": {"price": round(price, 2), "zone": {"low": round(price, 2), "high": round(price, 2)}},
            "stop_loss": {"price": 0.0, "distance_points": 0, "method": "none", "buffer_added": 0},
            "take_profit": {
                "tp1": {"price": 0.0, "distance_points": 0, "rr_ratio": 0},
                "tp2": {"price": 0.0, "distance_points": 0, "rr_ratio": 0},
                "tp3": {"price": 0.0, "distance_points": 0, "rr_ratio": 0},
            },
            "risk_metrics": {"checks": {}, "portfolio": {}, "trade_grade": {"grade": "F", "score": 0, "label": "Rejected"}},
            "trade_grade": {"grade": "F", "score": 0, "label": "Rejected", "risk_multiplier": 0},
            "position_size": {"recommended_lots": None, "risk_amount": None, "based_on_capital": None},
            "trailing_stop": {},
            "summary": f"Rejected: {reason}",
        }

    def _f(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
