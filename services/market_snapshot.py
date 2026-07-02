"""Verified market snapshot builder.

Agent Upgrade Phase A: provide one compact source-of-truth payload that agents
can attach to their outputs without changing the orchestration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from utils.indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    detect_support_resistance,
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _last(values: List[Any], default: float = 0.0) -> float:
    for value in reversed(values or []):
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return default


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _nearest_below(price: float, levels: List[float]) -> float:
    below = sorted([_f(x) for x in levels if _f(x) < price], reverse=True)
    return below[0] if below else 0.0


def _nearest_above(price: float, levels: List[float]) -> float:
    above = sorted([_f(x) for x in levels if _f(x) > price])
    return above[0] if above else 0.0


def build_market_snapshot(market_data: Dict[str, Any], config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build a compact verified snapshot from existing market data.

    The snapshot is best-effort and non-fatal: missing inputs are reported in
    ``data_quality.missing_fields`` instead of raising.
    """
    config = config or {}
    candles = market_data.get("data", market_data.get("candles", [])) or []
    indicators = market_data.get("indicators", {}) or {}
    symbol = str(market_data.get("symbol") or config.get("symbol") or "XAU/USD")
    timeframe = str(market_data.get("timeframe") or market_data.get("interval") or "unknown")
    source = str(market_data.get("source") or market_data.get("data_source") or "market_data_service")
    closes = [_f(c.get("close")) for c in candles if isinstance(c, dict)]
    latest = candles[-1] if candles and isinstance(candles[-1], dict) else {}
    current_price = _f(market_data.get("current_price") or indicators.get("current_price") or latest.get("close"), _last(closes, 0.0))

    ema = {}
    for period in (8, 21, 50, 100, 200):
        ema[f"ema_{period}"] = round(_f(indicators.get(f"ema_{period}"), _last(calculate_ema(closes, period), current_price)), 4) if closes else 0.0
    rsi_series = calculate_rsi(closes, 14) if closes else []
    macd = calculate_macd(closes) if closes else {"latest": {"histogram": 0}}
    atr_series = calculate_atr(candles, 14) if candles else []
    bb = calculate_bollinger_bands(closes, 20, 2) if closes else {"latest": {}}
    levels = detect_support_resistance(candles[-120:], lookback=min(80, len(candles))) if candles else {"supports": [], "resistances": []}

    latest_time = _parse_time(latest.get("time") or latest.get("timestamp") or market_data.get("timestamp"))
    now = datetime.now(timezone.utc)
    stale_minutes = round((now - latest_time).total_seconds() / 60, 1) if latest_time else None
    missing = []
    if not candles:
        missing.append("candles")
    if not current_price:
        missing.append("current_price")
    if latest_time is None:
        missing.append("timestamp")

    bb_latest = bb.get("latest", {}) or {}
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": latest_time.isoformat().replace("+00:00", "Z") if latest_time else now.isoformat().replace("+00:00", "Z"),
        "current_price": round(current_price, 4),
        "latest_candle": {
            "open": _f(latest.get("open")),
            "high": _f(latest.get("high")),
            "low": _f(latest.get("low")),
            "close": _f(latest.get("close"), current_price),
        },
        "indicators": {
            **ema,
            "rsi": round(_f(indicators.get("rsi"), _last(rsi_series, 50)), 2),
            "macd_histogram": round(_f(indicators.get("macd_histogram"), (macd.get("latest") or {}).get("histogram", 0)), 5),
            "atr": round(_f(indicators.get("atr"), _last(atr_series, 0)), 4),
            "bollinger": {
                "upper": round(_f(bb_latest.get("upper")), 4),
                "middle": round(_f(bb_latest.get("middle")), 4),
                "lower": round(_f(bb_latest.get("lower")), 4),
            },
        },
        "key_levels": {
            "nearest_support": round(_nearest_below(current_price, levels.get("supports", [])), 4),
            "nearest_resistance": round(_nearest_above(current_price, levels.get("resistances", [])), 4),
        },
        "data_quality": {
            "source": source,
            "stale_minutes": stale_minutes,
            "freshness": "UNKNOWN" if stale_minutes is None else "STALE" if stale_minutes > 30 else "OK",
            "synthetic": bool(market_data.get("synthetic", False)),
            "missing_fields": missing,
            "candles": len(candles),
        },
    }
