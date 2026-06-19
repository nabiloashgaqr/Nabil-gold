"""Multi-Timeframe Agent.

يراقب 4H/1H/15m/5m ويمنع الدخول عكس الاتجاه الرئيسي قدر الإمكان. يعتمد على
ترتيب المتوسطات، ميل السعر، بنية القمم/القيعان، ومستويات الدعم/المقاومة.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Tuple

from agents.base_agent import BaseAgent
from utils.indicators import calculate_ema, calculate_sma, detect_support_resistance, detect_swing_points

Candle = Dict[str, Any]


class MultiTimeframeAgent(BaseAgent):
    """Analyze multiple timeframes and return alignment quality."""

    name = "multitimeframe"

    TIMEFRAME_ORDER = ["4H", "1H", "15m", "5m"]
    TIMEFRAME_WEIGHTS = {"4H": 0.40, "1H": 0.30, "15m": 0.20, "5m": 0.10}

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run multi-timeframe alignment analysis."""
        try:
            tf_payloads = market_data.get("timeframes", {})
            if not tf_payloads:
                tf_payloads = {market_data.get("timeframe", "15m"): market_data}

            timeframe_analysis: Dict[str, Dict[str, Any]] = {}
            for timeframe in self.TIMEFRAME_ORDER:
                payload = tf_payloads.get(timeframe)
                if payload:
                    timeframe_analysis[timeframe] = self._analyze_timeframe(timeframe, payload.get("data", []))
            # Include any extra timeframe not in the canonical order.
            for timeframe, payload in tf_payloads.items():
                if timeframe not in timeframe_analysis:
                    timeframe_analysis[timeframe] = self._analyze_timeframe(str(timeframe), payload.get("data", []))

            weighted_bias = self._weighted_bias(timeframe_analysis)
            direction = weighted_bias["direction"]
            alignment, alignment_score, conflicts, warnings = self._alignment(timeframe_analysis, direction)
            conflict_matrix = self._conflict_matrix(timeframe_analysis)
            setup_type = self._setup_type(timeframe_analysis, direction)
            entry_tf = self._recommended_entry_tf(timeframe_analysis, setup_type)
            htf = timeframe_analysis.get("4H") or timeframe_analysis.get("1H") or next(iter(timeframe_analysis.values()))
            htf_bias = htf.get("bias", "NEUTRAL")
            counter_trend = direction in {"BUY", "SELL"} and htf_bias not in {direction, "NEUTRAL"}
            if counter_trend:
                warnings.append(f"Counter-trend vs HTF: {direction} ضد {htf_bias}")
            confidence = self._confidence(timeframe_analysis, direction, alignment_score, conflicts, counter_trend, setup_type)

            return {
                "agent": self.name,
                "direction": direction,
                "confidence": confidence,
                "timeframe_analysis": timeframe_analysis,
                "timeframe_hierarchy": self.TIMEFRAME_ORDER,
                "alignment": alignment,
                "alignment_score": alignment_score,
                "setup_type": setup_type,
                "counter_trend": counter_trend,
                "recommended_entry_tf": entry_tf,
                "trend_direction_from_htf": htf.get("trend", "SIDEWAYS"),
                "weighted_bias": weighted_bias,
                "conflict_matrix": conflict_matrix,
                "conflicts": conflicts,
                "warnings": warnings,
                "summary": f"توافق الفريمات {alignment} بدرجة {alignment_score}%، setup={setup_type}، اتجاه الفريم الأعلى {htf.get('trend')}، القرار {direction}",
            }
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("MTF analysis failed")
            return self._empty(f"فشل تحليل الفريمات: {exc}")

    def _analyze_timeframe(self, timeframe: str, candles: List[Candle]) -> Dict[str, Any]:
        """Analyze one timeframe bias using EMA, SMA and swing structure."""
        if len(candles) < 50:
            return {"trend": "SIDEWAYS", "strength": "WEAK", "key_level": 0.0, "bias": "NEUTRAL", "score": 0, "signals": ["بيانات غير كافية"]}

        close = self._f(candles[-1].get("close"))
        closes = [self._f(c.get("close")) for c in candles]
        ema20_series = calculate_ema(candles, 20)
        ema50_series = calculate_ema(candles, 50)
        sma200_series = calculate_sma(candles, 200 if len(candles) >= 200 else min(100, len(candles)))
        ema20 = self._last(ema20_series, close)
        ema50 = self._last(ema50_series, close)
        sma_long = self._last(sma200_series, close)
        ema20_prev = self._last(ema20_series[:-5], ema20) if len(ema20_series) > 5 else ema20
        price_slope = closes[-1] - closes[-10] if len(closes) >= 10 else 0.0

        score = 0.0
        signals: List[str] = []
        if close > ema20 > ema50:
            score += 2.0
            signals.append("السعر فوق EMA20/EMA50")
        elif close < ema20 < ema50:
            score -= 2.0
            signals.append("السعر تحت EMA20/EMA50")
        elif close > ema50:
            score += 0.8
            signals.append("السعر فوق EMA50")
        elif close < ema50:
            score -= 0.8
            signals.append("السعر تحت EMA50")

        if ema20 > ema20_prev:
            score += 0.8
            signals.append("ميل EMA20 صاعد")
        elif ema20 < ema20_prev:
            score -= 0.8
            signals.append("ميل EMA20 هابط")

        if close > sma_long:
            score += 0.8
            signals.append("السعر فوق المتوسط الطويل")
        elif close < sma_long:
            score -= 0.8
            signals.append("السعر تحت المتوسط الطويل")

        swing_signal, swing_score = self._swing_structure(candles[-120:])
        score += swing_score
        signals.append(swing_signal)

        if price_slope > 1.0:
            score += 0.6
            signals.append("زخم سعري صاعد آخر 10 شموع")
        elif price_slope < -1.0:
            score -= 0.6
            signals.append("زخم سعري هابط آخر 10 شموع")

        if score >= 2.2:
            trend, bias = "BULLISH", "BUY"
        elif score <= -2.2:
            trend, bias = "BEARISH", "SELL"
        else:
            trend, bias = "SIDEWAYS", "NEUTRAL"

        strength_abs = abs(score)
        strength = "STRONG" if strength_abs >= 4.0 else "MODERATE" if strength_abs >= 2.2 else "WEAK"
        key_level = self._key_level(candles, close, bias, ema50)
        pullback_state = self._pullback_state(close, ema20, ema50, bias)
        momentum = "UP" if price_slope > 1.0 else "DOWN" if price_slope < -1.0 else "FLAT"

        return {
            "trend": trend,
            "strength": strength,
            "key_level": round(key_level, 2),
            "bias": bias,
            "score": round(score, 2),
            "momentum": momentum,
            "pullback_state": pullback_state,
            "ema_20": round(ema20, 2),
            "ema_50": round(ema50, 2),
            "sma_long": round(sma_long, 2),
            "signals": signals[:6],
        }

    def _weighted_bias(self, analysis: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate weighted HTF/LTF directional score."""
        buy_score = 0.0
        sell_score = 0.0
        neutral_score = 0.0
        used_weight = 0.0
        details: Dict[str, float] = {}
        for timeframe, result in analysis.items():
            weight = self.TIMEFRAME_WEIGHTS.get(timeframe, 0.10)
            strength_factor = {"STRONG": 1.0, "MODERATE": 0.75, "WEAK": 0.45}.get(result.get("strength"), 0.45)
            contribution = weight * strength_factor
            used_weight += weight
            if result.get("bias") == "BUY":
                buy_score += contribution
                details[timeframe] = round(contribution, 3)
            elif result.get("bias") == "SELL":
                sell_score += contribution
                details[timeframe] = round(-contribution, 3)
            else:
                neutral_score += contribution
                details[timeframe] = 0.0

        normalized_buy = buy_score / max(used_weight, 0.01)
        normalized_sell = sell_score / max(used_weight, 0.01)
        net = normalized_buy - normalized_sell
        direction = "BUY" if net >= 0.25 else "SELL" if net <= -0.25 else "NEUTRAL"
        return {
            "direction": direction,
            "buy_score": round(normalized_buy * 100, 1),
            "sell_score": round(normalized_sell * 100, 1),
            "neutral_score": round(neutral_score / max(used_weight, 0.01) * 100, 1),
            "net_score": round(net * 100, 1),
            "details": details,
        }

    def _alignment(self, analysis: Dict[str, Dict[str, Any]], direction: str) -> Tuple[str, int, List[str], List[str]]:
        """Evaluate if lower timeframes agree with higher timeframe trend."""
        biases = [result.get("bias", "NEUTRAL") for result in analysis.values()]
        counts = Counter(biases)
        non_neutral = [bias for bias in biases if bias in {"BUY", "SELL"}]
        conflicts: List[str] = []
        warnings: List[str] = []

        htf_bias = (analysis.get("4H") or analysis.get("1H") or {}).get("bias", "NEUTRAL")
        one_h_bias = (analysis.get("1H") or {}).get("bias", "NEUTRAL")
        entry_bias = (analysis.get("15m") or {}).get("bias", "NEUTRAL")

        if direction in {"BUY", "SELL"} and htf_bias not in {direction, "NEUTRAL"}:
            conflicts.append(f"الاتجاه {direction} عكس فريم 4H ({htf_bias})")
        if direction in {"BUY", "SELL"} and one_h_bias not in {direction, "NEUTRAL"}:
            conflicts.append(f"الاتجاه {direction} عكس فريم 1H ({one_h_bias})")
        if direction in {"BUY", "SELL"} and entry_bias not in {direction, "NEUTRAL"}:
            warnings.append(f"فريم الدخول 15m غير متوافق ({entry_bias})")

        if non_neutral and all(bias == non_neutral[0] for bias in non_neutral) and len(non_neutral) == len(biases):
            alignment = "FULL"
        elif direction in {"BUY", "SELL"} and htf_bias in {direction, "NEUTRAL"} and counts.get(direction, 0) >= 2:
            alignment = "PARTIAL"
        elif conflicts or (counts.get("BUY", 0) > 0 and counts.get("SELL", 0) > 0):
            alignment = "CONFLICT"
        else:
            alignment = "WEAK"

        if direction in {"BUY", "SELL"}:
            total_weight = sum(self.TIMEFRAME_WEIGHTS.get(tf, 0.10) for tf in analysis)
            aligned_weight = sum(self.TIMEFRAME_WEIGHTS.get(tf, 0.10) for tf, result in analysis.items() if result.get("bias") == direction)
            alignment_score = int((aligned_weight / max(total_weight, 0.01)) * 100)
        else:
            alignment_score = int((counts.get("NEUTRAL", 0) / max(len(biases), 1)) * 100)

        if alignment == "CONFLICT":
            warnings.append("تعارض واضح بين الفريمات - لا يفضل الدخول إلا بتأكيد قوي")
        if htf_bias == "NEUTRAL":
            warnings.append("الفريم الأعلى محايد، جودة الاتجاه أقل")
        return alignment, alignment_score, conflicts, warnings


    def _conflict_matrix(self, analysis: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
        """Pairwise timeframe agreement matrix."""
        matrix: Dict[str, Dict[str, str]] = {}
        tfs = [tf for tf in self.TIMEFRAME_ORDER if tf in analysis]
        for a in tfs:
            matrix[a] = {}
            for b in tfs:
                ba = analysis[a].get("bias", "NEUTRAL")
                bb = analysis[b].get("bias", "NEUTRAL")
                if a == b:
                    matrix[a][b] = "SELF"
                elif ba == "NEUTRAL" or bb == "NEUTRAL":
                    matrix[a][b] = "NEUTRAL"
                elif ba == bb:
                    matrix[a][b] = "ALIGNED"
                else:
                    matrix[a][b] = "CONFLICT"
        return matrix

    def _setup_type(self, analysis: Dict[str, Dict[str, Any]], direction: str) -> str:
        """Classify the MTF setup type."""
        if direction not in {"BUY", "SELL"}:
            return "NO_TRADE"
        htf = analysis.get("4H") or analysis.get("1H") or {}
        one_h = analysis.get("1H") or {}
        entry = analysis.get("15m") or analysis.get("5m") or {}
        htf_bias = htf.get("bias", "NEUTRAL")
        entry_bias = entry.get("bias", "NEUTRAL")
        if htf_bias == direction and entry_bias == direction:
            return "TREND_CONTINUATION"
        if htf_bias == direction and entry_bias in {"NEUTRAL", direction} and entry.get("pullback_state") in {"PULLBACK_TO_EMA20", "PULLBACK_TO_EMA50"}:
            return "PULLBACK_ENTRY"
        if htf_bias not in {direction, "NEUTRAL"}:
            return "REVERSAL_ATTEMPT"
        if one_h.get("bias") == direction and entry_bias == direction:
            return "INTRADAY_ALIGNMENT"
        return "MIXED_ALIGNMENT"

    def _recommended_entry_tf(self, analysis: Dict[str, Dict[str, Any]], setup_type: str) -> str:
        if setup_type in {"TREND_CONTINUATION", "PULLBACK_ENTRY"} and "15m" in analysis:
            return "15m"
        if "5m" in analysis and setup_type == "INTRADAY_ALIGNMENT":
            return "5m"
        return "15m" if "15m" in analysis else next(iter(analysis.keys()), "15m")

    def _pullback_state(self, close: float, ema20: float, ema50: float, bias: str) -> str:
        near20 = abs(close - ema20) / max(abs(close), 0.01) < 0.0018
        near50 = abs(close - ema50) / max(abs(close), 0.01) < 0.0025
        if bias == "BUY" and near20:
            return "PULLBACK_TO_EMA20"
        if bias == "BUY" and near50:
            return "PULLBACK_TO_EMA50"
        if bias == "SELL" and near20:
            return "PULLBACK_TO_EMA20"
        if bias == "SELL" and near50:
            return "PULLBACK_TO_EMA50"
        return "NONE"

    def _confidence(self, analysis: Dict[str, Dict[str, Any]], direction: str, alignment_score: int, conflicts: List[str], counter_trend: bool = False, setup_type: str = "UNKNOWN") -> int:
        if direction == "NEUTRAL":
            return min(48, max(20, alignment_score))
        strength_bonus = 0
        for result in analysis.values():
            strength_bonus += {"STRONG": 6, "MODERATE": 3, "WEAK": 0}.get(result.get("strength"), 0)
        confidence = min(92, int(alignment_score * 0.75 + strength_bonus))
        if setup_type == "TREND_CONTINUATION":
            confidence += 4
        elif setup_type == "PULLBACK_ENTRY":
            confidence += 2
        elif setup_type == "REVERSAL_ATTEMPT":
            confidence -= 8
        if conflicts:
            confidence = min(confidence, 58)
        if counter_trend:
            confidence = min(confidence, 55)
        return max(35, min(92, confidence))

    def _swing_structure(self, candles: List[Candle]) -> Tuple[str, float]:
        swings = detect_swing_points(candles, lookback=3)
        highs = swings.get("highs", [])[-2:]
        lows = swings.get("lows", [])[-2:]
        if len(highs) >= 2 and len(lows) >= 2:
            if self._f(highs[-1].get("price")) > self._f(highs[-2].get("price")) and self._f(lows[-1].get("price")) > self._f(lows[-2].get("price")):
                return "بنية قمم/قيعان صاعدة HH/HL", 1.2
            if self._f(highs[-1].get("price")) < self._f(highs[-2].get("price")) and self._f(lows[-1].get("price")) < self._f(lows[-2].get("price")):
                return "بنية قمم/قيعان هابطة LH/LL", -1.2
        return "بنية سعرية جانبية/غير واضحة", 0.0

    def _key_level(self, candles: List[Candle], close: float, bias: str, fallback: float) -> float:
        levels = detect_support_resistance(candles[-100:], lookback=80)
        supports = sorted([self._f(x) for x in levels.get("supports", []) if self._f(x) < close], reverse=True)
        resistances = sorted([self._f(x) for x in levels.get("resistances", []) if self._f(x) > close])
        if bias == "BUY" and supports:
            return supports[0]
        if bias == "SELL" and resistances:
            return resistances[0]
        return fallback

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
            "timeframe_analysis": {},
            "alignment": "WEAK",
            "alignment_score": 0,
            "timeframe_hierarchy": self.TIMEFRAME_ORDER,
            "setup_type": "NO_TRADE",
            "counter_trend": False,
            "recommended_entry_tf": "15m",
            "trend_direction_from_htf": "SIDEWAYS",
            "weighted_bias": {"direction": "NEUTRAL", "buy_score": 0, "sell_score": 0, "net_score": 0, "details": {}},
            "conflict_matrix": {},
            "conflicts": [],
            "warnings": [summary],
            "summary": summary,
        }
