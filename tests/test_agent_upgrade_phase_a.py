from datetime import datetime, timedelta, timezone

from agents.classical_agent import ClassicalAgent
from agents.decision_agent import DecisionAgent
from agents.multitimeframe_agent import MultiTimeframeAgent
from agents.news_risk_agent import NewsRiskAgent
from agents.technical_agent import TechnicalAgent
from services.market_snapshot import build_market_snapshot


def _candles(count=240):
    start = datetime.now(timezone.utc) - timedelta(minutes=15 * count)
    price = 2300.0
    rows = []
    for i in range(count):
        price += 0.35 if i % 3 else -0.05
        rows.append({
            "time": (start + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z"),
            "open": round(price - 0.4, 2),
            "high": round(price + 1.1, 2),
            "low": round(price - 1.0, 2),
            "close": round(price, 2),
            "volume": 1000,
        })
    return rows


def _market_data():
    candles = _candles()
    return {"symbol": "XAU/USD", "timeframe": "15m", "data": candles, "current_price": candles[-1]["close"], "timeframes": {"15m": {"data": candles}, "1H": {"data": candles}, "4H": {"data": candles}}}


def test_verified_snapshot_builds_source_of_truth():
    snap = build_market_snapshot(_market_data(), {"symbol": "XAU/USD"})
    assert snap["symbol"] == "XAU/USD"
    assert snap["current_price"] > 0
    assert "ema_50" in snap["indicators"]
    assert "nearest_support" in snap["key_levels"]
    assert snap["data_quality"]["candles"] == 240
    assert snap["data_quality"]["freshness"] in {"OK", "STALE", "UNKNOWN"}


def _assert_structured(result):
    assert isinstance(result.get("reason_codes"), list)
    assert isinstance(result.get("evidence"), list)
    assert isinstance(result.get("invalidations"), list)
    assert isinstance(result.get("confidence_breakdown"), dict)
    assert isinstance(result.get("data_quality"), dict)


def test_core_agents_emit_structured_evidence_without_breaking_schema(monkeypatch):
    data = _market_data()
    technical = TechnicalAgent({}).analyze(data)
    classical = ClassicalAgent({}).analyze(data)
    mtf = MultiTimeframeAgent({}).analyze(data)

    for result in (technical, classical, mtf):
        _assert_structured(result)

    assert technical["signal"] in {"BUY", "SELL", "WAIT"}
    assert classical["direction"] in {"BUY", "SELL", "NEUTRAL"}
    assert mtf["direction"] in {"BUY", "SELL", "NEUTRAL"}

    # Prevent agents from reading saved macro_context.json
    # (which may contain real data and produce a non-neutral bias)
    monkeypatch.setattr(NewsRiskAgent, "_load_events", lambda self: [])
    monkeypatch.setenv("MACRO_CONTEXT_JSON", "{}")
    news = NewsRiskAgent({}).check(datetime.now(timezone.utc))
    _assert_structured(news)
    assert news["event_risk"]["status"] == news["market_status"]
    assert news["macro_direction"]["bias"] == "NEUTRAL"


def test_decision_preserves_agent_structured_payload():
    data = _market_data()
    technical = TechnicalAgent({}).analyze(data)
    technical["signal"] = "BUY"
    technical["confidence"] = 80
    classical = ClassicalAgent({}).analyze(data)
    classical["direction"] = "BUY"
    classical["signal"] = "BUY"
    classical["confidence"] = 78
    mtf = MultiTimeframeAgent({}).analyze(data)
    mtf["direction"] = "WAIT"
    mtf["signal"] = "WAIT"
    mtf["confidence"] = 0

    decision = DecisionAgent({"signal_requirements": {"min_agents_agree": 2, "agent_min_confidence": 60}, "risk_settings": {"min_confidence": 60}}).analyze({
        "technical": technical,
        "classical": classical,
        "multitimeframe": mtf,
        "smc": {"agent": "smc", "signal": "WAIT", "confidence": 0},
        "price_action": {"agent": "price_action", "signal": "WAIT", "confidence": 0},
        "session": {"trading_allowed": True, "allow_signals": True},
    })

    assert "agent_structured" in decision
    assert "technical" in decision["agent_structured"]
    assert decision["agent_structured"]["technical"]["reason_codes"]
    assert isinstance(decision["reason_codes"], list)
