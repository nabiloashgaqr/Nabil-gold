"""Instrument metadata and point conversion helpers.

The project stores PnL/SL/TP distances in broker-style *points*:
- Gold XAU/USD: 1 point = 0.10 USD (10 points = 1.00)
- FX non-JPY: 1 point = 0.00001 (10 points = 1 pip)
- FX JPY pairs: 1 point = 0.001 (10 points = 1 pip)
- WTI oil: 1 point = 0.01 USD
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


DEFAULT_INSTRUMENTS: Dict[str, Dict[str, Any]] = {
    "XAU/USD": {
        "symbol": "XAU/USD", "name": "Gold", "category": "metal",
        "point_size": 0.10, "price_decimals": 2,
        "min_sl_distance_points": 300, "trailing_distance": 100, "trailing_step": 30,
        "early_breakeven_points": 100, "duplicate_zone_points": 50,
    },
    "WTI/USD": {
        "symbol": "WTI/USD", "name": "WTI Crude Oil", "category": "oil",
        "point_size": 0.01, "price_decimals": 2,
        # Oil uses 0.01 USD per point. A balanced intraday floor is $1.20,
        # with earlier protection and a tighter trailing profile than gold.
        "min_sl_distance_points": 120, "trailing_distance": 70, "trailing_step": 25,
        "early_breakeven_points": 70, "duplicate_zone_points": 100,
    },
}

ALIASES = {
    "XAUUSD": "XAU/USD",
    "GOLD": "XAU/USD",
    "WTI": "WTI/USD",
    "USOIL": "WTI/USD",
    "OIL": "WTI/USD",
    "OIL/W": "WTI/USD",
    "WTICO_USD": "WTI/USD",
}


def normalize_symbol(symbol: Any) -> str:
    text = str(symbol or "XAU/USD").strip().upper().replace(" ", "")
    if text in ALIASES:
        return ALIASES[text]
    if "/" not in text and len(text) == 6:
        text = f"{text[:3]}/{text[3:]}"
    return text


def instrument_map(config: Dict[str, Any] | None = None) -> Dict[str, Dict[str, Any]]:
    mapping = deepcopy(DEFAULT_INSTRUMENTS)
    for item in (config or {}).get("instruments", []) or []:
        if not isinstance(item, dict):
            continue
        symbol = normalize_symbol(item.get("symbol"))
        base = mapping.get(symbol, {"symbol": symbol})
        base.update(item)
        base["symbol"] = symbol
        mapping[symbol] = base
    return mapping


def get_instrument(config: Dict[str, Any] | None = None, symbol: Any = None) -> Dict[str, Any]:
    symbol = normalize_symbol(symbol or (config or {}).get("symbol") or "XAU/USD")
    mapping = instrument_map(config)
    return deepcopy(mapping.get(symbol, {"symbol": symbol, "point_size": 0.00001, "price_decimals": 5}))


def point_size(symbol: Any = None, config: Dict[str, Any] | None = None) -> float:
    return float(get_instrument(config, symbol).get("point_size", 0.00001) or 0.00001)


def price_decimals(symbol: Any = None, config: Dict[str, Any] | None = None) -> int:
    return int(get_instrument(config, symbol).get("price_decimals", 5) or 5)


def points_to_price(points: float, symbol: Any = None, config: Dict[str, Any] | None = None) -> float:
    return float(points or 0) * point_size(symbol, config)


def price_to_points(price_delta: float, symbol: Any = None, config: Dict[str, Any] | None = None) -> float:
    ps = point_size(symbol, config)
    return float(price_delta or 0) / ps if ps else 0.0


def enabled_instruments(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    configured = config.get("symbols") or config.get("enabled_symbols")
    if not configured:
        return [get_instrument(config, config.get("symbol", "XAU/USD"))]
    result: List[Dict[str, Any]] = []
    mapping = instrument_map(config)
    for item in configured:
        if isinstance(item, str):
            symbol = normalize_symbol(item)
            enabled = True
            overrides: Dict[str, Any] = {}
        elif isinstance(item, dict):
            symbol = normalize_symbol(item.get("symbol"))
            enabled = bool(item.get("enabled", True))
            overrides = dict(item)
        else:
            continue
        if not enabled:
            continue
        spec = deepcopy(mapping.get(symbol, {"symbol": symbol}))
        spec.update(overrides)
        spec["symbol"] = symbol
        result.append(spec)
    return result or [get_instrument(config, config.get("symbol", "XAU/USD"))]


def config_for_instrument(base_config: Dict[str, Any], instrument: Dict[str, Any]) -> Dict[str, Any]:
    cfg = deepcopy(base_config)
    symbol = normalize_symbol(instrument.get("symbol"))
    spec = get_instrument(base_config, symbol)
    spec.update(instrument)
    spec["symbol"] = symbol
    cfg["symbol"] = symbol
    cfg["instrument"] = spec

    cfg.setdefault("risk_settings", {})["min_sl_distance_points"] = spec.get(
        "min_sl_distance_points", cfg.get("risk_settings", {}).get("min_sl_distance_points", 300)
    )
    cfg.setdefault("trailing_stop", {})["trailing_distance"] = spec.get(
        "trailing_distance", cfg.get("trailing_stop", {}).get("trailing_distance", 100)
    )
    cfg["trailing_stop"]["trailing_step"] = spec.get(
        "trailing_step", cfg.get("trailing_stop", {}).get("trailing_step", 30)
    )
    cfg["trailing_stop"]["early_breakeven_points"] = spec.get(
        "early_breakeven_points", cfg.get("trailing_stop", {}).get("early_breakeven_points", 100)
    )
    cfg.setdefault("duplicate_signal_filter", {})["price_zone_points"] = spec.get(
        "duplicate_zone_points", cfg.get("duplicate_signal_filter", {}).get("price_zone_points", 50)
    )
    return cfg
