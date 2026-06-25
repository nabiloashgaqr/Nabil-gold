"""Risk Management Agent.

يحسب وقف الخسارة، الأهداف، R:R، وحجم الصفقة الاختياري، ويطبق فلاتر الحماية:
ATR، السبريد، الحد الأقصى للصفقات المفتوحة، الخسائر المتتالية، عرض الوقف،
قرب الهدف، ونسبة العائد إلى المخاطرة.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from agents.base_agent import BaseAgent
from utils.helpers import calculate_pips, load_config

class RiskManagementAgent(BaseAgent):
    """Evaluate risk parameters and approve/reject a potential trade."""

    name = "risk_management"

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        super().__init__(config or load_config())
        self.settings = self.config.get("risk_settings", {})
        self.filters = self.config.get("filters", {})
        self.weights = self.config.get("agent_weights", {"technical": 0.20, "classical": 0.20, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.15})

    def evaluate(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a setup returned by analytical agents."""
        try:
            current_price = self._f(results.get("current_price"))
            direction, direction_details = self._consensus_direction(results)
            if current_price <= 0 or direction == "NEUTRAL":
                return self._rejected("No clear direction", current_price, direction_details=direction_details)

            atr = self._extract_atr(results)
            support_levels, resistance_levels = self._collect_levels(results, current_price)
            smc_suggestion = results.get("smc", {}).get("entry_suggestion", {}) or {}
            portfolio = results.get("portfolio", {}) or {}

            entry_price, entry_kind, entry_basis, entry_zone = self._smart_entry(
                direction, current_price, atr, smc_suggestion, support_levels, resistance_levels,
                results=results,
            )
            if entry_kind == "WAIT_FOR_LEVEL":
                return self._rejected(entry_basis, current_price, direction_details=direction_details)
            stop_loss, sl_method, buffer = self._stop_loss(direction, entry_price, atr, support_levels, resistance_levels, smc_suggestion, results)
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
            tp1, tp2, tp3, target_method = self._take_profits(direction, entry_price, atr, support_levels, resistance_levels)

            # Gold can move 50-100+ points within seconds; a too-tight
            # ATR-based stop gets clipped by ordinary noise/spread rather than
            # an actual reversal. min_sl_distance_points sets a floor on how
            # close SL may sit to entry. When the floor widens the stop,
            # TP1/TP2/TP3 are rescaled from the SAME R:R ratios implied by the
            # configured ATR multipliers (tp_mult/sl_mult) applied to the new,
            # wider stop distance - otherwise R:R would shrink and min_rr_ratio
            # would start rejecting trades purely because SL got floored.
            min_sl_distance = self._f(self.settings.get("min_sl_distance_points"), 0.0) / 10.0
            if min_sl_distance > 0 and abs(entry_price - stop_loss) < min_sl_distance:
                sl_mult = self._f(self.settings.get("atr_multiplier_sl"), 1.5) or 1.5
                tp1_ratio = self._f(self.settings.get("atr_multiplier_tp1"), 2.0) / sl_mult
                tp2_ratio = self._f(self.settings.get("atr_multiplier_tp2"), 3.5) / sl_mult
                tp3_ratio = max(tp2_ratio + 1.0, tp2_ratio * 1.2)
                if direction == "BUY":
                    stop_loss = entry_price - min_sl_distance
                    tp1 = entry_price + min_sl_distance * tp1_ratio
                    tp2 = entry_price + min_sl_distance * tp2_ratio
                    tp3 = entry_price + min_sl_distance * tp3_ratio
                else:
                    stop_loss = entry_price + min_sl_distance
                    tp1 = entry_price - min_sl_distance * tp1_ratio
                    tp2 = entry_price - min_sl_distance * tp2_ratio
                    tp3 = entry_price - min_sl_distance * tp3_ratio
                sl_method = f"{sl_method}+min_floor"
                target_method = "rr_from_floored_sl"

            risk_distance = abs(entry_price - stop_loss)
            max_rr = self._f(self.settings.get("max_rr_ratio"), 4.0)
            if risk_distance > 0 and max_rr > 0:
                max_tp2_distance = risk_distance * max_rr
                if direction == "BUY" and tp2 - entry_price > max_tp2_distance:
                    tp2 = entry_price + max_tp2_distance
                    tp3 = max(tp3, tp2 + atr)
                elif direction == "SELL" and entry_price - tp2 > max_tp2_distance:
                    tp2 = entry_price - max_tp2_distance
                    tp3 = min(tp3, tp2 - atr)
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
            )
            checks["trade_grade_filter"] = risk_profile["grade"] not in {"D", "F"}
            approved = all(checks.values())
            rejection_reason = None if approved else self._first_failed_reason(checks)
            position_size = self._position_size(entry_price, stop_loss, risk_multiplier=risk_profile["risk_multiplier"])

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
                    "zone": {
                        "low": round(entry_zone.get("low", entry_price - max(0.20, atr * 0.07)), 2),
                        "high": round(entry_zone.get("high", entry_price + max(0.20, atr * 0.07)), 2),
                        "proximal": round(entry_zone.get("proximal", entry_price), 2),
                        "distal": round(entry_zone.get("distal", entry_price), 2),
                        "fill_at": entry_zone.get("fill_at", "mid"),
                        "source": entry_zone.get("source", "atr"),
                    },
                    # Smart execution metadata (see _smart_entry / _classify_order):
                    #   kind        -> MARKET / LIMIT / STOP (human concept)
                    #   order_type  -> BUY_MARKET / SELL_LIMIT / ... (broker style)
                    #   basis       -> short text explaining the entry choice
                    #   current_price -> market price at evaluation time
                    "kind": entry_kind,
                    "order_type": self._classify_order(direction, entry_price, current_price),
                    "basis": entry_basis,
                    "current_price": round(current_price, 2),
                    "distance_points": abs(calculate_pips(current_price, entry_price, direction)) if entry_price != current_price else 0.0,
                },
                "stop_loss": {
                    "price": round(stop_loss, 2),
                    "distance_points": abs(calculate_pips(entry_price, stop_loss, direction)),
                    "method": sl_method,
                    "buffer_added": round(buffer, 2),
                },
                "take_profit": {
                    "tp1": {"price": round(tp1, 2), "distance_points": abs(calculate_pips(entry_price, tp1, direction)), "rr_ratio": round(rr_tp1, 2)},
                    "tp2": {"price": round(tp2, 2), "distance_points": abs(calculate_pips(entry_price, tp2, direction)), "rr_ratio": round(rr_tp2, 2)},
                    "tp3": {"price": round(tp3, 2), "distance_points": abs(calculate_pips(entry_price, tp3, direction)), "rr_ratio": round(rr_tp3, 2)},
                },
                "risk_metrics": {
                    "atr": round(atr, 2),
                    "risk_distance_price": round(risk_distance, 2),
                    "tp1_distance_price": round(tp1_distance, 2),
                    "tp2_distance_price": round(tp2_distance, 2),
                    "target_method": target_method,
                    "max_rr_ratio": self._f(self.settings.get("max_rr_ratio"), 4.0),
                    "checks": checks,
                    "portfolio": portfolio,
                    "trade_grade": risk_profile,
                    "risk_multiplier": risk_profile["risk_multiplier"],
                },
                "trade_grade": risk_profile,
                "position_size": position_size,
                "trailing_stop": {"activate_at": "TP1", "move_sl_to": "entry", "trail_distance": round(max(atr * 10, 10), 1)},
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

        if score > 0 and buy_count >= sell_count:
            direction = "BUY"
        elif score < 0 and sell_count >= buy_count:
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
            threshold = self._f(oe.get("market_threshold_points", 30), 30) / 10.0
            if abs(entry - current) <= max(threshold, 0.01):
                return f"{direction}_MARKET"
        else:
            # smart mode
            threshold = self._f(oe.get("pending_threshold_points", 1.0), 1.0) / 10.0
            if abs(entry - current) <= max(threshold, 0.01):
                return f"{direction}_MARKET"

        if direction == "BUY":
            return "BUY_LIMIT" if entry < current else "BUY_STOP"
        if direction == "SELL":
            return "SELL_LIMIT" if entry > current else "SELL_STOP"
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

            points_to_price = lambda p: p / 10.0  # 300 points = 30.00 in price

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
                    dist_points = best_distance * 10  # convert to points
                    if dist_points <= max_risk_points:
                        sl_price = best_level + (fr.get("sl_buffer_points", 20) / 10.0)
                        return _market(
                            f"SELL fixed_risk: resistance at {best_level:.2f} "
                            f"({dist_points:.0f}pts away ≤ {max_risk_points}pts). "
                            f"SL@{sl_price:.2f} (above level+buffer)"
                        )
                    else:
                        target_price = best_level - points_to_price(max_risk_points)
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
                    dist_points = best_distance * 10
                    if dist_points <= max_risk_points:
                        sl_price = best_level - (fr.get("sl_buffer_points", 20) / 10.0)
                        return _market(
                            f"BUY fixed_risk: support at {best_level:.2f} "
                            f"({dist_points:.0f}pts away ≤ {max_risk_points}pts). "
                            f"SL@{sl_price:.2f} (below level+buffer)"
                        )
                    else:
                        target_price = best_level + points_to_price(max_risk_points)
                        return _wait(
                            f"BUY waiting: support at {best_level:.2f} is "
                            f"{dist_points:.0f}pts away > {max_risk_points}pts. "
                            f"Will enter when price falls to ~{target_price:.2f}"
                        )
                return _wait("BUY waiting: no support level found below price")

        # ── smart / hybrid modes (existing logic) ────────────────────────
        enabled = bool(se.get("enabled", True))
        fill_at = str(se.get("fill_at", "mid")).lower()
        zone_width = self._f(se.get("zone_width_points", 50), 50) / 10.0
        min_pts = self._f(se.get("min_pullback_points", 60), 60) / 10.0
        max_pts = self._f(se.get("max_pullback_points", 350), 350) / 10.0

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

        if entry_style == "hybrid":
            market_threshold = self._f(oe.get("market_threshold_points", 30), 30) / 10.0
            smc = results.get("smc", {}) or {}
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

        # SMC order blocks
        smc = results.get("smc", {}) or {}
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
    def _stop_loss(
        self,
        direction: str,
        entry: float,
        atr: float,
        supports: List[float],
        resistances: List[float],
        smc_suggestion: Dict[str, Any],
        results: Dict[str, Any],
    ) -> Tuple[float, str, float]:
        sl_mult = self._f(self.settings.get("atr_multiplier_sl"), 1.5)
        buffer = max(0.30, atr * 0.12)
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
        # Closest logical stop above entry.
        selected_price, selected_method = min(logical, key=lambda item: item[0])
        return selected_price, selected_method, buffer

    def _take_profits(self, direction: str, entry: float, atr: float, supports: List[float], resistances: List[float]) -> Tuple[float, float, float, str]:
        tp1_mult = self._f(self.settings.get("atr_multiplier_tp1"), 2.0)
        tp2_mult = self._f(self.settings.get("atr_multiplier_tp2"), 3.5)
        tp3_mult = 5.0
        min_tp1_distance = max(atr, 0.80)
        method = "atr_targets"
        if direction == "BUY":
            atr_tp1 = entry + atr * tp1_mult
            atr_tp2 = entry + atr * tp2_mult
            valid_res = [level for level in resistances if level - entry >= min_tp1_distance]
            if valid_res:
                tp1 = min(valid_res[0], atr_tp1) if valid_res[0] >= entry + min_tp1_distance else atr_tp1
                tp2_candidates = [level for level in valid_res[1:] if level > tp1]
                tp2 = tp2_candidates[0] if tp2_candidates else max(atr_tp2, tp1 + atr * 1.2)
                method = "resistance_and_atr"
            else:
                tp1, tp2 = atr_tp1, atr_tp2
            tp3 = max(entry + atr * tp3_mult, tp2 + atr)
        else:
            atr_tp1 = entry - atr * tp1_mult
            atr_tp2 = entry - atr * tp2_mult
            valid_sup = [level for level in supports if entry - level >= min_tp1_distance]
            if valid_sup:
                tp1 = max(valid_sup[0], atr_tp1) if valid_sup[0] <= entry - min_tp1_distance else atr_tp1
                tp2_candidates = [level for level in valid_sup[1:] if level < tp1]
                tp2 = tp2_candidates[0] if tp2_candidates else min(atr_tp2, tp1 - atr * 1.2)
                method = "support_and_atr"
            else:
                tp1, tp2 = atr_tp1, atr_tp2
            tp3 = min(entry - atr * tp3_mult, tp2 - atr)
        return tp1, tp2, tp3, method

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
        max_daily_signals = int(self.settings.get("max_daily_signals", 8))
        max_losses = int(self.filters.get("max_consecutive_losses", 3))
        open_trades_count = int(portfolio.get("open_trades_count", 0) or 0)
        today_signals_count = int(portfolio.get("today_signals_count", 0) or 0)
        consecutive_losses = int(portfolio.get("consecutive_losses", 0) or 0)
        spread_value = None if spread_points is None or str(spread_points).strip().lower() in {"", "unknown", "none"} else self._f(spread_points)

        return {
            "atr_filter": atr >= min_atr,
            "spread_filter": True if spread_value is None else spread_value <= max_spread,
            "rr_filter": rr_tp2 >= min_rr,
            "sl_width_filter": risk_distance <= atr * 3.0,
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
    ) -> Dict[str, Any]:
        """Grade trade risk quality and assign a risk multiplier."""
        score = 0.0
        notes: List[str] = []
        if rr_tp2 >= 3.0:
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
        # Approximation for XAUUSD: 1 standard lot ~= 100 oz, $1 move ~= $100.
        lots = risk_amount / max(price_distance * 100, 0.01)
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
