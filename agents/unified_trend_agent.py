"""Unified Trend Evidence Agent.

One native 5m/15m/1H/4H evidence engine replaces the old Technical and
Multi-Timeframe votes. Raw directional evidence is normalized first; a single
historically calibrated confidence and a single external vote are emitted.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping

from agents.base_agent import BaseAgent
from services.market_snapshot import build_market_snapshot
from services.timeframe_fusion import (
    CANONICAL_TIMEFRAMES,
    missing_required_timeframes,
    native_timeframe_payloads,
)
from utils.indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    detect_support_resistance,
    detect_swing_points,
)
from utils.instruments import point_size


FEATURE_NAMES = (
    "ema",
    "structure",
    "momentum",
    "macd_hist",
    "macd_slope",
    "rsi_center",
    "rsi_accel",
    "rsi_divergence",
)


class UnifiedTrendAgent(BaseAgent):
    name = "unified_trend"

    def __init__(self, config: Dict[str, Any], **_kwargs: Any):
        super().__init__(config)
        cfg = config.get("unified_trend", {}) or {}
        self.min_confidence = float(
            (config.get("signal_requirements", {}) or {}).get("agent_min_confidence", 67) or 67
        )
        self.calibration_required = bool(cfg.get("calibration_required", True))
        self.calibration_path = Path(
            str(cfg.get("calibration_path") or "storage/model_calibration/unified_trend_v1.json")
        )
        self.calibration = self._load_calibration()

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        missing = missing_required_timeframes(market_data, self.config)
        if missing:
            return self._wait(
                "NATIVE_TIMEFRAME_BOOK_INCOMPLETE",
                f"Missing native timeframe(s): {', '.join(missing)}",
                missing_timeframes=missing,
            )
        if self.calibration_required and not self.calibration:
            return self._wait(
                "CALIBRATION_INVALID",
                f"Unified Trend calibration unavailable or invalid: {self.calibration_path}",
            )

        payloads = native_timeframe_payloads(market_data, self.config)
        raw_by_tf: Dict[str, Dict[str, float]] = {}
        normalized_by_tf: Dict[str, Dict[str, float]] = {}
        family_by_tf: Dict[str, Dict[str, float]] = {}
        valid_tfs: List[str] = []
        for timeframe in CANONICAL_TIMEFRAMES:
            candles = list((payloads.get(timeframe) or {}).get("data") or [])
            raw = self.raw_features(candles)
            if raw is None:
                continue
            raw_by_tf[timeframe] = raw
            normalized = {
                feature: self._normalize(timeframe, feature, value, candles)
                for feature, value in raw.items()
            }
            normalized_by_tf[timeframe] = normalized
            family_by_tf[timeframe] = self._families(normalized)
            valid_tfs.append(timeframe)

        if len(valid_tfs) != len(CANONICAL_TIMEFRAMES):
            missing_calc = [tf for tf in CANONICAL_TIMEFRAMES if tf not in valid_tfs]
            return self._wait(
                "INSUFFICIENT_NATIVE_HISTORY",
                f"Insufficient native candle history: {', '.join(missing_calc)}",
                missing_timeframes=missing_calc,
            )

        family_scores: Dict[str, float] = {}
        for family in ("ema", "structure", "momentum", "macd", "rsi"):
            values = [family_by_tf[tf][family] for tf in CANONICAL_TIMEFRAMES]
            family_scores[family] = self._clip(mean(values))

        values = list(family_scores.values())
        base_edge = mean(values) if values else 0.0
        absolute = sum(abs(x) for x in values)
        coherence = abs(sum(values)) / absolute if absolute > 0 else 0.0
        coverage = len(values) / 5.0
        edge = self._clip(base_edge * coherence * coverage)
        confidence = self._calibrated_confidence(abs(edge))
        raw_direction = "BUY" if edge > 0 else "SELL" if edge < 0 else "WAIT"
        signal = raw_direction if raw_direction in {"BUY", "SELL"} and confidence >= self.min_confidence else "WAIT"

        regime = self._market_regime(list(payloads["15m"].get("data") or []), edge)
        setup_type, timing_state, entry_permission = self._setup_context(
            family_by_tf, raw_direction, confidence
        )
        levels = self._key_levels(list(payloads["15m"].get("data") or []))
        snapshot = build_market_snapshot(market_data, self.config)
        reasons = self._reasons(family_scores, edge, coherence, signal)
        return {
            "agent": self.name,
            "signal": signal,
            "direction": signal,
            "raw_direction": raw_direction,
            "confidence": round(confidence, 1),
            "raw_edge": round(edge, 4),
            "base_edge": round(base_edge, 4),
            "coherence": round(coherence, 4),
            "coverage": round(coverage, 4),
            "family_scores": {k: round(v, 4) for k, v in family_scores.items()},
            "timeframe_family_scores": {
                tf: {k: round(v, 4) for k, v in vals.items()}
                for tf, vals in family_by_tf.items()
            },
            "timeframe_raw_features": {
                tf: {k: round(v, 6) for k, v in vals.items()}
                for tf, vals in raw_by_tf.items()
            },
            "timeframe_normalized_features": {
                tf: {k: round(v, 4) for k, v in vals.items()}
                for tf, vals in normalized_by_tf.items()
            },
            "market_regime": regime,
            "rsi": round(50.0 + 25.0 * float(raw_by_tf.get("15m", {}).get("rsi_center", 0.0)), 2),
            "setup_type": setup_type,
            "timing_state": timing_state,
            "entry_permission": entry_permission,
            "trend_direction_from_htf": self._htf_direction(family_by_tf),
            "key_levels": levels,
            "support_levels": levels.get("supports", []),
            "resistance_levels": levels.get("resistances", []),
            "data_quality": snapshot.get("data_quality", {}),
            "verified_snapshot": snapshot,
            "calibration": {
                "version": (self.calibration or {}).get("version"),
                "samples": int((self.calibration or {}).get("sample_count", 0) or 0),
                "checksum_valid": bool(self.calibration),
            },
            "reason_codes": self._reason_codes(signal, raw_direction, regime, coherence),
            "reasons": reasons,
            "evidence": [
                {"name": name, "value": round(value, 4), "bias": "BULLISH" if value > 0 else "BEARISH" if value < 0 else "NEUTRAL"}
                for name, value in family_scores.items()
            ],
            "invalidations": self._invalidations(signal, levels),
            "confidence_breakdown": {
                "calibrated_probability": round(confidence, 1),
                "edge_strength": round(abs(edge) * 100, 1),
                "coherence": round(coherence * 100, 1),
                "coverage": round(coverage * 100, 1),
            },
            "summary": (
                f"Unified Trend: {signal} {confidence:.1f}% "
                f"(raw {raw_direction}, edge {edge:+.3f}, coherence {coherence * 100:.0f}%)"
            ),
            "timestamp": self.now_iso(),
        }

    @classmethod
    def raw_features(cls, candles: List[Dict[str, Any]]) -> Dict[str, float] | None:
        """Raw ATR-normalized feature vector for one native timeframe."""
        if len(candles) < 205:
            return None
        closes = [cls._float(c.get("close")) for c in candles]
        atr_series = calculate_atr(candles, 14)
        atr = cls._last_number(atr_series, 0.0)
        if atr <= 0:
            return None
        emas = {p: cls._last_number(calculate_ema(closes, p), closes[-1]) for p in (8, 21, 50, 100, 200)}
        ema_components = [
            (closes[-1] - emas[8]) / atr,
            (emas[8] - emas[21]) / atr,
            (emas[21] - emas[50]) / atr,
            (emas[50] - emas[100]) / atr,
            (emas[100] - emas[200]) / atr,
        ]
        ema_raw = mean(ema_components)

        swings = detect_swing_points(candles[-160:], lookback=3)
        highs = swings.get("highs", [])[-2:]
        lows = swings.get("lows", [])[-2:]
        structure = 0.0
        if len(highs) == 2 and len(lows) == 2:
            structure = (
                (cls._float(highs[-1].get("price")) - cls._float(highs[-2].get("price")))
                + (cls._float(lows[-1].get("price")) - cls._float(lows[-2].get("price")))
            ) / (2.0 * atr)

        momentum = (closes[-1] - closes[-11]) / atr
        macd = calculate_macd(closes)
        histogram = [float(x) for x in (macd.get("histogram") or []) if x is not None]
        macd_hist = (histogram[-1] / atr) if histogram else 0.0
        macd_slope = ((histogram[-1] - histogram[-6]) / atr) if len(histogram) >= 6 else 0.0

        rsi14 = cls._last_number(calculate_rsi(closes, 14), 50.0)
        rsi7 = cls._last_number(calculate_rsi(closes, 7), rsi14)
        divergence = cls._rsi_divergence(candles, calculate_rsi(closes, 14))
        return {
            "ema": ema_raw,
            "structure": structure,
            "momentum": momentum,
            "macd_hist": macd_hist,
            "macd_slope": macd_slope,
            "rsi_center": (rsi14 - 50.0) / 25.0,
            "rsi_accel": (rsi7 - rsi14) / 15.0,
            "rsi_divergence": divergence,
        }

    def _normalize(
        self, timeframe: str, feature: str, value: float,
        candles: List[Dict[str, Any]],
    ) -> float:
        values = (((self.calibration or {}).get("features") or {}).get(timeframe) or {}).get(feature) or []
        try:
            ordered = sorted(float(x) for x in values if float(x) >= 0)
        except (TypeError, ValueError):
            ordered = []
        if not ordered:
            # Tests/manual tools may deliberately disable required calibration.
            # Production never reaches this branch because INSTALL validates it.
            if self.calibration_required:
                return 0.0
            scale = {
                "ema": 1.0, "structure": 2.0, "momentum": 3.0,
                "macd_hist": 0.5, "macd_slope": 0.5,
                "rsi_center": 1.0, "rsi_accel": 1.0,
                "rsi_divergence": 1.0,
            }.get(feature, 1.0)
            return self._clip(value / max(scale, 1e-9))
        magnitude = abs(float(value))
        rank = bisect.bisect_right(ordered, magnitude) / len(ordered)
        return self._clip(math.copysign(rank, value) if value else 0.0)

    @staticmethod
    def _families(normalized: Mapping[str, float]) -> Dict[str, float]:
        return {
            "ema": UnifiedTrendAgent._clip(normalized.get("ema", 0.0)),
            "structure": UnifiedTrendAgent._clip(normalized.get("structure", 0.0)),
            "momentum": UnifiedTrendAgent._clip(normalized.get("momentum", 0.0)),
            "macd": UnifiedTrendAgent._clip(mean([normalized.get("macd_hist", 0.0), normalized.get("macd_slope", 0.0)])),
            "rsi": UnifiedTrendAgent._clip(mean([normalized.get("rsi_center", 0.0), normalized.get("rsi_accel", 0.0), normalized.get("rsi_divergence", 0.0)])),
        }

    def _calibrated_confidence(self, strength: float) -> float:
        logistic = (self.calibration or {}).get("logistic") or {}
        if logistic:
            a = float(logistic.get("a", 0.0) or 0.0)
            b = max(0.0, float(logistic.get("b", 0.0) or 0.0))
            z = max(-60.0, min(60.0, a + b * float(strength)))
            return max(50.0, min(95.0, 100.0 / (1.0 + math.exp(-z))))
        return max(50.0, min(95.0, 50.0 + 45.0 * float(strength)))

    def _load_calibration(self) -> Dict[str, Any] | None:
        try:
            raw = json.loads(self.calibration_path.read_text(encoding="utf-8"))
            supplied = str(raw.pop("checksum", "") or "")
            canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            expected = hashlib.sha256(canonical).hexdigest()
            if supplied != expected:
                return None
            if str(raw.get("version")) != str((self.config.get("unified_trend", {}) or {}).get("calibration_version", "unified_trend_v1")):
                return None
            minimum = int((self.config.get("unified_trend", {}) or {}).get("min_calibration_samples", 500) or 500)
            if int(raw.get("sample_count", 0) or 0) < minimum:
                return None
            for timeframe in CANONICAL_TIMEFRAMES:
                feature_map = (raw.get("features") or {}).get(timeframe) or {}
                if any(not feature_map.get(feature) for feature in FEATURE_NAMES):
                    return None
            return raw
        except Exception:
            return None

    def _market_regime(self, candles: List[Dict[str, Any]], edge: float) -> Dict[str, Any]:
        closes = [self._float(c.get("close")) for c in candles]
        atrs = [float(x) for x in calculate_atr(candles, 14) if x is not None]
        atr = atrs[-1] if atrs else 0.0
        rank = sum(1 for x in atrs if x <= atr) / len(atrs) * 100.0 if atrs else 50.0
        volatility = "HIGH" if rank >= 80 else "LOW" if rank <= 20 else "NORMAL"
        bb = calculate_bollinger_bands(closes, 20, 2).get("latest", {}) if closes else {}
        middle = self._float(bb.get("middle"), 0.0)
        width = (self._float(bb.get("upper")) - self._float(bb.get("lower"))) / max(abs(middle), 0.01) if middle else 0.0
        squeeze = width < 0.004
        strength = "STRONG" if abs(edge) >= 0.60 else "MODERATE" if abs(edge) >= 0.35 else "WEAK"
        phase = "SQUEEZE" if squeeze else "TRENDING" if strength in {"STRONG", "MODERATE"} else "RANGING"
        return {
            "trend_direction": "UP" if edge > 0 else "DOWN" if edge < 0 else "SIDEWAYS",
            "trend_strength": strength,
            "volatility_regime": volatility,
            "market_phase": phase,
            "atr": round(atr, 4),
            "atr_percentile": round(rank, 1),
            "bollinger_width": round(width, 6),
        }

    def _setup_context(
        self, by_tf: Mapping[str, Mapping[str, float]],
        direction: str, confidence: float,
    ) -> tuple[str, str, str]:
        def tf_edge(tf: str) -> float:
            vals = list((by_tf.get(tf) or {}).values())
            return mean(vals) if vals else 0.0
        htf = mean([tf_edge("4H"), tf_edge("1H")])
        ltf = mean([tf_edge("15m"), tf_edge("5m")])
        sign = 1 if direction == "BUY" else -1 if direction == "SELL" else 0
        if not sign:
            return "NO_TRADE", "NO_TRADE", "NOT_RECOMMENDED"
        htf_aligned = htf * sign > 0
        ltf_aligned = ltf * sign > 0
        if htf_aligned and ltf_aligned:
            setup = "TREND_CONTINUATION"
            timing = "VALID"
        elif htf_aligned and not ltf_aligned:
            setup = "PULLBACK_ENTRY"
            timing = "EARLY"
        elif not htf_aligned and ltf_aligned:
            setup = "REVERSAL_ATTEMPT"
            timing = "EARLY"
        else:
            setup = "MIXED_ALIGNMENT"
            timing = "LATE"
        permission = "ALLOWED" if confidence >= self.min_confidence and setup == "TREND_CONTINUATION" else "ALLOWED_WITH_CAUTION" if confidence >= self.min_confidence else "NOT_RECOMMENDED"
        return setup, timing, permission

    @staticmethod
    def _htf_direction(by_tf: Mapping[str, Mapping[str, float]]) -> str:
        vals: List[float] = []
        for tf in ("4H", "1H"):
            vals.extend((by_tf.get(tf) or {}).values())
        edge = mean(vals) if vals else 0.0
        return "BULLISH" if edge > 0 else "BEARISH" if edge < 0 else "SIDEWAYS"

    @staticmethod
    def _key_levels(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        levels = detect_support_resistance(candles[-120:], lookback=80) if candles else {"supports": [], "resistances": []}
        price = UnifiedTrendAgent._float(candles[-1].get("close")) if candles else 0.0
        supports = sorted([float(x) for x in levels.get("supports", []) if float(x) < price], reverse=True)[:3]
        resistances = sorted([float(x) for x in levels.get("resistances", []) if float(x) > price])[:3]
        return {
            "supports": [round(x, 2) for x in supports],
            "resistances": [round(x, 2) for x in resistances],
            "nearest_support": round(supports[0], 2) if supports else 0.0,
            "nearest_resistance": round(resistances[0], 2) if resistances else 0.0,
        }

    @staticmethod
    def _reasons(families: Mapping[str, float], edge: float, coherence: float, signal: str) -> List[str]:
        ordered = sorted(families.items(), key=lambda item: abs(item[1]), reverse=True)
        reasons = [f"{name} evidence {value:+.2f}" for name, value in ordered[:3]]
        reasons.append(f"Unified edge {edge:+.3f}; coherence {coherence * 100:.0f}%")
        if signal == "WAIT":
            reasons.append("Calibrated confidence below the canonical 67% agent bar or no coherent edge")
        return reasons

    @staticmethod
    def _reason_codes(signal: str, raw: str, regime: Mapping[str, Any], coherence: float) -> List[str]:
        codes = [f"UTE_RAW_{raw}", f"UTE_{str(regime.get('market_phase') or 'UNKNOWN')}"]
        codes.append("UTE_COHERENT" if coherence >= 0.60 else "UTE_INTERNAL_CONFLICT")
        if signal == "WAIT":
            codes.append("UTE_WAIT_BELOW_AGENT_BAR")
        else:
            codes.append(f"UTE_{signal}_QUALIFIED")
        return codes

    @staticmethod
    def _invalidations(signal: str, levels: Mapping[str, Any]) -> List[str]:
        if signal == "BUY" and levels.get("nearest_support"):
            return [f"Close below native-15m support {levels['nearest_support']:.2f}", "HTF evidence flips negative"]
        if signal == "SELL" and levels.get("nearest_resistance"):
            return [f"Close above native-15m resistance {levels['nearest_resistance']:.2f}", "HTF evidence flips positive"]
        return []

    def _wait(self, code: str, summary: str, **extra: Any) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "signal": "WAIT",
            "direction": "WAIT",
            "raw_direction": "WAIT",
            "confidence": 0,
            "raw_edge": 0.0,
            "coherence": 0.0,
            "coverage": 0.0,
            "family_scores": {},
            "timeframe_family_scores": {},
            "market_regime": {"trend_direction": "SIDEWAYS", "trend_strength": "WEAK", "volatility_regime": "UNKNOWN", "market_phase": "UNKNOWN"},
            "setup_type": "NO_TRADE",
            "timing_state": "NO_TRADE",
            "entry_permission": "NOT_RECOMMENDED",
            "trend_direction_from_htf": "SIDEWAYS",
            "reason_codes": [code],
            "warnings": [summary],
            "summary": summary,
            "data_quality": {"valid": False},
            **extra,
        }

    @staticmethod
    def _rsi_divergence(candles: List[Dict[str, Any]], rsi_series: List[Any]) -> float:
        if len(candles) < 40 or len(rsi_series) < len(candles):
            return 0.0
        swings = detect_swing_points(candles[-120:], lookback=3)
        offset = len(candles) - len(candles[-120:])
        try:
            lows = swings.get("lows", [])[-2:]
            if len(lows) == 2:
                ia, ib = offset + int(lows[0]["index"]), offset + int(lows[1]["index"])
                if ia < len(rsi_series) and ib < len(rsi_series):
                    pa, pb = float(lows[0]["price"]), float(lows[1]["price"])
                    ra, rb = float(rsi_series[ia] or 50), float(rsi_series[ib] or 50)
                    if (pb < pa and rb > ra) or (pb > pa and rb < ra):
                        return 1.0
            highs = swings.get("highs", [])[-2:]
            if len(highs) == 2:
                ia, ib = offset + int(highs[0]["index"]), offset + int(highs[1]["index"])
                if ia < len(rsi_series) and ib < len(rsi_series):
                    pa, pb = float(highs[0]["price"]), float(highs[1]["price"])
                    ra, rb = float(rsi_series[ia] or 50), float(rsi_series[ib] or 50)
                    if (pb > pa and rb < ra) or (pb < pa and rb > ra):
                        return -1.0
        except Exception:
            return 0.0
        return 0.0

    @staticmethod
    def _clip(value: float) -> float:
        return max(-1.0, min(1.0, float(value)))

    @staticmethod
    def _float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _last_number(values: Iterable[Any], default: float) -> float:
        for value in reversed(list(values)):
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return default
