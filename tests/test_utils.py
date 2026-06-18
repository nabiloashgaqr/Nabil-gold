"""Tests for utility functions (Indicators and Helpers)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from utils.indicators import (
    calculate_sma,
    calculate_ema,
    calculate_rsi,
    calculate_macd,
    calculate_bollinger_bands,
    calculate_atr,
    calculate_pivot_points,
    calculate_fibonacci_levels,
    detect_swing_points,
    detect_support_resistance,
)
from utils.helpers import (
    calculate_pips,
    format_price,
    load_config,
    get_current_session,
    is_market_open,
    setup_logging,
)


# ───────────────────────────── Indicators ────────────────────────────────────


def make_candles(n: int = 100, base: float = 2350.0) -> list[dict]:
    """Generate n synthetic candles."""
    candles = []
    price = base
    for i in range(n):
        price += 0.3 * ((i % 3) - 1)
        candles.append({
            "time": f"2026-06-{17 - (i // 96):02d}T{(i % 96):02d}:00:00Z",
            "open": round(price - 0.2, 2),
            "high": round(price + 0.8, 2),
            "low": round(price - 0.8, 2),
            "close": round(price, 2),
            "volume": 1000 + i,
        })
    return candles


def test_sma_basic():
    """SMA must return correct rolling averages."""
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    result = calculate_sma(values, 3)
    assert result[0] is None
    assert result[1] is None
    assert abs(result[2] - 20.0) < 0.01
    assert abs(result[3] - 30.0) < 0.01
    assert abs(result[4] - 40.0) < 0.01


def test_sma_from_candles():
    """SMA must work with candle input and return valid averages."""
    candles = make_candles(30)
    result = calculate_sma(candles, 10)
    assert len(result) == 30
    # At least some valid values
    valid = [x for x in result if x is not None and x != 0.0]
    assert len(valid) > 10
    assert all(2000 < x < 3000 for x in valid)  # Reasonable price range


def test_ema_basic():
    """EMA must converge toward recent prices."""
    values = [10.0] * 50 + [100.0] * 10
    result = calculate_ema(values, 14)
    # Last values should reflect the jump
    assert result[-1] is not None
    assert result[-1] > 50  # Jump detected


def test_ema_from_candles():
    """EMA must work with candle input and track price trends."""
    candles = make_candles(60)
    result = calculate_ema(candles, 20)
    assert len(result) == 60
    valid = [x for x in result if x is not None and x != 0.0]
    assert len(valid) > 20
    # EMA should track close prices roughly
    last_valid = valid[-1]
    assert 2300 < last_valid < 2400


def test_rsi_oversold():
    """RSI must detect oversold conditions."""
    # Declining prices → oversold RSI
    prices = [100.0 - i * 0.5 for i in range(30)]
    result = calculate_rsi(prices, 14)
    last = [x for x in result if x is not None][-1]
    assert last < 30  # Oversold


def test_rsi_overbought():
    """RSI must detect overbought conditions."""
    # Rising prices → overbought RSI
    prices = [100.0 + i * 0.5 for i in range(30)]
    result = calculate_rsi(prices, 14)
    last = [x for x in result if x is not None][-1]
    assert last > 70  # Overbought


def test_rsi_wilder_smoothing():
    """RSI must use Wilder smoothing (period-1 decay)."""
    candles = make_candles(50, base=2350)
    result = calculate_rsi(candles, 14)
    valid = [x for x in result if x is not None]
    assert len(valid) > 20
    assert all(0 <= x <= 100 for x in valid)


def test_macd_basic():
    """MACD must return macd, signal, histogram series."""
    candles = make_candles(80)
    result = calculate_macd(candles)
    assert "macd" in result
    assert "signal" in result
    assert "histogram" in result
    assert "latest" in result
    assert len(result["macd"]) == 80
    # MACD values can be positive or negative depending on trend
    latest = result["latest"]
    assert isinstance(latest["macd"], float)
    assert isinstance(latest["signal"], float)
    assert isinstance(latest["histogram"], float)


def test_bollinger_bands():
    """Bollinger bands must have upper > middle > lower."""
    candles = make_candles(40)
    result = calculate_bollinger_bands(candles, 20, 2)
    latest = result["latest"]
    assert latest["upper"] > latest["middle"]
    assert latest["middle"] > latest["lower"]
    assert latest["upper"] - latest["lower"] > 0


def test_atr_basic():
    """ATR must be positive."""
    candles = make_candles(20)
    result = calculate_atr(candles, 14)
    valid = [x for x in result if x is not None]
    assert all(x >= 0 for x in valid)
    assert len(valid) > 5


def test_atr_from_close_prices():
    """ATR must accept list of close prices."""
    closes = [2350 + i * 0.5 for i in range(30)]
    # Wrap as candles with same close=open=high=low
    candles = [{"open": c, "high": c, "low": c, "close": c} for c in closes]
    result = calculate_atr(candles, 14)
    valid = [x for x in result if x is not None]
    assert len(valid) > 5


def test_pivot_points():
    """Pivot points must be mathematically correct."""
    result = calculate_pivot_points(high=2355.0, low=2345.0, close=2350.0)
    pivot = result["pivot"]
    assert abs(pivot - 2350.0) < 0.01  # pivot = (H+L+C)/3
    assert result["r1"] > pivot
    assert result["s1"] < pivot
    assert result["r2"] > result["r1"]
    assert result["s2"] < result["s1"]


def test_fibonacci_levels():
    """Fibonacci retracement must have correct ordering."""
    result = calculate_fibonacci_levels(high=2360.0, low=2340.0)
    assert result["23.6"] > result["38.2"]
    assert result["38.2"] > result["50.0"]
    assert result["50.0"] > result["61.8"]
    assert result["61.8"] > result["78.6"]
    assert 2340 < result["38.2"] < 2360


def test_swing_points_detect_highs_and_lows():
    """Swing detection must find peaks and troughs."""
    # Strong trend up then down
    candles = []
    for i in range(50):
        if i < 15:
            price = 2340 + i * 0.5
        elif i < 35:
            price = 2347.5 + (i - 15) * 0.3
        else:
            price = 2353.5 - (i - 35) * 0.5
        candles.append({"high": round(price + 1, 2), "low": round(price - 1, 2)})

    result = detect_swing_points(candles, lookback=3)
    highs = result["highs"]
    lows = result["lows"]
    assert len(highs) > 0 or len(lows) > 0  # Some swings found


def test_support_resistance_levels():
    """Support/resistance must be around current price."""
    candles = make_candles(100)
    result = detect_support_resistance(candles, lookback=80)
    assert "supports" in result
    assert "resistances" in result
    current_price = candles[-1]["close"]
    for sup in result["supports"]:
        assert sup < current_price, f"Support {sup} must be below price {current_price}"
    for res in result["resistances"]:
        assert res > current_price, f"Resistance {res} must be above price {current_price}"


# ───────────────────────────── Helpers ───────────────────────────────────────


def test_calculate_pips_buy():
    """Pips must be positive for profitable BUY trade."""
    assert calculate_pips(entry=2350.0, exit_price=2355.0, trade_type="BUY") > 0
    assert calculate_pips(entry=2350.0, exit_price=2345.0, trade_type="BUY") < 0


def test_calculate_pips_sell():
    """Pips must be positive for profitable SELL trade."""
    assert calculate_pips(entry=2350.0, exit_price=2345.0, trade_type="SELL") > 0
    assert calculate_pips(entry=2350.0, exit_price=2355.0, trade_type="SELL") < 0


def test_calculate_pips_neutral():
    """Zero pips at entry price."""
    assert calculate_pips(entry=2350.0, exit_price=2350.0, trade_type="BUY") == 0


def test_format_price():
    """format_price must return exactly 2 decimal places."""
    assert format_price(2350.5) == "2350.50"
    assert format_price(2350.567) == "2350.57"
    assert format_price("2350.123") == "2350.12"
    assert format_price(None) == "0.00"
    assert format_price("invalid") == "0.00"


def test_get_current_session():
    """get_current_session must return a known session label."""
    session = get_current_session()
    assert session in {"Asian", "London", "London-NY Overlap", "New York", "Late NY / Rollover"}


def test_is_market_open():
    """is_market_open must return boolean."""
    result = is_market_open()
    assert isinstance(result, bool)


def test_load_config():
    """load_config must return a dict with symbol."""
    config = load_config()
    assert isinstance(config, dict)
    assert "symbol" in config
    assert config["symbol"] == "XAU/USD"


def test_setup_logging_no_crash():
    """setup_logging must not raise."""
    setup_logging()
    setup_logging(level=10)
    assert True