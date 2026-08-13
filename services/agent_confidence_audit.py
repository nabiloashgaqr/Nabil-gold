"""Independent arithmetic verification of the five agents' final confidence."""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping

CORE = ("unified_trend", "classical", "smc", "price_action", "auction_flow")
FUSION = ("classical", "smc", "price_action")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _side(value: Any) -> str:
    side = str(value or "WAIT").upper()
    return "WAIT" if side in {"NEUTRAL", "HOLD", "NO_TRADE", "NONE", ""} else side


def _calibrated_expected(result: Mapping[str, Any], calibration: Mapping[str, Any] | None) -> float | None:
    logistic = (calibration or {}).get("logistic") or {}
    if "a" not in logistic or "b" not in logistic:
        return None
    a = _f(logistic.get("a"))
    b = max(0.0, _f(logistic.get("b")))
    strength = abs(_f(result.get("raw_edge")))
    z = max(-60.0, min(60.0, a + b * strength))
    return max(50.0, min(95.0, 100.0 / (1.0 + math.exp(-z))))


def _fusion_expected(result: Mapping[str, Any]) -> Dict[str, Any] | None:
    reads = result.get("timeframe_analysis") or {}
    required = tuple(((result.get("timeframe_fusion") or {}).get("required") or ("5m", "15m", "1H", "4H")))
    if not isinstance(reads, Mapping) or not reads:
        return None
    signed = []
    directional_conf = []
    all_conf = []
    for timeframe in required:
        read = reads.get(timeframe) or {}
        confidence = max(0.0, min(100.0, _f(read.get("confidence"))))
        side = _side(read.get("direction") or read.get("signal"))
        signed.append(confidence / 100.0 if side == "BUY" else -confidence / 100.0 if side == "SELL" else 0.0)
        all_conf.append(confidence)
        if side in {"BUY", "SELL"}:
            directional_conf.append(confidence)
    net = sum(signed)
    absolute = sum(abs(v) for v in signed)
    coherence = abs(net) / absolute if absolute > 0 else 0.0
    directional_count = len(directional_conf)
    coverage = directional_count / max(len(required), 1)
    participation = 0.75 + 0.25 * coverage
    average = sum(directional_conf) / directional_count if directional_count else 0.0
    direction = "BUY" if net > 1e-9 else "SELL" if net < -1e-9 else "WAIT"
    if directional_count == 0:
        confidence = min(55.0, sum(all_conf) / max(len(all_conf), 1))
    else:
        confidence = average * coherence * participation
        if direction == "WAIT":
            confidence = min(55.0, confidence)
    return {
        "direction": direction,
        "confidence": confidence,
        "coherence": coherence,
        "directional_count": directional_count,
        "participation_factor": participation,
    }


def audit_agent_book(
    results: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any],
    calibrations: Mapping[str, Mapping[str, Any] | None] | None = None,
) -> Dict[str, Any]:
    """Recompute each final confidence independently and report mismatches."""
    min_conf = _f((config.get("signal_requirements") or {}).get("agent_min_confidence"), 67.0)
    weights = config.get("agent_weights") or {}
    calibrations = calibrations or {}
    shared_ids = {
        str((results.get(name, {}).get("shared_market_data") or {}).get("snapshot_id") or "")
        for name in CORE if isinstance(results.get(name), Mapping)
    }
    shared_ids.discard("")
    rows: Dict[str, Any] = {}
    overall = True
    for name in CORE:
        result = results.get(name) or {}
        actual = _f(result.get("confidence"))
        actual_side = _side(result.get("signal") or result.get("direction"))
        expected = None
        expected_side = actual_side
        method = "unknown"
        if name in FUSION:
            fusion = _fusion_expected(result)
            if fusion:
                expected = _f(fusion.get("confidence"))
                expected_side = str(fusion.get("direction") or "WAIT")
                method = "independent_timeframe_fusion_recalculation"
        elif name in {"unified_trend", "auction_flow"}:
            expected = _calibrated_expected(result, calibrations.get(name))
            method = "independent_logistic_calibration_recalculation"
        difference = abs(actual - expected) if expected is not None else None
        arithmetic_ok = difference is not None and difference <= 0.11
        if expected is None:
            arithmetic_ok = False
        direction_ok = actual_side == expected_side or (
            actual_side == "WAIT" and expected_side in {"BUY", "SELL"} and actual < min_conf
        )
        qualified = actual_side in {"BUY", "SELL"} and actual >= min_conf
        row_ok = arithmetic_ok and direction_ok and 0 <= actual <= 100
        overall = overall and row_ok
        rows[name] = {
            "ok": row_ok,
            "method": method,
            "actual_direction": actual_side,
            "expected_raw_direction": expected_side,
            "actual_confidence": round(actual, 4),
            "recalculated_confidence": round(expected, 4) if expected is not None else None,
            "difference": round(difference, 6) if difference is not None else None,
            "arithmetic_ok": arithmetic_ok,
            "direction_ok": direction_ok,
            "qualified": qualified,
            "weight": _f(weights.get(name)),
        }
    weight_sum = sum(_f(weights.get(name)) for name in CORE)
    shared_ok = len(shared_ids) == 1
    overall = overall and abs(weight_sum - 1.0) < 1e-9 and shared_ok
    return {
        "ok": overall,
        "agent_min_confidence": min_conf,
        "weights_sum": round(weight_sum, 8),
        "weights_ok": abs(weight_sum - 1.0) < 1e-9,
        "shared_snapshot_ids": sorted(shared_ids),
        "shared_source_ok": shared_ok,
        "agents": rows,
    }
