"""Classical Analysis Agent.

يرصد الدعوم والمقاومات والفيبوناتشي والسيناريوهات الكلاسيكية الأساسية. هذه
نسخة مرحلة أولى قابلة للتوسع للنماذج السعرية الأكثر تعقيداً لاحقاً.
"""

from __future__ import annotations

from statistics import mean
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
                return self._empty_result("Not enough data for classical analysis")

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

            level_strength = self._level_strength(recent, support_levels, resistance_levels)
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

            # Classical pattern hints with completion and validation.
            patterns = self._detect_classical_patterns(recent, swings, current_price, nearest_support, nearest_resistance, range_size)
            clear_patterns = [p for p in patterns if p.get("pattern") != "NO_CLEAR_PATTERN"]
            for pattern in clear_patterns:
                completion = float(pattern.get("completion", 0))
                if completion < 75:
                    continue
                weight = 1.6 if pattern.get("confidence") == "high" else 1.0
                if pattern.get("type") == "bullish":
                    score += weight
                elif pattern.get("type") == "bearish":
                    score -= weight

            if not clear_patterns:
                reasons.append("NO_CLEAR_PATTERN - no forced classical setup")

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
                "level_strength": level_strength,
                "patterns_detected": patterns,
                "no_clear_pattern": not any(p.get("pattern") != "NO_CLEAR_PATTERN" for p in patterns),
                "scenarios": {
                    "bullish": {
                        "condition": f"Break and close above {nearest_resistance:.2f}",
                        "target": round(nearest_resistance + (nearest_resistance - nearest_support), 2),
                        "probability": bullish_probability,
                    },
                    "bearish": {
                        "condition": f"Break and close below {nearest_support:.2f}",
                        "target": round(nearest_support - (nearest_resistance - nearest_support), 2),
                        "probability": bearish_probability,
                    },
                },
                "signals": reasons,
                "summary": f"Nearest support {nearest_support:.2f}, nearest resistance {nearest_resistance:.2f}. Classical decision: {direction}",
            }
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Classical analysis failed")
            return self._empty_result(f"Classical analysis failed: {exc}")

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
        """Backward-compatible wrapper."""
        swings = detect_swing_points(candles, lookback=3)
        range_size = max(max(float(c["high"]) for c in candles) - min(float(c["low"]) for c in candles), 0.01)
        return self._detect_classical_patterns(candles, swings, current_price, support, resistance, range_size)

    def _detect_classical_patterns(
        self,
        candles: List[Dict[str, Any]],
        swings: Dict[str, List[Dict[str, Any]]],
        current_price: float,
        support: float,
        resistance: float,
        range_size: float,
    ) -> List[Dict[str, Any]]:
        """Detect core classical patterns without force-fitting."""
        patterns: List[Dict[str, Any]] = []
        highs = swings.get("highs", [])
        lows = swings.get("lows", [])
        tolerance = max(range_size * 0.006, 1.0)

        patterns.extend(self._double_triple_patterns(highs, lows, current_price, support, resistance, tolerance, range_size))
        patterns.extend(self._triangle_patterns(candles, highs, lows, support, resistance, tolerance, range_size))
        patterns.extend(self._channel_wedge_patterns(highs, lows, current_price, support, resistance, range_size))

        # Legacy simple setups only if no stronger pattern exists.
        if not patterns:
            last_20 = candles[-20:]
            highs_raw = [float(c["high"]) for c in last_20]
            lows_raw = [float(c["low"]) for c in last_20]
            resistance_flat = max(highs_raw) - min(sorted(highs_raw, reverse=True)[:4]) < max(2.5, tolerance) if len(highs_raw) >= 4 else False
            lows_rising = lows_raw[-1] > lows_raw[0]
            highs_falling = highs_raw[-1] < highs_raw[0]
            support_flat = max(sorted(lows_raw)[:4]) - min(lows_raw) < max(2.5, tolerance) if len(lows_raw) >= 4 else False
            if resistance_flat and lows_rising:
                patterns.append({"pattern": "Ascending Triangle", "status": "FORMING", "completion": 70, "breakout_level": round(resistance, 2), "target": round(resistance + (resistance - support), 2), "type": "bullish", "confidence": "medium"})
            elif support_flat and highs_falling:
                patterns.append({"pattern": "Descending Triangle", "status": "FORMING", "completion": 70, "breakout_level": round(support, 2), "target": round(support - (resistance - support), 2), "type": "bearish", "confidence": "medium"})

        if not patterns:
            patterns.append({"pattern": "NO_CLEAR_PATTERN", "status": "NONE", "completion": 0, "type": "neutral", "confidence": "none", "note": "No valid classical pattern with minimum touches"})
        return patterns[:6]

    def _double_triple_patterns(self, highs: List[Dict[str, Any]], lows: List[Dict[str, Any]], current_price: float, support: float, resistance: float, tolerance: float, range_size: float) -> List[Dict[str, Any]]:
        patterns: List[Dict[str, Any]] = []
        last_highs = highs[-5:]
        last_lows = lows[-5:]
        high_prices = [float(p["price"]) for p in last_highs]
        low_prices = [float(p["price"]) for p in last_lows]
        eq_highs = self._near_equal_points(high_prices, tolerance)
        eq_lows = self._near_equal_points(low_prices, tolerance)
        if len(eq_highs) >= 2:
            neckline = min(low_prices) if low_prices else support
            completion = 100 if current_price < neckline else 72
            touches = len(eq_highs)
            patterns.append({"pattern": "Triple Top" if touches >= 3 else "Double Top", "status": "COMPLETE" if completion >= 100 else "FORMING", "completion": completion, "touches": touches, "neckline": round(neckline, 2), "breakout_level": round(neckline, 2), "target": round(neckline - (mean(eq_highs) - neckline), 2), "type": "bearish", "confidence": "high" if touches >= 3 else "medium"})
        if len(eq_lows) >= 2:
            neckline = max(high_prices) if high_prices else resistance
            completion = 100 if current_price > neckline else 72
            touches = len(eq_lows)
            patterns.append({"pattern": "Triple Bottom" if touches >= 3 else "Double Bottom", "status": "COMPLETE" if completion >= 100 else "FORMING", "completion": completion, "touches": touches, "neckline": round(neckline, 2), "breakout_level": round(neckline, 2), "target": round(neckline + (neckline - mean(eq_lows)), 2), "type": "bullish", "confidence": "high" if touches >= 3 else "medium"})
        return patterns

    def _triangle_patterns(self, candles: List[Dict[str, Any]], highs: List[Dict[str, Any]], lows: List[Dict[str, Any]], support: float, resistance: float, tolerance: float, range_size: float) -> List[Dict[str, Any]]:
        patterns: List[Dict[str, Any]] = []
        if len(highs) < 2 or len(lows) < 2:
            return patterns
        h1, h2 = float(highs[-2]["price"]), float(highs[-1]["price"])
        l1, l2 = float(lows[-2]["price"]), float(lows[-1]["price"])
        high_slope = h2 - h1
        low_slope = l2 - l1
        flat_high = abs(high_slope) <= tolerance
        flat_low = abs(low_slope) <= tolerance
        completion = self._pattern_completion(candles, support, resistance)
        height = max(resistance - support, range_size * 0.2)
        if flat_high and low_slope > tolerance:
            patterns.append({"pattern": "Ascending Triangle", "status": "COMPLETE" if completion >= 75 else "FORMING", "completion": completion, "touches": 3, "breakout_level": round(resistance, 2), "target": round(resistance + height, 2), "type": "bullish", "confidence": "medium"})
        elif flat_low and high_slope < -tolerance:
            patterns.append({"pattern": "Descending Triangle", "status": "COMPLETE" if completion >= 75 else "FORMING", "completion": completion, "touches": 3, "breakout_level": round(support, 2), "target": round(support - height, 2), "type": "bearish", "confidence": "medium"})
        elif high_slope < -tolerance and low_slope > tolerance:
            direction = "bullish" if candles[-1]["close"] > (support + resistance) / 2 else "bearish"
            patterns.append({"pattern": "Symmetrical Triangle", "status": "COMPLETE" if completion >= 75 else "FORMING", "completion": completion, "touches": 4, "breakout_level": round(resistance if direction == "bullish" else support, 2), "target": round((resistance + height) if direction == "bullish" else (support - height), 2), "type": direction, "confidence": "medium"})
        return patterns

    def _channel_wedge_patterns(self, highs: List[Dict[str, Any]], lows: List[Dict[str, Any]], current_price: float, support: float, resistance: float, range_size: float) -> List[Dict[str, Any]]:
        patterns: List[Dict[str, Any]] = []
        if len(highs) < 3 or len(lows) < 3:
            return patterns
        hs = [float(p["price"]) for p in highs[-3:]]
        ls = [float(p["price"]) for p in lows[-3:]]
        high_slope = hs[-1] - hs[0]
        low_slope = ls[-1] - ls[0]
        width_start = hs[0] - ls[0]
        width_end = hs[-1] - ls[-1]
        narrowing = width_end < width_start * 0.75
        if high_slope > 0 and low_slope > 0 and narrowing:
            patterns.append({"pattern": "Rising Wedge", "status": "FORMING", "completion": 72, "touches": 6, "breakout_level": round(support, 2), "target": round(support - max(width_end, range_size * 0.15), 2), "type": "bearish", "confidence": "medium"})
        elif high_slope < 0 and low_slope < 0 and narrowing:
            patterns.append({"pattern": "Falling Wedge", "status": "FORMING", "completion": 72, "touches": 6, "breakout_level": round(resistance, 2), "target": round(resistance + max(width_end, range_size * 0.15), 2), "type": "bullish", "confidence": "medium"})
        elif abs(high_slope - low_slope) <= range_size * 0.08:
            direction = "bullish" if high_slope > 0 else "bearish" if high_slope < 0 else "neutral"
            if direction != "neutral":
                patterns.append({"pattern": "Ascending Channel" if direction == "bullish" else "Descending Channel", "status": "FORMING", "completion": 68, "touches": 6, "breakout_level": round(resistance if direction == "bullish" else support, 2), "target": round(resistance if direction == "bullish" else support, 2), "type": direction, "confidence": "low"})
        return patterns

    def _near_equal_points(self, prices: List[float], tolerance: float) -> List[float]:
        if not prices:
            return []
        clusters: List[List[float]] = []
        for price in sorted(prices):
            if not clusters or abs(price - mean(clusters[-1])) > tolerance:
                clusters.append([price])
            else:
                clusters[-1].append(price)
        best = max(clusters, key=len) if clusters else []
        return best if len(best) >= 2 else []

    def _pattern_completion(self, candles: List[Dict[str, Any]], support: float, resistance: float) -> int:
        current = float(candles[-1]["close"])
        width = max(resistance - support, 0.01)
        distance_to_edge = min(abs(resistance - current), abs(current - support))
        return int(max(50, min(100, 100 - (distance_to_edge / width) * 100)))

    def _level_strength(self, candles: List[Dict[str, Any]], supports: List[float], resistances: List[float]) -> Dict[str, Any]:
        tolerance = max((max(float(c["high"]) for c in candles) - min(float(c["low"]) for c in candles)) * 0.004, 1.0)
        def touches(level: float) -> int:
            return sum(1 for c in candles if abs(float(c["high"]) - level) <= tolerance or abs(float(c["low"]) - level) <= tolerance)
        return {
            "supports": [{"level": round(x, 2), "touches": touches(x), "valid": touches(x) >= 2} for x in supports[:5]],
            "resistances": [{"level": round(x, 2), "touches": touches(x), "valid": touches(x) >= 2} for x in resistances[:5]],
        }

    def _empty_result(self, summary: str) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "direction": "NEUTRAL",
            "confidence": 0,
            "support_levels": [],
            "resistance_levels": [],
            "fibonacci_levels": {},
            "trendline": {"direction": "SIDEWAYS", "respected": True, "current_level": 0.0},
            "level_strength": {"supports": [], "resistances": []},
            "patterns_detected": [],
            "no_clear_pattern": True,
            "scenarios": {},
            "signals": [],
            "summary": summary,
        }
