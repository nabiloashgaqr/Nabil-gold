"""Daily Bias Agent.

Determines the higher-timeframe bias used to avoid weak counter-trend trades.
For live data it uses the configured trend timeframe (usually 4H) as a practical
proxy for daily direction because GitHub Actions data fetch is optimized for
intraday operation.
"""

from __future__ import annotations

from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from utils.helpers import load_config
from utils.indicators import calculate_ema, calculate_rsi
from services.market_snapshot import build_market_snapshot


class DailyBiasAgent(BaseAgent):
    """Infer broader market bias from 4H/1H/primary candles."""

    name = "daily_bias"

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        super().__init__(config or load_config())
        self.settings = self.config.get("daily_bias_filter", {}) or {}

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.settings.get("enabled", True):
            return {"agent": self.name, "enabled": False, "bias": "NEUTRAL", "confidence": 0, "summary": "Daily bias disabled"}

        timeframes = data.get("timeframes", {}) or {}
        preferred = self.settings.get("timeframe") or self.config.get("trend_timeframe", "4H")
        payload = timeframes.get(preferred) or timeframes.get("4H") or timeframes.get("1H") or {"data": data.get("data", [])}
        candles: List[Dict[str, Any]] = list(payload.get("data", []) or [])
        if len(candles) < 50:
            return {"agent": self.name, "enabled": True, "bias": "NEUTRAL", "confidence": 0, "summary": "Not enough candles for daily bias"}

        closes = [self._f(c.get("close")) for c in candles if self._f(c.get("close")) > 0]
        if len(closes) < 50:
            return {"agent": self.name, "enabled": True, "bias": "NEUTRAL", "confidence": 0, "summary": "Invalid candles for daily bias"}
        snapshot = build_market_snapshot({**payload, "symbol": data.get("symbol"), "timeframe": preferred}, self.config)

        ema_fast_series = calculate_ema(closes, int(self.settings.get("ema_fast", 20)))
        ema_slow_series = calculate_ema(closes, int(self.settings.get("ema_slow", 50)))
        rsi_series = calculate_rsi(closes, int(self.settings.get("rsi_period", 14)))
        # calculate_ema/calculate_rsi return the full series (with leading/trailing
        # None values), so reduce each to its latest valid scalar before comparing.
        ema_fast = self._last_valid(ema_fast_series)
        ema_slow = self._last_valid(ema_slow_series)
        rsi = self._last_valid(rsi_series)
        last = closes[-1]
        if ema_fast is None or ema_slow is None or rsi is None:
            return {"agent": self.name, "enabled": True, "bias": "NEUTRAL", "confidence": 0, "summary": "Indicators not ready for daily bias"}
        lookback = int(self.settings.get("slope_lookback", 12))
        old = closes[-lookback] if len(closes) > lookback else closes[0]
        slope = last - old
        prev_fast = self._last_valid(ema_fast_series[:-lookback]) if len(ema_fast_series) > lookback else ema_fast
        prev_slow = self._last_valid(ema_slow_series[:-lookback]) if len(ema_slow_series) > lookback else ema_slow
        previous_bias = self._raw_bias(last=old, ema_fast=prev_fast or ema_fast, ema_slow=prev_slow or ema_slow, rsi=self._last_valid(rsi_series[:-lookback]) or rsi, slope=slope)

        score = 0.0
        reasons: List[str] = []
        if last > ema_slow:
            score += 1
            reasons.append("price above EMA slow")
        else:
            score -= 1
            reasons.append("price below EMA slow")
        if ema_fast > ema_slow:
            score += 1
            reasons.append("EMA fast above EMA slow")
        else:
            score -= 1
            reasons.append("EMA fast below EMA slow")
        if slope > 0:
            score += 0.75
            reasons.append("positive higher-timeframe slope")
        elif slope < 0:
            score -= 0.75
            reasons.append("negative higher-timeframe slope")
        if rsi > 55:
            score += 0.5
            reasons.append("RSI bullish")
        elif rsi < 45:
            score -= 0.5
            reasons.append("RSI bearish")

        threshold = float(self.settings.get("score_threshold", 1.25))
        if score >= threshold:
            bias = "BULLISH"
        elif score <= -threshold:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"
        persistence = self._bias_persistence(previous_bias, bias, score, threshold)
        if persistence.get("smoothed_bias") != bias:
            reasons.append(persistence.get("reason", "bias persistence smoothing"))
            bias = persistence.get("smoothed_bias", bias)
        strength_band = self._strength_band(bias, score)
        confidence = min(95, round(abs(score) / 3.25 * 100, 1)) if bias != "NEUTRAL" else round(max(0, 50 - abs(score) * 10), 1)
        reason_codes = self._reason_codes(bias, strength_band, last, ema_fast, ema_slow, rsi, slope, persistence)
        evidence = [
            {"name": "price_vs_ema_slow", "value": round(last - ema_slow, 2), "bias": "BULLISH" if last > ema_slow else "BEARISH"},
            {"name": "ema_fast_vs_slow", "value": round(ema_fast - ema_slow, 2), "bias": "BULLISH" if ema_fast > ema_slow else "BEARISH"},
            {"name": "HTF slope", "value": round(slope, 2), "bias": "BULLISH" if slope > 0 else "BEARISH" if slope < 0 else "NEUTRAL"},
            {"name": "RSI", "value": round(rsi, 2), "bias": "BULLISH" if rsi > 55 else "BEARISH" if rsi < 45 else "NEUTRAL"},
        ]
        return {
            "agent": self.name,
            "enabled": True,
            "timeframe": preferred,
            "bias": bias,
            "confidence": confidence,
            "score": round(score, 2),
            "price": round(last, 2),
            "ema_fast": round(ema_fast, 2),
            "ema_slow": round(ema_slow, 2),
            "rsi": round(rsi, 2),
            "slope": round(slope, 2),
            "strength_band": strength_band,
            "bias_persistence": persistence,
            "previous_bias_estimate": previous_bias,
            "reasons": reasons,
            "reason_codes": reason_codes,
            "evidence": evidence,
            "invalidations": ["Structure break against bias", "EMA fast/slow flip with RSI confirmation"] if bias != "NEUTRAL" else [],
            "data_quality": snapshot.get("data_quality", {}),
            "verified_snapshot": snapshot,
            "confidence_breakdown": {"price_location": 20 if last > ema_slow and bias == "BULLISH" or last < ema_slow and bias == "BEARISH" else 5, "ema_alignment": 20 if (ema_fast > ema_slow and bias == "BULLISH") or (ema_fast < ema_slow and bias == "BEARISH") else 5, "slope": 15 if (slope > 0 and bias == "BULLISH") or (slope < 0 and bias == "BEARISH") else 4, "rsi": 10 if (rsi > 55 and bias == "BULLISH") or (rsi < 45 and bias == "BEARISH") else 3, "penalties": -8 if persistence.get("smoothed") else 0},
            "warnings": [persistence.get("reason")] if persistence.get("smoothed") else [],
            "summary": f"Daily bias {bias} ({confidence}%) on {preferred}: " + ", ".join(reasons[:3]),
        }

    def _raw_bias(self, last: float, ema_fast: float, ema_slow: float, rsi: float, slope: float) -> str:
        score = 0.0
        score += 1 if last > ema_slow else -1
        score += 1 if ema_fast > ema_slow else -1
        score += 0.75 if slope > 0 else -0.75 if slope < 0 else 0
        score += 0.5 if rsi > 55 else -0.5 if rsi < 45 else 0
        threshold = float(self.settings.get("score_threshold", 1.25))
        return "BULLISH" if score >= threshold else "BEARISH" if score <= -threshold else "NEUTRAL"

    def _bias_persistence(self, previous_bias: str, new_bias: str, score: float, threshold: float) -> Dict[str, Any]:
        if previous_bias in {"BULLISH", "BEARISH"} and new_bias in {"BULLISH", "BEARISH"} and previous_bias != new_bias:
            flip_threshold = float(self.settings.get("flip_score_threshold", max(threshold + 0.75, 2.0)))
            if abs(score) < flip_threshold:
                return {"smoothed": True, "previous_bias": previous_bias, "raw_bias": new_bias, "smoothed_bias": "NEUTRAL", "reason": f"Bias flip {previous_bias}->{new_bias} requires stronger confirmation"}
        return {"smoothed": False, "previous_bias": previous_bias, "raw_bias": new_bias, "smoothed_bias": new_bias}

    def _strength_band(self, bias: str, score: float) -> str:
        if bias == "NEUTRAL":
            return "neutral"
        side = "bullish" if bias == "BULLISH" else "bearish"
        mag = abs(score)
        level = "strong" if mag >= 2.75 else "moderate" if mag >= 2.0 else "weak"
        return f"{level}_{side}"

    def _reason_codes(self, bias: str, strength: str, last: float, ema_fast: float, ema_slow: float, rsi: float, slope: float, persistence: Dict[str, Any]) -> List[str]:
        codes: List[str] = [f"DAILY_BIAS_{bias}", f"DAILY_STRENGTH_{strength.upper()}"]
        codes.append("DAILY_PRICE_ABOVE_EMA_SLOW" if last > ema_slow else "DAILY_PRICE_BELOW_EMA_SLOW")
        codes.append("DAILY_EMA_FAST_ABOVE_SLOW" if ema_fast > ema_slow else "DAILY_EMA_FAST_BELOW_SLOW")
        if rsi > 55:
            codes.append("DAILY_RSI_BULL")
        elif rsi < 45:
            codes.append("DAILY_RSI_BEAR")
        if slope > 0:
            codes.append("DAILY_SLOPE_POSITIVE")
        elif slope < 0:
            codes.append("DAILY_SLOPE_NEGATIVE")
        if persistence.get("smoothed"):
            codes.append("DAILY_BIAS_FLIP_SMOOTHED")
        return codes[:10]

    @staticmethod
    def _last_valid(series: List[Any] | None) -> float | None:
        """Return the most recent non-None numeric value from an indicator series."""
        if not series:
            return None
        for value in reversed(series):
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return None

    def _f(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
