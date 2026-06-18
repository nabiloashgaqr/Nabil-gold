"""Technical indicator calculations for the XAU/USD AI signal bot.

الدوال هنا مكتوبة بدون مكتبات مدفوعة، وتعتمد على Python القياسي فقط حتى تعمل
بسلاسة داخل GitHub Actions. جميع الدوال تقبل إما قائمة أسعار إغلاق أو قائمة
شموع OHLCV بالشكل: {open, high, low, close, volume, time}.
"""

from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List, Sequence

Number = float | int
Candle = Dict[str, Any]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _series(data: Sequence[Candle] | Sequence[Number], key: str = "close") -> List[float]:
    """Extract a float series from candles or a numeric list."""
    if not data:
        return []
    first = data[0]
    if isinstance(first, dict):
        return [_as_float(item.get(key)) for item in data if isinstance(item, dict)]  # type: ignore[arg-type]
    return [_as_float(item) for item in data]  # type: ignore[arg-type]


def calculate_sma(data: Sequence[Candle] | Sequence[Number], period: int) -> List[float | None]:
    """Calculate Simple Moving Average list."""
    values = _series(data)
    if period <= 0:
        raise ValueError("period must be positive")
    result: List[float | None] = []
    for index in range(len(values)):
        if index + 1 < period:
            result.append(None)
        else:
            result.append(mean(values[index + 1 - period : index + 1]))
    return result


def calculate_ema(data: Sequence[Candle] | Sequence[Number], period: int) -> List[float | None]:
    """Calculate Exponential Moving Average list."""
    values = _series(data)
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []

    multiplier = 2 / (period + 1)
    result: List[float | None] = []
    ema: float | None = None

    for index, price in enumerate(values):
        if index + 1 < period:
            result.append(None)
            continue
        if ema is None:
            ema = mean(values[index + 1 - period : index + 1])
        else:
            ema = (price - ema) * multiplier + ema
        result.append(ema)
    return result


def calculate_rsi(data: Sequence[Candle] | Sequence[Number], period: int = 14) -> List[float | None]:
    """Calculate RSI using Wilder smoothing."""
    values = _series(data)
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period + 1:
        return [None for _ in values]

    rsi_values: List[float | None] = [None] * len(values)
    gains: List[float] = []
    losses: List[float] = []

    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))

    avg_gain = mean(gains)
    avg_loss = mean(losses)

    def _rsi(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    rsi_values[period] = _rsi(avg_gain, avg_loss)

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = max(change, 0.0)
        loss = abs(min(change, 0.0))
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        rsi_values[i] = _rsi(avg_gain, avg_loss)

    return rsi_values


def calculate_macd(
    data: Sequence[Candle] | Sequence[Number],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Dict[str, List[float | None] | Dict[str, float]]:
    """Calculate MACD, signal line and histogram."""
    values = _series(data)
    if not values:
        empty: List[float | None] = []
        return {"macd": empty, "signal": empty, "histogram": empty, "latest": {"macd": 0.0, "signal": 0.0, "histogram": 0.0}}

    ema_fast = calculate_ema(values, fast)
    ema_slow = calculate_ema(values, slow)
    macd_line: List[float | None] = []
    for f_val, s_val in zip(ema_fast, ema_slow):
        macd_line.append(None if f_val is None or s_val is None else f_val - s_val)

    # Signal EMA over available MACD values while preserving indexes.
    valid_macd = [x for x in macd_line if x is not None]
    valid_signal = calculate_ema(valid_macd, signal) if valid_macd else []
    signal_line: List[float | None] = [None] * len(macd_line)
    valid_index = 0
    for i, m_val in enumerate(macd_line):
        if m_val is None:
            continue
        signal_line[i] = valid_signal[valid_index] if valid_index < len(valid_signal) else None
        valid_index += 1

    histogram: List[float | None] = []
    for m_val, s_val in zip(macd_line, signal_line):
        histogram.append(None if m_val is None or s_val is None else m_val - s_val)

    latest_macd = _last_number(macd_line)
    latest_signal = _last_number(signal_line)
    latest_hist = _last_number(histogram)
    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
        "latest": {"macd": latest_macd, "signal": latest_signal, "histogram": latest_hist},
    }


def calculate_bollinger_bands(
    data: Sequence[Candle] | Sequence[Number],
    period: int = 20,
    std_dev: float = 2.0,
) -> Dict[str, List[float | None] | Dict[str, float]]:
    """Calculate Bollinger Bands."""
    values = _series(data)
    upper: List[float | None] = []
    middle: List[float | None] = []
    lower: List[float | None] = []

    for index in range(len(values)):
        if index + 1 < period:
            upper.append(None)
            middle.append(None)
            lower.append(None)
            continue
        window = values[index + 1 - period : index + 1]
        mid = mean(window)
        deviation = pstdev(window) if len(window) > 1 else 0.0
        middle.append(mid)
        upper.append(mid + std_dev * deviation)
        lower.append(mid - std_dev * deviation)

    return {
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "latest": {"upper": _last_number(upper), "middle": _last_number(middle), "lower": _last_number(lower)},
    }


def calculate_atr(data: Sequence[Candle], period: int = 14) -> List[float | None]:
    """Calculate Average True Range."""
    if not data:
        return []
    true_ranges: List[float] = []
    previous_close: float | None = None
    for candle in data:
        high = _as_float(candle.get("high"))
        low = _as_float(candle.get("low"))
        close = _as_float(candle.get("close"))
        if previous_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(max(tr, 0.0))
        previous_close = close

    atr: List[float | None] = []
    current_atr: float | None = None
    for index, tr in enumerate(true_ranges):
        if index + 1 < period:
            atr.append(None)
        elif current_atr is None:
            current_atr = mean(true_ranges[index + 1 - period : index + 1])
            atr.append(current_atr)
        else:
            current_atr = ((current_atr * (period - 1)) + tr) / period
            atr.append(current_atr)
    return atr


def calculate_pivot_points(high: float, low: float, close: float) -> Dict[str, float]:
    """Calculate classic daily pivot points."""
    pivot = (high + low + close) / 3
    return {
        "pivot": pivot,
        "r1": (2 * pivot) - low,
        "s1": (2 * pivot) - high,
        "r2": pivot + (high - low),
        "s2": pivot - (high - low),
        "r3": high + 2 * (pivot - low),
        "s3": low - 2 * (high - pivot),
    }


def calculate_fibonacci_levels(high: float, low: float) -> Dict[str, float]:
    """Calculate Fibonacci retracement levels between high and low."""
    if high < low:
        high, low = low, high
    diff = high - low
    return {
        "23.6": high - diff * 0.236,
        "38.2": high - diff * 0.382,
        "50.0": high - diff * 0.500,
        "61.8": high - diff * 0.618,
        "78.6": high - diff * 0.786,
    }


def detect_swing_points(data: Sequence[Candle], lookback: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    """Detect swing highs and swing lows with a local window."""
    highs: List[Dict[str, Any]] = []
    lows: List[Dict[str, Any]] = []
    if len(data) < (lookback * 2) + 1:
        return {"highs": highs, "lows": lows}

    for index in range(lookback, len(data) - lookback):
        high = _as_float(data[index].get("high"))
        low = _as_float(data[index].get("low"))
        left = data[index - lookback : index]
        right = data[index + 1 : index + lookback + 1]
        if all(high >= _as_float(c.get("high")) for c in left + right):
            highs.append({"index": index, "price": high, "time": data[index].get("time")})
        if all(low <= _as_float(c.get("low")) for c in left + right):
            lows.append({"index": index, "price": low, "time": data[index].get("time")})
    return {"highs": highs, "lows": lows}


def detect_support_resistance(data: Sequence[Candle], lookback: int = 50) -> Dict[str, List[float]]:
    """Detect nearest support and resistance levels from recent swings."""
    recent = list(data[-lookback:]) if len(data) > lookback else list(data)
    swings = detect_swing_points(recent, lookback=3)
    supports = _cluster_levels([point["price"] for point in swings["lows"]])
    resistances = _cluster_levels([point["price"] for point in swings["highs"]])

    # Fallback to simple extrema if swings are scarce.
    if not supports and recent:
        lows = sorted({_as_float(c.get("low")) for c in recent})
        supports = lows[:3]
    if not resistances and recent:
        highs = sorted({_as_float(c.get("high")) for c in recent}, reverse=True)
        resistances = sorted(highs[:3])

    return {"supports": supports[:5], "resistances": resistances[:5]}


def _cluster_levels(levels: Iterable[float], tolerance: float = 1.5) -> List[float]:
    """Group nearby levels into rounded representative prices."""
    ordered = sorted(levels)
    if not ordered:
        return []
    clusters: List[List[float]] = [[ordered[0]]]
    for level in ordered[1:]:
        if abs(level - mean(clusters[-1])) <= tolerance:
            clusters[-1].append(level)
        else:
            clusters.append([level])
    # Stronger clusters first, then price order.
    ranked = sorted(clusters, key=lambda cluster: (-len(cluster), mean(cluster)))
    return [round(mean(cluster), 2) for cluster in ranked]


def _last_number(values: Sequence[float | None], default: float = 0.0) -> float:
    for value in reversed(values):
        if value is not None and not math.isnan(float(value)):
            return float(value)
    return default
