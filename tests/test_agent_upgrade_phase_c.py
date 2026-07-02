from datetime import datetime, timedelta, timezone

from agents.decision_agent import DecisionAgent
from agents.macro_fundamental_agent import MacroFundamentalAgent
from agents.news_risk_agent import NewsRiskAgent
from services.database import DatabaseService
from services.learning_service import LearningService


def test_macro_agent_builds_gold_direction_from_operator_context():
    context = {
        "source": "unit-test",
        "dxy_trend": "falling",
        "us10y_trend": "down",
        "fed_tone": "dovish",
        "risk_sentiment": "risk_off",
    }
    result = MacroFundamentalAgent({"macro_context": context}).analyze({})

    assert result["macro_direction"]["bias"] == "BULLISH_GOLD"
    assert result["signal"] == "BUY"
    assert result["confidence"] >= 55
    assert "MACRO_DXY_BULLISH_GOLD" in result["reason_codes"]
    assert result["data_quality"]["source"] == "unit-test"


def test_news_risk_separates_event_block_from_macro_direction(monkeypatch):
    now = datetime.now(timezone.utc)
    events = [{
        "event": "US CPI",
        "currency": "USD",
        "impact": "HIGH",
        "time": (now + timedelta(minutes=10)).isoformat(),
    }]
    monkeypatch.setattr(NewsRiskAgent, "_load_events", lambda self: events)
    result = NewsRiskAgent({"macro_context": {"dxy_trend": "rising", "us10y_trend": "up", "fed_tone": "hawkish"}}).check(now)

    assert result["event_risk"]["can_trade"] is False
    assert result["event_risk"]["status"] == "DANGER"
    assert result["macro_direction"]["bias"] == "BEARISH_GOLD"
    assert "NEWS_HARD_BLOCK" in result["reason_codes"]
    assert any(code.startswith("MACRO_") for code in result["reason_codes"])


def test_decision_entry_attribution_is_post_trade_ready():
    cfg = {"signal_requirements": {"min_agents_agree": 2, "agent_min_confidence": 60}, "risk_settings": {"min_confidence": 60}}
    decision = DecisionAgent(cfg).decide({
        "technical": {"agent": "technical", "signal": "BUY", "confidence": 82, "reason_codes": ["EMA_BULL_ALIGN"], "evidence": []},
        "classical": {"agent": "classical", "signal": "BUY", "direction": "BUY", "confidence": 76, "pattern_quality": {"grade": "A"}, "breakout_quality": {"state": "BULLISH_BREAKOUT"}},
        "multitimeframe": {"agent": "multitimeframe", "signal": "BUY", "direction": "BUY", "confidence": 80, "entry_permission": "ALLOWED", "timing_state": "VALID", "mtf_failure_mode": "NONE"},
        "smc": {"agent": "smc", "signal": "WAIT", "confidence": 0},
        "price_action": {"agent": "price_action", "signal": "WAIT", "confidence": 0},
        "risk": {"approved": True, "entry": {"price": 2300}, "stop_loss": {"price": 2280}, "take_profit": {"tp1": {"price": 2330, "rr_ratio": 1.5}, "tp2": {"price": 2360, "rr_ratio": 3}}},
        "news": {"can_trade": True, "market_status": "SAFE", "event_risk": {"status": "SAFE"}, "macro_direction": {"bias": "BULLISH_GOLD", "confidence": 76}},
        "daily_bias": {"bias": "BULLISH", "confidence": 70, "strength_band": "moderate_bullish"},
        "session": {"trading_allowed": True, "allow_signals": True},
        "current_price": 2300,
    })

    attr = decision["entry_attribution"]
    assert decision["decision"] == "BUY"
    assert attr["primary_entry_driver"] in {"technical", "classical", "multitimeframe"}
    assert attr["entry_permission"] == "ALLOWED"
    assert attr["macro_direction"]["bias"] == "BULLISH_GOLD"
    assert attr["failure_mode"] == "NONE"


def test_trade_enrichment_and_learning_use_attribution_metadata(tmp_path):
    cfg = {"database": {"local_fallback_file": str(tmp_path / "trades.json")}}
    db = DatabaseService(cfg)
    decision = {
        "decision": "SELL",
        "symbol": "XAU/USD",
        "current_price": 2400,
        "signal": {"type": "SELL", "entry": {"price": 2400}, "stop_loss": 2420, "tp2": 2340, "rr_ratio": 3},
        "entry_attribution": {"primary_entry_driver": "multitimeframe", "failure_mode": "NONE", "supporting_agents": ["multitimeframe", "technical"], "macro_direction": {"bias": "BEARISH_GOLD"}},
        "market_context": {"macro_direction": {"bias": "BEARISH_GOLD"}, "technical_regime": {"volatility_regime": "HIGH"}},
        "news_context": {"rule_based": {"market_status": "SAFE"}},
        "session_info": {"current_session": "London"},
    }
    enrichment = db._entry_enrichment(decision, decision["signal"], "XAU/USD", 2400, 2420)
    assert enrichment["primary_entry_driver"] == "multitimeframe"
    assert enrichment["entry_failure_mode"] == "NONE"
    assert enrichment["macro_bias_at_entry"] == "BEARISH_GOLD"

    trade = {
        "final_pnl": 300,
        "planned_risk_points": 200,
        "volatility_regime": "HIGH",
        "macro_bias_at_entry": "BEARISH_GOLD",
        "primary_entry_driver": "multitimeframe",
        "signal_snapshot": decision,
    }
    learning = LearningService(db, cfg)
    configs = learning._trade_agent_configs(trade)
    assert [c["name"] for c in configs][:2] == ["multitimeframe", "technical"]
    breakdown = learning._enrichment_breakdowns([trade])
    assert breakdown["macro_bias"]["BEARISH_GOLD"]["pnl"] == 300
    assert breakdown["entry_driver"]["multitimeframe"]["count"] == 1
