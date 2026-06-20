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

            entry_price = self._entry_price(direction, current_price, smc_suggestion)
            stop_loss, sl_method, buffer = self._stop_loss(direction, entry_price, atr, support_levels, resistance_levels, smc_suggestion, results)
            tp1, tp2, tp3, target_method = self._take_profits(direction, entry_price, atr, support_levels, resistance_levels)

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
                    "zone": {
                        "low": round(entry_price - max(0.20, atr * 0.07), 2),
                        "high": round(entry_price + max(0.20, atr * 0.07), 2),
                    },
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

    def _entry_price(self, direction: str, current_price: float, smc_suggestion: Dict[str, Any]) -> float:
        smc_type = str(smc_suggestion.get("type", "")).upper()
        smc_entry = self._f(smc_suggestion.get("entry"), 0.0)
        if smc_type == direction and smc_entry > 0 and abs(smc_entry - current_price) <= max(current_price * 0.01, 20):
            return smc_entry
        return current_price

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
            score += 25; notes.append("R:R ممتاز")
        elif rr_tp2 >= 2.0:
            score += 20; notes.append("R:R جيد")
        elif rr_tp2 >= self._f(self.settings.get("min_rr_ratio"), 1.5):
            score += 12; notes.append("R:R مقبول")
        else:
            score -= 15; notes.append("R:R ضعيف")

        if risk_distance <= atr * 1.6:
            score += 20; notes.append("وقف منطقي مقارنة بالـ ATR")
        elif risk_distance <= atr * 2.4:
            score += 12; notes.append("وقف متوسط")
        else:
            score -= 10; notes.append("وقف واسع")

        total_voting = int(direction_details.get("buy_count", 0) or 0) + int(direction_details.get("sell_count", 0) or 0)
        side_count = int(direction_details.get("buy_count" if direction == "BUY" else "sell_count", 0) or 0)
        if total_voting and side_count / total_voting >= 0.75:
            score += 20; notes.append("توافق وكلاء قوي")
        elif side_count >= 3:
            score += 14; notes.append("توافق وكلاء مقبول")
        else:
            score -= 8; notes.append("توافق وكلاء ضعيف")

        mtf = results.get("multitimeframe", {}) or {}
        if mtf.get("direction") == direction and mtf.get("alignment") in {"FULL", "PARTIAL"}:
            score += 15; notes.append("الفريمات متوافقة")
        elif mtf.get("counter_trend"):
            score -= 15; notes.append("عكس الفريم الأعلى")

        daily_bias = results.get("daily_bias", {}) or {}
        bias = str(daily_bias.get("bias", "NEUTRAL")).upper()
        if (direction == "BUY" and bias == "BULLISH") or (direction == "SELL" and bias == "BEARISH"):
            score += 10; notes.append("متوافق مع Daily Bias")
        elif (direction == "BUY" and bias == "BEARISH") or (direction == "SELL" and bias == "BULLISH"):
            score -= 10; notes.append("عكس Daily Bias")

        if all(checks.values()):
            score += 10; notes.append("كل فلاتر المخاطر الأساسية ناجحة")
        else:
            score -= 20; notes.append("بعض فلاتر المخاطر فشلت")

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
            return f"صفقة معتمدة: SL={stop_loss:.2f}, TP1={tp1:.2f}, TP2={tp2:.2f}, R:R={rr_tp2:.2f}"
        return f"صفقة مرفوضة: {rejection_reason} | SL={stop_loss:.2f}, TP2={tp2:.2f}, R:R={rr_tp2:.2f}"

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
            "summary": f"مرفوض: {reason}",
        }

    def _f(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
