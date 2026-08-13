"""Canonical native-timeframe input and one-vote fusion helpers.

Every core analysis agent reads the same frozen 5m/15m/1H/4H candle book.
The market-data service fetches that book once per cycle; agents never make
independent network calls and never resample one timeframe into another.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping

CANONICAL_TIMEFRAMES = ("5m", "15m", "1H", "4H")


def _direction(value: Any) -> str:
    side = str(value or "WAIT").upper()
    if side in {"NEUTRAL", "HOLD", "NO_TRADE", "NONE", ""}:
        return "WAIT"
    return side if side in {"BUY", "SELL", "WAIT"} else "WAIT"


def native_timeframe_payloads(
    market_data: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Return native canonical payloads without deriving missing candles.

    Production config sets ``require_native=true`` and ``require_all=true``.
    The top-level fallback exists only for backward-compatible unit tests and
    manual callers that provide a single historical payload.
    """
    cfg = ((config or {}).get("all_agents_timeframes") or {}) if isinstance(config, Mapping) else {}
    required = tuple(str(x) for x in (cfg.get("required") or CANONICAL_TIMEFRAMES))
    require_native = bool(cfg.get("require_native", False))
    tf_book = market_data.get("timeframes") or {}
    payloads: Dict[str, Dict[str, Any]] = {}
    if isinstance(tf_book, Mapping):
        for timeframe in required:
            payload = tf_book.get(timeframe)
            if not isinstance(payload, Mapping):
                continue
            if require_native and payload.get("resampled_from"):
                continue
            candles = payload.get("data") or []
            if not isinstance(candles, list) or not candles:
                continue
            merged = dict(payload)
            merged.setdefault("symbol", market_data.get("symbol"))
            merged["timeframe"] = timeframe
            # Carry the frozen book for snapshot/data-quality consumers, but
            # the single-timeframe core reads only merged["data"].
            merged["timeframes"] = tf_book
            payloads[timeframe] = merged

    if not payloads and not bool(cfg.get("require_all", False)):
        candles = market_data.get("data") or []
        if isinstance(candles, list) and candles:
            timeframe = str(market_data.get("timeframe") or "15m")
            payloads[timeframe] = dict(market_data)
    return payloads


def missing_required_timeframes(
    market_data: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> list[str]:
    cfg = ((config or {}).get("all_agents_timeframes") or {}) if isinstance(config, Mapping) else {}
    required = tuple(str(x) for x in (cfg.get("required") or CANONICAL_TIMEFRAMES))
    payloads = native_timeframe_payloads(market_data, config)
    return [tf for tf in required if tf not in payloads]


def fuse_timeframe_results(
    agent_name: str,
    results: Mapping[str, Mapping[str, Any]],
    *,
    required_timeframes: Iterable[str] = CANONICAL_TIMEFRAMES,
    primary_timeframe: str = "15m",
) -> Dict[str, Any]:
    """Fuse per-timeframe reads into one agent direction/confidence.

    There are no hidden timeframe weights. Directional frames contribute their
    signed confidence equally. Agreement is measured by ``coherence`` and the
    fraction of directional frames by ``directional_coverage``. Three aligned
    frames retain 93.75% of their average confidence; two retain 87.5%; one
    retains 81.25%. Opposite frames cancel through coherence.
    """
    required = tuple(str(x) for x in required_timeframes)
    signed: list[float] = []
    directional_confidences: list[float] = []
    all_confidences: list[float] = []
    compact: Dict[str, Dict[str, Any]] = {}
    for timeframe in required:
        result = results.get(timeframe) or {}
        side = _direction(result.get("signal") or result.get("direction"))
        try:
            confidence = max(0.0, min(100.0, float(result.get("confidence", 0) or 0)))
        except (TypeError, ValueError):
            confidence = 0.0
        value = confidence / 100.0 if side == "BUY" else -confidence / 100.0 if side == "SELL" else 0.0
        signed.append(value)
        all_confidences.append(confidence)
        if side in {"BUY", "SELL"}:
            directional_confidences.append(confidence)
        compact[timeframe] = {
            "direction": side,
            "confidence": round(confidence, 1),
            "summary": str(result.get("summary") or "")[:300],
            "signals": list(result.get("signals") or result.get("reasons") or [])[:4],
        }

    directional_count = len(directional_confidences)
    net = sum(signed)
    absolute = sum(abs(v) for v in signed)
    coherence = abs(net) / absolute if absolute > 0 else 0.0
    directional_coverage = directional_count / max(len(required), 1)
    average_directional = (
        sum(directional_confidences) / directional_count
        if directional_count else 0.0
    )
    # Participation penalty: all four=1.00, three=.9375, two=.875, one=.8125.
    participation = 0.75 + 0.25 * directional_coverage
    confidence = average_directional * coherence * participation
    direction = "BUY" if net > 1e-9 else "SELL" if net < -1e-9 else "WAIT"
    if directional_count == 0:
        # All frames explicitly returned NEUTRAL/WAIT. Preserve their neutral
        # confidence for observability instead of displaying a misleading 0%.
        # It remains non-directional and can never qualify as a BUY/SELL vote.
        confidence = min(55.0, sum(all_confidences) / max(len(all_confidences), 1))
    elif direction == "WAIT":
        confidence = min(55.0, confidence)

    representative_key = primary_timeframe
    primary_result = results.get(primary_timeframe) or {}
    if direction in {"BUY", "SELL"} and _direction(primary_result.get("signal") or primary_result.get("direction")) != direction:
        for candidate_tf in ("15m", "1H", "5m", "4H"):
            candidate_result = results.get(candidate_tf) or {}
            if _direction(candidate_result.get("signal") or candidate_result.get("direction")) == direction:
                representative_key = candidate_tf
                break
    representative = deepcopy(results.get(representative_key) or next(iter(results.values()), {}))
    representative["agent"] = agent_name
    representative["representative_timeframe"] = representative_key
    representative["direction"] = direction if direction in {"BUY", "SELL"} else "NEUTRAL"
    representative["signal"] = direction if direction in {"BUY", "SELL"} else "WAIT"
    representative["confidence"] = round(max(0.0, min(95.0, confidence)), 1)
    representative["timeframe_analysis"] = compact
    representative["timeframe_fusion"] = {
        "required": list(required),
        "available": [tf for tf in required if tf in results],
        "directional_count": directional_count,
        "directional_coverage": round(directional_coverage, 4),
        "coherence": round(coherence, 4),
        "signed_net": round(net / max(len(required), 1), 4),
        "average_directional_confidence": round(average_directional, 1),
        "participation_factor": round(participation, 4),
        "method": "equal_native_timeframes_one_vote",
    }
    representative["confidence_breakdown"] = {
        **(representative.get("confidence_breakdown") or {}),
        "timeframe_coherence": round(coherence * 100.0, 1),
        "timeframe_coverage": round(directional_coverage * 100.0, 1),
    }
    representative["summary"] = (
        f"Multi-frame fusion: {direction} {representative['confidence']:.1f}% · "
        f"coherence {coherence * 100:.0f}% · directional frames {directional_count}/{len(required)}"
    )
    return representative


def native_timeframe_failure(agent_name: str, missing: Iterable[str]) -> Dict[str, Any]:
    missing_list = [str(x) for x in missing]
    return {
        "agent": agent_name,
        "direction": "WAIT",
        "signal": "WAIT",
        "confidence": 0,
        "timeframe_analysis": {},
        "reason_codes": [f"MISSING_NATIVE_{tf.upper()}" for tf in missing_list],
        "warnings": [f"Missing native timeframe(s): {', '.join(missing_list)}"],
        "summary": f"Native timeframe book incomplete: {', '.join(missing_list)}",
        "data_quality": {"valid": False, "missing_timeframes": missing_list},
    }
