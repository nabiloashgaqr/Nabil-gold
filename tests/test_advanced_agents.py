"""Tests for phase-two advanced agents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.multitimeframe_agent import MultiTimeframeAgent
from agents.price_action_agent import PriceActionAgent
from agents.smc_agent import SMCAgent


def sample_candles(count: int = 240) -> list[dict]:
    start = datetime.now(timezone.utc) - timedelta(minutes=15 * count)
    price = 2320.0
    candles = []
    for i in range(count):
        drift = 0.18 if i < count // 2 else 0.28
        pullback = -0.55 if i % 17 == 0 else 0.0
        open_price = price
        close = price + drift + pullback
        high = max(open_price, close) + 1.1
        low = min(open_price, close) - 1.0
        candles.append(
            {
                "time": (start + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z"),
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": 1000 + i,
            }
        )
        price = close
    return candles


def market_data() -> dict:
    candles_15m = sample_candles()
    candles_5m = sample_candles()
    candles_1h = sample_candles()
    candles_4h = sample_candles()
    return {
        "symbol": "XAU/USD",
        "timeframe": "15m",
        "data": candles_15m,
        "current_price": candles_15m[-1]["close"],
        "timeframes": {
            "5m": {"data": candles_5m},
            "15m": {"data": candles_15m},
            "1H": {"data": candles_1h},
            "4H": {"data": candles_4h},
        },
    }


def test_smc_agent_full_schema() -> None:
    result = SMCAgent().analyze(market_data())
    assert result["agent"] == "smc"
    assert result["direction"] in {"BUY", "SELL", "NEUTRAL"}
    assert "market_structure" in result
    assert "order_blocks" in result
    assert "liquidity" in result
    assert "fvg" in result
    assert "entry_suggestion" in result


def test_price_action_agent_full_schema() -> None:
    result = PriceActionAgent().analyze(market_data())
    assert result["agent"] == "price_action"
    assert result["role"] in {"CONFIRM", "WAIT", "REJECT"}
    assert "candle_patterns" in result
    assert "breakout_analysis" in result
    assert "rejection" in result


def test_multitimeframe_agent_full_schema() -> None:
    result = MultiTimeframeAgent().analyze(market_data())
    assert result["agent"] == "multitimeframe"
    assert result["alignment"] in {"FULL", "PARTIAL", "CONFLICT", "WEAK"}
    assert "timeframe_analysis" in result
    assert "weighted_bias" in result
