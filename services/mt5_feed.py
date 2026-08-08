"""MT5 price feed (demo branch, phase 1).

Returns payloads with the EXACT contract of
services.market_data.MarketDataService payloads:
    {"data": [{"time","open","high","low","close"}, ...], "source": "mt5", ...}

MetaTrader5 is imported LAZILY so paper mode and the test suite run without
the terminal installed. Candle times are normalised from MT5 server time to
UTC by a snapped offset (mt5.time() vs epoch, snapped to 900 s).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SNAP_SECONDS = 900
_offset_cache: Dict[str, int] = {}


def _mt5():
    import MetaTrader5 as mt5  # noqa: WPS113 (lazy on purpose)
    return mt5


def connected() -> bool:
    try:
        return bool(_mt5().terminal_info() is not None)
    except Exception:  # noqa: BLE001 - module missing / terminal down
        return False


def initialize() -> bool:
    mt5 = _mt5()
    for attempt in range(3):
        try:
            path = os.environ.get("MT5_PATH") or None
            if not mt5.initialize(path=path) if path else mt5.initialize():
                raise RuntimeError(mt5.last_error())
            login = int(os.environ.get("MT5_LOGIN") or 0)
            if login:
                ok = mt5.login(
                    login,
                    password=os.environ.get("MT5_PASSWORD") or "",
                    server=os.environ.get("MT5_SERVER") or "",
                )
                if not ok:
                    raise RuntimeError(mt5.last_error())
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("MT5 init attempt %d failed: %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return False


def time_offset_seconds() -> int:
    """MT5 server time minus UTC epoch, snapped to 15-minute steps."""
    key = "off"
    if key in _offset_cache:
        return _offset_cache[key]
    try:
        raw = _mt5().time() - int(time.time())
        off = int(round(raw / _SNAP_SECONDS) * _SNAP_SECONDS)
    except Exception:  # noqa: BLE001
        off = 0
    _offset_cache[key] = off
    return off


def _timeframe_const(mt5, timeframe_min: int):
    return {5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15,
            60: mt5.TIMEFRAME_H1, 240: mt5.TIMEFRAME_H4}.get(timeframe_min)


def _tf_minutes(timeframe: str) -> int:
    tf = str(timeframe).lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    return 5


def get_candles(
    symbol: str,
    timeframe: str = "5m",
    count: int = 220,
    symbol_map: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """None on any failure so the caller falls back to TwelveData."""
    if not connected() and not initialize():
        return None
    mt5 = _mt5()
    mt5_symbol = (symbol_map or {}).get(symbol, symbol.replace("/", ""))
    tf_const = _timeframe_const(mt5, _tf_minutes(timeframe))
    if tf_const is None:
        return None
    try:
        rates = mt5.copy_rates_from_pos(mt5_symbol, tf_const, 0, count)
    except Exception as exc:  # noqa: BLE001
        logger.warning("MT5 copy_rates failed for %s: %s", mt5_symbol, exc)
        return None
    if rates is None or len(rates) == 0:
        return None
    off = time_offset_seconds()
    data = [
        {
            "time": int(r["time"]) - off,
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
        }
        for r in rates
    ]
    tf_min = _tf_minutes(timeframe)
    fresh = (int(time.time()) - data[-1]["time"]) <= 2 * tf_min * 60
    return {
        "data": data,
        "source": "mt5",
        "current_price": data[-1]["close"],
        "last_updated": data[-1]["time"],
        "source_integrity": {
            "source": "mt5",
            "source_type": "historical_ohlc",
            "grade": "HIGH" if (len(data) >= count and fresh) else "MEDIUM",
            "signal_generation": fresh,
            "pending_activation": fresh,
        },
    }
