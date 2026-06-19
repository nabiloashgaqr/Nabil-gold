"""Price Action / Candlestick Agent.

وكيل حركة السعر والشموع: يرصد النماذج الانعكاسية، قوة الشمعة، الكسر/إعادة
الاختبار، والرفض السعري عند مستويات مهمة. لا يستخدم أي مكتبات مدفوعة.
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Dict, List, Tuple

from agents.base_agent import BaseAgent
from utils.indicators import calculate_atr, detect_support_resistance

Candle = Dict[str, Any]


class PriceActionAgent(BaseAgent):
    """Detect candlestick patterns and price-action confirmations."""

    name = "price_action"

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run price action analysis and return a confirmation/rejection role."""
        try:
            candles = market_data.get("data", [])
            if len(candles) < 20:
                return self._empty("بيانات غير كافية لحركة السعر")

            timeframe = str(market_data.get("timeframe", "15m"))
            current = candles[-1]
            current_price = self._f(current.get("close"))
            atr = self._last(calculate_atr(candles, 14), 1.5)
            metrics = self._candle_metrics(current, atr)
            support_resistance = detect_support_resistance(candles[-90:], lookback=70)
            nearest_support, nearest_resistance = self._nearest_levels(current_price, support_resistance, candles[-40:])

            candle_patterns, pattern_score = self._detect_patterns(candles, timeframe, current_price, nearest_support, nearest_resistance, atr)
            candle_score, candle_text = self._score_current_candle(metrics)
            breakout_analysis, breakout_score = self._breakout_analysis(candles, current_price, nearest_support, nearest_resistance, atr)
            rejection, rejection_score = self._rejection_analysis(metrics, current_price, nearest_support, nearest_resistance, atr)
            context_score, context_text = self._last_three_context(candles, atr)

            total_score = pattern_score + candle_score + breakout_score + rejection_score + context_score
            direction = "BUY" if total_score >= 3.0 else "SELL" if total_score <= -3.0 else "NEUTRAL"
            confidence = min(84, int(38 + abs(total_score) * 9)) if direction != "NEUTRAL" else min(48, int(24 + abs(total_score) * 5))

            role = "CONFIRM" if direction in {"BUY", "SELL"} and confidence >= 60 else "WAIT"
            if direction == "NEUTRAL" and (breakout_analysis.get("quality") == "false_breakout" or metrics["body_ratio"] < 0.12):
                role = "REJECT"

            return {
                "agent": self.name,
                "direction": direction,
                "confidence": confidence,
                "candle_patterns": candle_patterns,
                "candle_analysis": {
                    "current_candle": {
                        "type": metrics["type"],
                        "body_ratio": round(metrics["body_ratio"], 2),
                        "upper_wick_ratio": round(metrics["upper_wick_ratio"], 2),
                        "lower_wick_ratio": round(metrics["lower_wick_ratio"], 2),
                        "close_position": round(metrics["close_position"], 2),
                        "size_vs_atr": round(metrics["size_vs_atr"], 2),
                        "assessment": candle_text,
                    },
                    "last_3_candles": context_text,
                },
                "breakout_analysis": breakout_analysis,
                "rejection": rejection,
                "role": role,
                "signals": self._signals_from_components(candle_patterns, breakout_analysis, rejection, candle_text, context_text),
                "summary": f"Price Action: {direction} بثقة {confidence}% — {candle_text}، {context_text}",
            }
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Price action failed")
            return self._empty(f"فشل Price Action: {exc}")

    def _detect_patterns(
        self,
        candles: List[Candle],
        timeframe: str,
        current_price: float,
        support: float,
        resistance: float,
        atr: float,
    ) -> Tuple[List[Dict[str, Any]], float]:
        """Detect common candlestick patterns and score them."""
        patterns: List[Dict[str, Any]] = []
        score = 0.0
        current = candles[-1]
        previous = candles[-2]
        current_metrics = self._candle_metrics(current, atr)
        previous_metrics = self._candle_metrics(previous, atr)
        location = self._location(current_price, support, resistance, atr)

        # Engulfing
        if (
            current_metrics["type"] == "bullish"
            and previous_metrics["type"] == "bearish"
            and self._f(current.get("close")) >= self._f(previous.get("open"))
            and self._f(current.get("open")) <= self._f(previous.get("close"))
        ):
            strength = "strong" if current_metrics["body_ratio"] > 0.55 else "medium"
            patterns.append({"pattern": "Bullish Engulfing", "timeframe": timeframe, "location": location, "strength": strength, "confirmed": True})
            score += 3.0 if strength == "strong" else 2.2
        elif (
            current_metrics["type"] == "bearish"
            and previous_metrics["type"] == "bullish"
            and self._f(current.get("close")) <= self._f(previous.get("open"))
            and self._f(current.get("open")) >= self._f(previous.get("close"))
        ):
            strength = "strong" if current_metrics["body_ratio"] > 0.55 else "medium"
            patterns.append({"pattern": "Bearish Engulfing", "timeframe": timeframe, "location": location, "strength": strength, "confirmed": True})
            score -= 3.0 if strength == "strong" else 2.2

        # Pin bar / Hammer / Shooting star
        if current_metrics["lower_wick_ratio"] >= 0.55 and current_metrics["lower_wick"] >= current_metrics["body"] * 2:
            confirmed = current_metrics["type"] == "bullish" or current_metrics["close_position"] > 0.55
            strength = "strong" if current_metrics["lower_wick_ratio"] >= 0.65 and location in {"at_support", "near_support"} else "medium"
            patterns.append({"pattern": "Hammer / Bullish Pin Bar", "timeframe": timeframe, "location": location, "strength": strength, "confirmed": confirmed})
            score += 2.4 if confirmed else 1.4
        if current_metrics["upper_wick_ratio"] >= 0.55 and current_metrics["upper_wick"] >= current_metrics["body"] * 2:
            confirmed = current_metrics["type"] == "bearish" or current_metrics["close_position"] < 0.45
            strength = "strong" if current_metrics["upper_wick_ratio"] >= 0.65 and location in {"at_resistance", "near_resistance"} else "medium"
            patterns.append({"pattern": "Shooting Star / Bearish Pin Bar", "timeframe": timeframe, "location": location, "strength": strength, "confirmed": confirmed})
            score -= 2.4 if confirmed else 1.4

        # Doji variants / Spinning Top / Marubozu / Harami / Piercing / Dark Cloud / Tweezer
        if current_metrics["body_ratio"] <= 0.10:
            doji_name = "Doji"
            if current_metrics["lower_wick_ratio"] >= 0.60 and current_metrics["upper_wick_ratio"] <= 0.15:
                doji_name = "Dragonfly Doji"
                score += 1.2 if location in {"at_support", "near_support"} else 0.4
            elif current_metrics["upper_wick_ratio"] >= 0.60 and current_metrics["lower_wick_ratio"] <= 0.15:
                doji_name = "Gravestone Doji"
                score -= 1.2 if location in {"at_resistance", "near_resistance"} else 0.4
            patterns.append({"pattern": doji_name, "timeframe": timeframe, "location": location, "strength": "medium" if doji_name != "Doji" else "weak", "confirmed": False})
            score *= 0.75 if doji_name == "Doji" else 1.0

        if 0.10 < current_metrics["body_ratio"] <= 0.25 and current_metrics["upper_wick_ratio"] >= 0.25 and current_metrics["lower_wick_ratio"] >= 0.25:
            patterns.append({"pattern": "Spinning Top", "timeframe": timeframe, "location": location, "strength": "weak", "confirmed": False})
            score *= 0.85

        if current_metrics["body_ratio"] >= 0.82:
            if current_metrics["type"] == "bullish":
                patterns.append({"pattern": "Bullish Marubozu", "timeframe": timeframe, "location": location, "strength": "strong", "confirmed": True})
                score += 1.8
            elif current_metrics["type"] == "bearish":
                patterns.append({"pattern": "Bearish Marubozu", "timeframe": timeframe, "location": location, "strength": "strong", "confirmed": True})
                score -= 1.8

        if self._f(current.get("high")) < self._f(previous.get("high")) and self._f(current.get("low")) > self._f(previous.get("low")):
            patterns.append({"pattern": "Inside Bar", "timeframe": timeframe, "location": location, "strength": "medium", "confirmed": False})

        # Harami: current body is inside previous body, opposite color preferred.
        prev_body_high = max(self._f(previous.get("open")), self._f(previous.get("close")))
        prev_body_low = min(self._f(previous.get("open")), self._f(previous.get("close")))
        curr_body_high = max(self._f(current.get("open")), self._f(current.get("close")))
        curr_body_low = min(self._f(current.get("open")), self._f(current.get("close")))
        if curr_body_high <= prev_body_high and curr_body_low >= prev_body_low and current_metrics["body_ratio"] <= 0.45:
            if previous_metrics["type"] == "bearish" and current_metrics["type"] == "bullish":
                patterns.append({"pattern": "Bullish Harami", "timeframe": timeframe, "location": location, "strength": "medium", "confirmed": location in {"at_support", "near_support"}})
                score += 1.4 if location in {"at_support", "near_support"} else 0.7
            elif previous_metrics["type"] == "bullish" and current_metrics["type"] == "bearish":
                patterns.append({"pattern": "Bearish Harami", "timeframe": timeframe, "location": location, "strength": "medium", "confirmed": location in {"at_resistance", "near_resistance"}})
                score -= 1.4 if location in {"at_resistance", "near_resistance"} else 0.7

        # Piercing Pattern / Dark Cloud Cover
        prev_mid = (self._f(previous.get("open")) + self._f(previous.get("close"))) / 2
        if previous_metrics["type"] == "bearish" and current_metrics["type"] == "bullish":
            if self._f(current.get("open")) < self._f(previous.get("close")) and self._f(current.get("close")) > prev_mid and self._f(current.get("close")) < self._f(previous.get("open")):
                patterns.append({"pattern": "Piercing Pattern", "timeframe": timeframe, "location": location, "strength": "strong" if location in {"at_support", "near_support"} else "medium", "confirmed": True})
                score += 2.2 if location in {"at_support", "near_support"} else 1.4
        if previous_metrics["type"] == "bullish" and current_metrics["type"] == "bearish":
            if self._f(current.get("open")) > self._f(previous.get("close")) and self._f(current.get("close")) < prev_mid and self._f(current.get("close")) > self._f(previous.get("open")):
                patterns.append({"pattern": "Dark Cloud Cover", "timeframe": timeframe, "location": location, "strength": "strong" if location in {"at_resistance", "near_resistance"} else "medium", "confirmed": True})
                score -= 2.2 if location in {"at_resistance", "near_resistance"} else 1.4

        # Tweezer Top/Bottom: equal highs/lows with opposite candles.
        level_tolerance = max(atr * 0.12, 0.25)
        if abs(self._f(current.get("low")) - self._f(previous.get("low"))) <= level_tolerance and previous_metrics["type"] == "bearish" and current_metrics["type"] == "bullish":
            patterns.append({"pattern": "Tweezer Bottom", "timeframe": timeframe, "location": location, "strength": "medium", "confirmed": location in {"at_support", "near_support"}})
            score += 1.5 if location in {"at_support", "near_support"} else 0.8
        if abs(self._f(current.get("high")) - self._f(previous.get("high"))) <= level_tolerance and previous_metrics["type"] == "bullish" and current_metrics["type"] == "bearish":
            patterns.append({"pattern": "Tweezer Top", "timeframe": timeframe, "location": location, "strength": "medium", "confirmed": location in {"at_resistance", "near_resistance"}})
            score -= 1.5 if location in {"at_resistance", "near_resistance"} else 0.8

        # Inverted Hammer / Hanging Man contextual variants.
        if current_metrics["upper_wick_ratio"] >= 0.55 and current_metrics["upper_wick"] >= current_metrics["body"] * 2:
            prior_move = self._prior_move(candles[-8:-1])
            if prior_move == "DOWN":
                patterns.append({"pattern": "Inverted Hammer", "timeframe": timeframe, "location": location, "strength": "medium", "confirmed": current_metrics["close_position"] > 0.45})
                score += 1.0 if location in {"at_support", "near_support"} else 0.4
            elif prior_move == "UP":
                patterns.append({"pattern": "Hanging Man / Upper Rejection", "timeframe": timeframe, "location": location, "strength": "medium", "confirmed": current_metrics["close_position"] < 0.55})
                score -= 1.0 if location in {"at_resistance", "near_resistance"} else 0.4

        # Morning/Evening star
        if len(candles) >= 3:
            c1, c2, c3 = candles[-3], candles[-2], candles[-1]
            m1 = self._candle_metrics(c1, atr)
            m2 = self._candle_metrics(c2, atr)
            m3 = self._candle_metrics(c3, atr)
            c1_mid = (self._f(c1.get("open")) + self._f(c1.get("close"))) / 2
            if m1["type"] == "bearish" and m2["body_ratio"] < 0.35 and m3["type"] == "bullish" and self._f(c3.get("close")) > c1_mid:
                patterns.append({"pattern": "Morning Star", "timeframe": timeframe, "location": location, "strength": "strong", "confirmed": True})
                score += 3.2
            if m1["type"] == "bullish" and m2["body_ratio"] < 0.35 and m3["type"] == "bearish" and self._f(c3.get("close")) < c1_mid:
                patterns.append({"pattern": "Evening Star", "timeframe": timeframe, "location": location, "strength": "strong", "confirmed": True})
                score -= 3.2

        # Three soldiers/crows
        if len(candles) >= 3:
            last3 = candles[-3:]
            metrics3 = [self._candle_metrics(c, atr) for c in last3]
            if all(m["type"] == "bullish" and m["body_ratio"] > 0.45 for m in metrics3):
                patterns.append({"pattern": "Three White Soldiers", "timeframe": timeframe, "location": "trend_continuation", "strength": "strong", "confirmed": True})
                score += 2.5
            if all(m["type"] == "bearish" and m["body_ratio"] > 0.45 for m in metrics3):
                patterns.append({"pattern": "Three Black Crows", "timeframe": timeframe, "location": "trend_continuation", "strength": "strong", "confirmed": True})
                score -= 2.5

        return patterns[:8], score

    def _score_current_candle(self, metrics: Dict[str, float | str]) -> Tuple[float, str]:
        """Assess current candle strength."""
        score = 0.0
        candle_type = str(metrics["type"])
        body_ratio = float(metrics["body_ratio"])
        close_position = float(metrics["close_position"])
        size_vs_atr = float(metrics["size_vs_atr"])

        if candle_type == "bullish" and body_ratio >= 0.55 and close_position >= 0.70:
            score += 1.6 if size_vs_atr >= 0.70 else 1.0
            text = "شمعة صعودية قوية بإغلاق قريب من القمة"
        elif candle_type == "bearish" and body_ratio >= 0.55 and close_position <= 0.30:
            score -= 1.6 if size_vs_atr >= 0.70 else 1.0
            text = "شمعة هبوطية قوية بإغلاق قريب من القاع"
        elif body_ratio <= 0.15:
            text = "شمعة تردد/دوجي تقلل جودة الإشارة"
            score += 0.0
        elif candle_type == "bullish":
            score += 0.5
            text = "شمعة صعودية متوسطة"
        elif candle_type == "bearish":
            score -= 0.5
            text = "شمعة هبوطية متوسطة"
        else:
            text = "شمعة محايدة"
        return score, text

    def _breakout_analysis(
        self,
        candles: List[Candle],
        current_price: float,
        support: float,
        resistance: float,
        atr: float,
    ) -> Tuple[Dict[str, Any], float]:
        """Detect breakout, false breakout and retest quality."""
        current = candles[-1]
        metrics = self._candle_metrics(current, atr)
        body_quality = metrics["body_ratio"] >= 0.50 and metrics["size_vs_atr"] >= 0.55
        level = None
        breakout_type = None
        score = 0.0
        recent_breakout = False
        quality = "not_detected"
        retested = False

        if resistance and current_price > resistance and body_quality:
            recent_breakout = True
            breakout_type = "bullish"
            level = resistance
            score += 2.2
            quality = "strong" if metrics["close_position"] >= 0.70 else "moderate"
        elif support and current_price < support and body_quality:
            recent_breakout = True
            breakout_type = "bearish"
            level = support
            score -= 2.2
            quality = "strong" if metrics["close_position"] <= 0.30 else "moderate"

        # Retest after a prior breakout within last 8 candles.
        if level is not None:
            for candle in candles[-8:-1]:
                low = self._f(candle.get("low"))
                high = self._f(candle.get("high"))
                if low <= level <= high:
                    retested = True
                    break

        # False breakout: swept a level and closed back inside.
        if not recent_breakout:
            last = candles[-1]
            if resistance and self._f(last.get("high")) > resistance and self._f(last.get("close")) < resistance:
                recent_breakout = True
                breakout_type = "bearish_false_breakout"
                level = resistance
                quality = "false_breakout"
                score -= 1.8
            elif support and self._f(last.get("low")) < support and self._f(last.get("close")) > support:
                recent_breakout = True
                breakout_type = "bullish_false_breakout"
                level = support
                quality = "false_breakout"
                score += 1.8

        return (
            {
                "recent_breakout": recent_breakout,
                "level": round(level, 2) if level else None,
                "type": breakout_type,
                "quality": quality,
                "retested": retested,
            },
            score,
        )

    def _rejection_analysis(
        self,
        metrics: Dict[str, float | str],
        current_price: float,
        support: float,
        resistance: float,
        atr: float,
    ) -> Tuple[Dict[str, Any], float]:
        """Detect price rejection from support/resistance by wick dominance."""
        score = 0.0
        detected = False
        rejection_type = None
        level = None
        strength = "weak"
        near_support = support and abs(current_price - support) <= atr * 0.60
        near_resistance = resistance and abs(current_price - resistance) <= atr * 0.60

        if float(metrics["lower_wick_ratio"]) >= 0.45 and (near_support or float(metrics["close_position"]) >= 0.60):
            detected = True
            rejection_type = "bullish_rejection"
            level = support if near_support else current_price
            strength = "strong" if float(metrics["lower_wick_ratio"]) >= 0.60 else "medium"
            score += 2.0 if strength == "strong" else 1.2
        if float(metrics["upper_wick_ratio"]) >= 0.45 and (near_resistance or float(metrics["close_position"]) <= 0.40):
            # If both wicks are long, keep the dominant one.
            upper_score = 2.0 if float(metrics["upper_wick_ratio"]) >= 0.60 else 1.2
            if not detected or upper_score > abs(score):
                detected = True
                rejection_type = "bearish_rejection"
                level = resistance if near_resistance else current_price
                strength = "strong" if float(metrics["upper_wick_ratio"]) >= 0.60 else "medium"
                score = -upper_score

        return (
            {
                "detected": detected,
                "level": round(float(level), 2) if level else None,
                "type": rejection_type,
                "strength": strength if detected else None,
            },
            score,
        )

    def _last_three_context(self, candles: List[Candle], atr: float) -> Tuple[float, str]:
        """Assess acceleration/deceleration over last three candles."""
        last3 = candles[-3:]
        metrics = [self._candle_metrics(c, atr) for c in last3]
        bullish = sum(1 for m in metrics if m["type"] == "bullish")
        bearish = sum(1 for m in metrics if m["type"] == "bearish")
        avg_body = mean(float(m["body_ratio"]) for m in metrics)
        avg_size = mean(float(m["size_vs_atr"]) for m in metrics)
        if bullish == 3 and avg_body >= 0.45:
            return (1.2 if avg_size >= 0.55 else 0.8, "صعود متسارع مع أجسام صاعدة")
        if bearish == 3 and avg_body >= 0.45:
            return (-1.2 if avg_size >= 0.55 else -0.8, "هبوط متسارع مع أجسام هابطة")
        if avg_body <= 0.20:
            return (0.0, "تباطؤ/تردد واضح في آخر 3 شموع")
        return (0.0, "سياق شموع مختلط")

    def _candle_metrics(self, candle: Candle, atr: float) -> Dict[str, float | str]:
        """Return normalized candle anatomy metrics."""
        open_price = self._f(candle.get("open"))
        high = self._f(candle.get("high"))
        low = self._f(candle.get("low"))
        close = self._f(candle.get("close"))
        candle_range = max(high - low, 0.0001)
        body = abs(close - open_price)
        upper_wick = high - max(open_price, close)
        lower_wick = min(open_price, close) - low
        close_position = (close - low) / candle_range
        candle_type = "bullish" if close > open_price else "bearish" if close < open_price else "doji"
        return {
            "type": candle_type,
            "range": candle_range,
            "body": body,
            "upper_wick": upper_wick,
            "lower_wick": lower_wick,
            "body_ratio": body / candle_range,
            "upper_wick_ratio": upper_wick / candle_range,
            "lower_wick_ratio": lower_wick / candle_range,
            "close_position": close_position,
            "size_vs_atr": candle_range / max(atr, 0.01),
        }

    def _nearest_levels(self, current_price: float, levels: Dict[str, List[float]], candles: List[Candle]) -> Tuple[float, float]:
        supports = sorted([self._f(x) for x in levels.get("supports", []) if self._f(x) < current_price], reverse=True)
        resistances = sorted([self._f(x) for x in levels.get("resistances", []) if self._f(x) > current_price])
        support = supports[0] if supports else min(self._f(c.get("low")) for c in candles)
        resistance = resistances[0] if resistances else max(self._f(c.get("high")) for c in candles)
        return support, resistance

    def _location(self, price: float, support: float, resistance: float, atr: float) -> str:
        if support and abs(price - support) <= atr * 0.45:
            return "at_support"
        if resistance and abs(price - resistance) <= atr * 0.45:
            return "at_resistance"
        if support and abs(price - support) <= atr * 0.90:
            return "near_support"
        if resistance and abs(price - resistance) <= atr * 0.90:
            return "near_resistance"
        return "mid_range"


    def _prior_move(self, candles: List[Candle]) -> str:
        """Classify short prior move before a candle pattern."""
        if len(candles) < 3:
            return "FLAT"
        first = self._f(candles[0].get("close"))
        last = self._f(candles[-1].get("close"))
        change = last - first
        avg_range = mean(max(self._f(c.get("high")) - self._f(c.get("low")), 0.01) for c in candles)
        if change > avg_range * 0.6:
            return "UP"
        if change < -avg_range * 0.6:
            return "DOWN"
        return "FLAT"

    def _signals_from_components(
        self,
        patterns: List[Dict[str, Any]],
        breakout: Dict[str, Any],
        rejection: Dict[str, Any],
        candle_text: str,
        context_text: str,
    ) -> List[str]:
        signals = [f"Pattern: {p['pattern']} ({p.get('strength')})" for p in patterns[:4]]
        if breakout.get("recent_breakout"):
            signals.append(f"Breakout: {breakout.get('type')} at {breakout.get('level')} quality={breakout.get('quality')}")
        if rejection.get("detected"):
            signals.append(f"Rejection: {rejection.get('type')} at {rejection.get('level')}")
        signals.append(candle_text)
        signals.append(context_text)
        return signals[:8]

    def _last(self, values: List[float | None], default: float) -> float:
        for value in reversed(values):
            if value is not None:
                return float(value)
        return default

    def _f(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _empty(self, summary: str) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "direction": "NEUTRAL",
            "confidence": 0,
            "candle_patterns": [],
            "candle_analysis": {},
            "breakout_analysis": {"recent_breakout": False, "level": None, "type": None, "quality": "not_detected", "retested": False},
            "rejection": {"detected": False, "level": None, "type": None, "strength": None},
            "role": "WAIT",
            "signals": [],
            "summary": summary,
        }
