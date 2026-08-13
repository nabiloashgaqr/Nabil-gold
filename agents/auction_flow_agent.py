"""Auction Flow Agent: broker-tick pressure, value and acceptance/rejection."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Mapping

from agents.base_agent import BaseAgent
from services.auction_flow_store import AuctionFlowStore
from services.market_snapshot import build_market_snapshot
from services.timeframe_fusion import (
    CANONICAL_TIMEFRAMES,
    missing_required_timeframes,
    native_timeframe_payloads,
)
from utils.indicators import calculate_atr
from utils.instruments import point_size


class AuctionFlowAgent(BaseAgent):
    name = "auction_flow"

    def __init__(self, config: Dict[str, Any], **_kwargs: Any):
        super().__init__(config)
        cfg = config.get("auction_flow", {}) or {}
        self.cfg = cfg
        self.min_confidence = float(
            (config.get("signal_requirements", {}) or {}).get("agent_min_confidence", 67) or 67
        )
        self.store = AuctionFlowStore(str(cfg.get("storage_path") or "storage/auction_flow.sqlite3"))
        self.calibration_required = bool(cfg.get("calibration_required", True))
        self.calibration_path = Path(
            str(cfg.get("calibration_path") or "storage/model_calibration/auction_flow_v1.json")
        )
        self.calibration = self._load_calibration()

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        missing = missing_required_timeframes(market_data, self.config)
        if missing:
            return self._wait("NATIVE_TIMEFRAME_BOOK_INCOMPLETE", f"Missing native timeframe(s): {', '.join(missing)}")
        if self.calibration_required and not self.calibration:
            return self._wait("CALIBRATION_INVALID", f"Auction Flow calibration unavailable or invalid: {self.calibration_path}")

        symbol = str(market_data.get("symbol") or self.config.get("symbol") or "XAU/USD")
        pv = point_size(symbol, self.config)
        state = self.store.flow_state(
            session_timezone=str(self.cfg.get("session_timezone") or "Asia/Hebron"),
            point_value=pv,
            profile_bin_points=float(self.cfg.get("profile_bin_points", 10) or 10),
            value_area_pct=float(self.cfg.get("value_area_pct", 70) or 70),
        )
        invalid = self._data_problem(state, pv)
        if invalid:
            return self._wait(invalid[0], invalid[1], flow_state=state)

        health = state.get("health") or {}
        current = float(health.get("last_mid", 0) or 0)
        vwap = float(state.get("session_vwap", 0) or 0)
        poc = float(state.get("poc", 0) or 0)
        vah = float(state.get("vah", 0) or 0)
        val = float(state.get("val", 0) or 0)
        payloads = native_timeframe_payloads(market_data, self.config)
        atr15 = self._atr((payloads.get("15m") or {}).get("data") or [])
        if atr15 <= 0 or min(vwap, poc, vah, val, current) <= 0:
            return self._wait("SESSION_PROFILE_INCOMPLETE", "VWAP/POC/VAH/VAL or native 15m ATR is incomplete", flow_state=state)

        pressure = self._clip(mean([
            float(state.get("imbalance_60s", 0) or 0),
            float(state.get("imbalance_5m", 0) or 0),
        ]))
        timeframe_context = self._timeframe_value_context(payloads, vwap, poc)
        current_value = self._clip(mean([(current - vwap) / atr15, (current - poc) / atr15]))
        value_location = self._clip(mean([current_value, *timeframe_context.values()]))
        response, response_state = self._acceptance_rejection(state, pressure, vah, val)

        families = {
            "flow_pressure": pressure,
            "value_location": value_location,
            "acceptance_rejection": response,
        }
        values = list(families.values())
        base_edge = mean(values)
        absolute = sum(abs(v) for v in values)
        coherence = abs(sum(values)) / absolute if absolute > 0 else 0.0
        activity_ratio = float(state.get("activity_ratio", 0) or 0)
        activity_quality = max(0.50, min(1.0, activity_ratio))
        data_quality = 1.0 * activity_quality
        edge = self._clip(base_edge * coherence * data_quality)
        confidence = self._calibrated_confidence(abs(edge))
        raw_direction = "BUY" if edge > 0 else "SELL" if edge < 0 else "WAIT"
        signal = raw_direction if raw_direction in {"BUY", "SELL"} and confidence >= self.min_confidence else "WAIT"
        snapshot = build_market_snapshot(market_data, self.config)
        reason_codes = [f"AUCTION_{response_state}", f"AUCTION_RAW_{raw_direction}"]
        if signal == "WAIT":
            reason_codes.append("AUCTION_WAIT_BELOW_AGENT_BAR")
        else:
            reason_codes.append(f"AUCTION_{signal}_QUALIFIED")

        return {
            "agent": self.name,
            "signal": signal,
            "direction": signal,
            "raw_direction": raw_direction,
            "confidence": round(confidence, 1),
            "raw_edge": round(edge, 4),
            "base_edge": round(base_edge, 4),
            "coherence": round(coherence, 4),
            "data_quality_score": round(data_quality, 4),
            "state": response_state,
            "family_scores": {k: round(v, 4) for k, v in families.items()},
            "timeframe_value_context": {k: round(v, 4) for k, v in timeframe_context.items()},
            "session_vwap": round(vwap, 2),
            "poc": round(poc, 2),
            "vah": round(vah, 2),
            "val": round(val, 2),
            "tick_imbalance_60s": round(float(state.get("imbalance_60s", 0) or 0), 4),
            "tick_imbalance_5m": round(float(state.get("imbalance_5m", 0) or 0), 4),
            "activity_ratio": round(activity_ratio, 3),
            "spread_points": round(float(health.get("current_spread", 0) or 0) / max(pv, 1e-9), 2),
            "flow_health": health,
            "data_quality": snapshot.get("data_quality", {}),
            "verified_snapshot": snapshot,
            "calibration": {
                "version": (self.calibration or {}).get("version"),
                "samples": int((self.calibration or {}).get("sample_count", 0) or 0),
                "checksum_valid": bool(self.calibration),
            },
            "reason_codes": reason_codes,
            "reasons": self._reasons(families, response_state, current, vwap, vah, val, signal),
            "evidence": [
                {"name": k, "value": round(v, 4), "bias": "BULLISH" if v > 0 else "BEARISH" if v < 0 else "NEUTRAL"}
                for k, v in families.items()
            ],
            "invalidations": [
                "Acceptance failed and price returned through the value area",
                "Live tick pressure reversed with normal data quality",
            ] if signal in {"BUY", "SELL"} else [],
            "key_levels": {"vwap": round(vwap, 2), "poc": round(poc, 2), "vah": round(vah, 2), "val": round(val, 2)},
            "confidence_breakdown": {
                "calibrated_probability": round(confidence, 1),
                "edge_strength": round(abs(edge) * 100, 1),
                "coherence": round(coherence * 100, 1),
                "activity_quality": round(activity_quality * 100, 1),
            },
            "summary": (
                f"Auction Flow: {signal} {confidence:.1f}% · "
                f"{response_state} · edge {edge:+.3f}"
            ),
            "timestamp": self.now_iso(),
        }

    def _data_problem(self, state: Mapping[str, Any], point_value: float) -> tuple[str, str] | None:
        health = state.get("health") or {}
        tick_raw = health.get("last_tick_age_seconds")
        heartbeat_raw = health.get("heartbeat_age_seconds")
        tick_age = float(tick_raw) if tick_raw is not None else math.inf
        heartbeat_age = float(heartbeat_raw) if heartbeat_raw is not None else math.inf
        ticks5 = int(health.get("unique_ticks_5m", 0) or 0)
        spread_points = float(health.get("current_spread", 0) or 0) / max(point_value, 1e-9)
        max_tick_age = float(self.cfg.get("max_tick_age_seconds", 5) or 5)
        max_heartbeat = float(self.cfg.get("max_heartbeat_age_seconds", 10) or 10)
        min_ticks = int(self.cfg.get("min_unique_ticks_5m", 60) or 60)
        max_spread = float((self.config.get("filters", {}) or {}).get("max_spread_points", 5) or 5)
        if tick_age > max_tick_age:
            return "LIVE_TICK_FEED_STALE", f"MT5 live tick age {tick_age:.1f}s exceeds {max_tick_age:.0f}s"
        if heartbeat_age > max_heartbeat:
            return "COLLECTOR_HEARTBEAT_STALE", f"Auction collector heartbeat age {heartbeat_age:.1f}s exceeds {max_heartbeat:.0f}s"
        if ticks5 < min_ticks:
            return "INSUFFICIENT_LIVE_TICKS", f"Only {ticks5} unique ticks in 5m; need {min_ticks}"
        if spread_points > max_spread:
            return "SPREAD_GUARD", f"Spread {spread_points:.1f} pts exceeds existing {max_spread:.1f}-pt guard"
        if int(state.get("session_ticks", 0) or 0) <= 0:
            return "SESSION_PROFILE_INCOMPLETE", "Current auction session contains no valid tick activity"
        return None

    def _timeframe_value_context(
        self, payloads: Mapping[str, Mapping[str, Any]], vwap: float, poc: float,
    ) -> Dict[str, float]:
        result: Dict[str, float] = {}
        for timeframe in CANONICAL_TIMEFRAMES:
            candles = list((payloads.get(timeframe) or {}).get("data") or [])
            if len(candles) < 20:
                result[timeframe] = 0.0
                continue
            # Use the latest completed native candle; no timeframe is derived.
            candle = candles[-2] if len(candles) >= 2 else candles[-1]
            close = float(candle.get("close", 0) or 0)
            atr = self._atr(candles)
            result[timeframe] = self._clip(mean([(close - vwap) / max(atr, 1e-9), (close - poc) / max(atr, 1e-9)]))
        return result

    @staticmethod
    def _acceptance_rejection(
        state: Mapping[str, Any], pressure: float, vah: float, val: float,
    ) -> tuple[float, str]:
        rows = list(state.get("recent_minutes") or [])
        closes = [float(r.get("close", 0) or 0) for r in rows]
        if len(closes) >= 3 and all(c > vah for c in closes[-3:]) and pressure > 0:
            return 1.0, "INITIATIVE_BUY_ACCEPTANCE"
        if len(closes) >= 3 and all(c < val for c in closes[-3:]) and pressure < 0:
            return -1.0, "INITIATIVE_SELL_ACCEPTANCE"
        if rows and any(float(r.get("high", 0) or 0) > vah for r in rows[-5:]) and closes[-1] < vah and pressure < 0:
            return -1.0, "RESPONSIVE_SELL_REJECTION"
        if rows and any(float(r.get("low", 0) or 0) < val for r in rows[-5:]) and closes[-1] > val and pressure > 0:
            return 1.0, "RESPONSIVE_BUY_REJECTION"
        return 0.0, "BALANCED_AUCTION"

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
            if hashlib.sha256(canonical).hexdigest() != supplied:
                return None
            expected_version = str(self.cfg.get("calibration_version", "auction_flow_v1"))
            minimum = int(self.cfg.get("min_calibration_samples", 300) or 300)
            if str(raw.get("version")) != expected_version or int(raw.get("sample_count", 0) or 0) < minimum:
                return None
            logistic = raw.get("logistic") or {}
            if "a" not in logistic or "b" not in logistic:
                return None
            return raw
        except Exception:
            return None

    @staticmethod
    def _atr(candles: List[Dict[str, Any]]) -> float:
        values = [float(x) for x in calculate_atr(candles, 14) if x is not None]
        return values[-1] if values else 0.0

    @staticmethod
    def _clip(value: float) -> float:
        return max(-1.0, min(1.0, float(value)))

    @staticmethod
    def _reasons(
        families: Mapping[str, float], state: str, current: float,
        vwap: float, vah: float, val: float, signal: str,
    ) -> List[str]:
        ordered = sorted(families.items(), key=lambda item: abs(item[1]), reverse=True)
        reasons = [f"{name} {value:+.2f}" for name, value in ordered]
        reasons.append(f"{state}; price {current:.2f}, VWAP {vwap:.2f}, VAH {vah:.2f}, VAL {val:.2f}")
        if signal == "WAIT":
            reasons.append("Calibrated confidence below the canonical 67% agent bar or auction is balanced")
        return reasons

    def _wait(self, code: str, summary: str, **extra: Any) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "signal": "WAIT",
            "direction": "WAIT",
            "raw_direction": "WAIT",
            "confidence": 0,
            "raw_edge": 0.0,
            "coherence": 0.0,
            "state": code,
            "family_scores": {},
            "reason_codes": [code],
            "warnings": [summary],
            "summary": summary,
            "data_quality": {"valid": False},
            **extra,
        }
