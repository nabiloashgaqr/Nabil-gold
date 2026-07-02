from datetime import datetime, timedelta, timezone

from agents.classical_agent import ClassicalAgent
from agents.daily_bias_agent import DailyBiasAgent
from agents.multitimeframe_agent import MultiTimeframeAgent
from agents.technical_agent import TechnicalAgent


def _candles(count=240, drift=0.35):
    start = datetime.now(timezone.utc) - timedelta(minutes=15 * count)
    price = 2300.0
    rows = []
    for i in range(count):
        price += drift if i % 3 else drift * 0.2
        rows.append({
            "time": (start + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z"),
            "open": round(price - 0.5, 2),
            "high": round(price + 1.2, 2),
            "low": round(price - 1.1, 2),
            "close": round(price, 2),
            "volume": 1000,
        })
    return rows


def _market_data():
    candles = _candles()
    return {
        "symbol": "XAU/USD",
        "timeframe": "15m",
        "data": candles,
        "current_price": candles[-1]["close"],
        "timeframes": {"15m": {"data": candles}, "1H": {"data": candles}, "4H": {"data": candles}},
    }


def test_technical_regime_aware_scoring_is_exposed():
    result = TechnicalAgent({}).analyze(_market_data())
    tech = result["technical"]
    assert "regime_scoring" in tech
    assert tech["regime_scoring"]["market_phase"] in {"TRENDING", "RANGING", "SQUEEZE"}
    assert "base_score" in tech
    assert "adjusted_score" in tech["regime_scoring"]
    assert isinstance(result["confidence_breakdown"], dict)


def test_classical_pattern_quality_breakout_and_retest_are_exposed():
    result = ClassicalAgent({}).analyze(_market_data())
    assert "pattern_quality" in result
    assert "breakout_quality" in result
    assert "retest_state" in result
    assert result["pattern_quality"]["grade"] in {"A", "B", "C", "D", "NONE"}
    assert isinstance(result["confidence_breakdown"], dict)


def test_multitimeframe_entry_permission_timing_and_failure_mode():
    result = MultiTimeframeAgent({}).analyze(_market_data())
    assert result["entry_permission"] in {"ALLOWED", "ALLOWED_WITH_CAUTION", "NOT_RECOMMENDED", "BLOCKED"}
    assert result["timing_state"] in {"EARLY", "VALID", "LATE", "EXHAUSTED", "NO_TRADE"}
    assert isinstance(result["mtf_failure_mode"], str)
    assert "timing" in result["confidence_breakdown"]


def test_daily_bias_persistence_strength_and_reason_codes():
    result = DailyBiasAgent({"daily_bias_filter": {"enabled": True}}).analyze(_market_data())
    assert result["bias"] in {"BULLISH", "BEARISH", "NEUTRAL"}
    assert "strength_band" in result
    assert "bias_persistence" in result
    assert isinstance(result["reason_codes"], list)
    assert isinstance(result["evidence"], list)
    assert isinstance(result["confidence_breakdown"], dict)
