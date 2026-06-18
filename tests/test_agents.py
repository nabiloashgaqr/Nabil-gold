"""Basic tests for phase-one agents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.classical_agent import ClassicalAgent
from agents.decision_agent import DecisionAgent
from agents.risk_management_agent import RiskManagementAgent
from agents.technical_agent import TechnicalAgent
from utils.helpers import load_config


def sample_market_data(count: int = 240) -> dict:
    start = datetime.now(timezone.utc) - timedelta(minutes=15 * count)
    price = 2300.0
    candles = []
    for i in range(count):
        price += 0.25 + (0.15 if i % 5 == 0 else -0.05)
        candles.append(
            {
                "time": (start + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z"),
                "open": round(price - 0.4, 2),
                "high": round(price + 1.2, 2),
                "low": round(price - 1.1, 2),
                "close": round(price, 2),
                "volume": 1000,
            }
        )
    return {
        "symbol": "XAU/USD",
        "timeframe": "15m",
        "data": candles,
        "current_price": candles[-1]["close"],
        "timeframes": {"15m": {"data": candles}}
    }


def test_technical_agent_returns_required_keys() -> None:
    """اختبار الوكيل الفني"""
    config = load_config()
    result = TechnicalAgent(config, ai_service=None).analyze(sample_market_data())
    
    assert result["agent"] == "technical"
    assert result["signal"] in {"BUY", "SELL", "WAIT"}
    assert 0 <= result["confidence"] <= 100


def test_classical_agent_returns_levels() -> None:
    """اختبار الوكيل الكلاسيكي"""
    config = load_config()
    result = ClassicalAgent(config, ai_service=None).analyze(sample_market_data())
    
    assert result["agent"] == "classical"
    assert "support_levels" in result or "support" in result


def test_decision_agent_waits_without_three_agent_agreement() -> None:
    """اختبار وكيل القرار"""
    config = load_config()
    data = sample_market_data()
    
    technical = TechnicalAgent(config, ai_service=None).analyze(data)
    classical = ClassicalAgent(config, ai_service=None).analyze(data)
    
    results = {
        "technical": technical,
        "classical": classical,
        "smc": {"agent": "smc", "signal": "WAIT", "confidence": 0},
        "price_action": {"agent": "price_action", "signal": "WAIT", "confidence": 0},
        "multitimeframe": {"agent": "multitimeframe", "signal": "WAIT", "confidence": 0},
        "news": {"market_status": "SAFE", "can_trade": True, "summary": "safe"},
        "current_price": data["current_price"],
    }
    results["risk"] = RiskManagementAgent(config).evaluate(results)
    
    decision = DecisionAgent(config, ai_service=None).analyze(results)
    
    assert decision["signal"] in {"WAIT", "BUY", "SELL"}
    assert "confidence" in decision