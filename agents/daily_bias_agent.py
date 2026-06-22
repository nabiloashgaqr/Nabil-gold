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
        confidence = min(95, round(abs(score) / 3.25 * 100, 1)) if bias != "NEUTRAL" else round(max(0, 50 - abs(score) * 10), 1)
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
            "reasons": reasons,
            "summary": f"Daily bias {bias} ({confidence}%) on {preferred}: " + ", ".join(reasons[:3]),
        }

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
