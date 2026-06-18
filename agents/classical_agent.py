"""Classical Analysis Agent.

يرصد الدعوم والمقاومات والفيبوناتشي والسيناريوهات الكلاسيكية الأساسية. هذه
نسخة مرحلة أولى قابلة للتوسع للنماذج السعرية الأكثر تعقيداً لاحقاً.
"""

from __future__ import annotations

from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from utils.indicators import calculate_fibonacci_levels, calculate_pivot_points, detect_support_resistance, detect_swing_points


class ClassicalAgent(BaseAgent):
    """Analyze support/resistance, pivots, Fibonacci and simple trendlines."""

    name = "classical"

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            candles = market_data.get("data", [])
            if len(candles) < 30:
                return self._empty_result("بيانات غير كافية للتحليل الكلاسيكي")

            current_price = float(candles[-1]["close"])
            recent = candles[-80:] if len(candles) >= 80 else candles
            levels = detect_support_resistance(recent, lookback=len(recent))
            supports = sorted(set(round(level, 2) for level in levels.get("supports", [])))
            resistances = sorted(set(round(level, 2) for level in levels.get("resistances", [])))

            # Ensure levels around current price are useful.
            support_levels = sorted([x for x in supports if x < current_price], reverse=True)[:3]
            resistance_levels = sorted([x for x in resistances if x > current_price])[:3]
            if not support_levels:
                support_levels = sorted([float(c["low"]) for c in recent])[:3]
            if not resistance_levels:
                resistance_levels = sorted([float(c["high"]) for c in recent], reverse=True)[:3]

            recent_high = max(float(c["high"]) for c in recent)
            recent_low = min(float(c["low"]) for c in recent)
            prev = recent[-2] if len(recent) > 1 else recent[-1]
            pivot_points = calculate_pivot_points(float(prev["high"]), float(prev["low"]), float(prev["close"]))
            fib = calculate_fibonacci_levels(recent_high, recent_low)
            swings = detect_swing_points(recent, lookback=3)
            trendline = self._build_trendline(swings, current_price)

            nearest_support = support_levels[0] if support_levels else recent_low
            nearest_resistance = resistance_levels[0] if resistance_levels else recent_high
            distance_to_support = abs(current_price - nearest_support)
            distance_to_resistance = abs(nearest_resistance - current_price)
            range_size = max(recent_high - recent_low, 0.01)

            score = 0
            reasons: List[str] = []
            if trendline["direction"] == "ASCENDING" and trendline["respected"]:
                score += 2
                reasons.append("Ascending trendline respected")
            elif trendline["direction"] == "DESCENDING" and trendline["respected"]:
                score -= 2
                reasons.append("Descending trendline respected")

            if distance_to_support / range_size < 0.18:
                score += 1.5
                reasons.append("Price near support")
            if distance_to_resistance / range_size < 0.18:
                score -= 1.5
                reasons.append("Price near resistance")
            if current_price > pivot_points["pivot"]:
                score += 1
                reasons.append("Price above pivot")
            elif current_price < pivot_points["pivot"]:
                score -= 1
                reasons.append("Price below pivot")

            # Simple breakout pattern hints.
            patterns = self._detect_simple_patterns(recent, current_price, nearest_support, nearest_resistance)
            for pattern in patterns:
                if "Ascending" in pattern["pattern"] or pattern.get("type") == "bullish":
                    score += 1
                elif "Descending" in pattern["pattern"] or pattern.get("type") == "bearish":
                    score -= 1

            direction = "BUY" if score >= 2.5 else "SELL" if score <= -2.5 else "NEUTRAL"
            confidence = min(82, int(45 + abs(score) * 10)) if direction != "NEUTRAL" else int(25 + abs(score) * 5)
            bullish_probability = max(10, min(90, int(50 + score * 8)))
            bearish_probability = 100 - bullish_probability

            return {
                "agent": self.name,
                "direction": direction,
                "confidence": confidence,
                "support_levels": [round(x, 2) for x in support_levels[:3]],
                "resistance_levels": [round(x, 2) for x in resistance_levels[:3]],
                "fibonacci_levels": {key: round(value, 2) for key, value in fib.items()},
                "pivot_points": {key: round(value, 2) for key, value in pivot_points.items()},
                "trendline": trendline,
                "patterns_detected": patterns,
                "scenarios": {
                    "bullish": {
                        "condition": f"كسر وإغلاق فوق {nearest_resistance:.2f}",
                        "target": round(nearest_resistance + (nearest_resistance - nearest_support), 2),
                        "probability": bullish_probability,
                    },
                    "bearish": {
                        "condition": f"كسر وإغلاق تحت {nearest_support:.2f}",
                        "target": round(nearest_support - (nearest_resistance - nearest_support), 2),
                        "probability": bearish_probability,
                    },
                },
                "signals": reasons,
                "summary": f"أقرب دعم {nearest_support:.2f} وأقرب مقاومة {nearest_resistance:.2f}. القرار الكلاسيكي: {direction}",
            }
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Classical analysis failed")
            return self._empty_result(f"فشل التحليل الكلاسيكي: {exc}")

    def _build_trendline(self, swings: Dict[str, List[Dict[str, Any]]], current_price: float) -> Dict[str, Any]:
        lows = swings.get("lows", [])[-3:]
        highs = swings.get("highs", [])[-3:]
        if len(lows) >= 2 and lows[-1]["price"] > lows[0]["price"]:
            level = float(lows[-1]["price"])
            return {"direction": "ASCENDING", "respected": current_price >= level * 0.998, "current_level": round(level, 2)}
        if len(highs) >= 2 and highs[-1]["price"] < highs[0]["price"]:
            level = float(highs[-1]["price"])
            return {"direction": "DESCENDING", "respected": current_price <= level * 1.002, "current_level": round(level, 2)}
        return {"direction": "SIDEWAYS", "respected": True, "current_level": round(current_price, 2)}

    def _detect_simple_patterns(self, candles: List[Dict[str, Any]], current_price: float, support: float, resistance: float) -> List[Dict[str, Any]]:
        patterns: List[Dict[str, Any]] = []
        last_20 = candles[-20:]
        highs = [float(c["high"]) for c in last_20]
        lows = [float(c["low"]) for c in last_20]
        if not highs or not lows:
            return patterns
        resistance_flat = max(highs) - min(sorted(highs, reverse=True)[:4]) < 2.5 if len(highs) >= 4 else False
        lows_rising = lows[-1] > lows[0]
        highs_falling = highs[-1] < highs[0]
        support_flat = max(sorted(lows)[:4]) - min(lows) < 2.5 if len(lows) >= 4 else False
        if resistance_flat and lows_rising:
            patterns.append({"pattern": "Ascending Triangle", "status": "forming", "breakout_level": round(resistance, 2), "target": round(resistance + (resistance - support), 2), "type": "bullish"})
        if support_flat and highs_falling:
            patterns.append({"pattern": "Descending Triangle", "status": "forming", "breakout_level": round(support, 2), "target": round(support - (resistance - support), 2), "type": "bearish"})
        if not patterns and abs(current_price - support) < abs(resistance - current_price):
            patterns.append({"pattern": "Support Bounce Setup", "status": "watch", "breakout_level": round(resistance, 2), "target": round(resistance, 2), "type": "bullish"})
        return patterns[:3]

    def _empty_result(self, summary: str) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "direction": "NEUTRAL",
            "confidence": 0,
            "support_levels": [],
            "resistance_levels": [],
            "fibonacci_levels": {},
            "trendline": {"direction": "SIDEWAYS", "respected": True, "current_level": 0.0},
            "patterns_detected": [],
            "scenarios": {},
            "signals": [],
            "summary": summary,
        }
