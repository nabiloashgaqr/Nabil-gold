"""
Regression tests for DecisionAgent._final_decision with AI available.

Before the fix, _final_decision referenced three undefined variables
(`external_model_observation_enabled`, `observation_min_conf`, `external_model_obs`) that
were only defined inside _ai_decision. The production path
(analyze_async / decide_async) crashed with NameError as soon as external model
returned an available response.

These tests exercise _final_decision directly with ai['available']=True
to make sure those names are defined locally inside the method.
"""

import pytest

from agents.decision_agent import DecisionAgent


def _base_config():
    return {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {
            "min_agents_agree": 1,
            "min_agreement_percentage": 1,
            "allow_all_signals": True,
        },
        "agent_weights": {
            "technical": 0.20,
            "classical": 0.20,
            "smc": 0.25,
            "price_action": 0.15,
            "multitimeframe": 0.20,
        },
        "external_model_observation_mode": {
            "enabled": True,
            "min_external_model_confidence": 60,
            "allow_single_agent_context": True,
        },
        "external_service": {"enabled": True, "provider": "external_model", "fallback_to_classic": False},
    }


def _session():
    return {
        "allow_signals": True,
        "trading_allowed": True,
        "quality": "HIGH",
        "current_session": "London-NY Trading",
    }


def _classic_buy():
    return {
        "decision": "BUY",
        "confidence": 80,
        "buy_count": 3,
        "sell_count": 0,
        "buy_agreement_pct": 100.0,
        "sell_agreement_pct": 0,
        "total_voting_agents": 3,
        "rejection_reason": None,
    }


class TestFinalDecisionAiAvailable:
    """Direct _final_decision calls with ai['available']=True.

    Before the fix, all of these raised:
        NameError: name 'external_model_observation_enabled' is not defined
    """

    def test_external_model_buy_above_min_confidence_returns_buy(self):
        agent = DecisionAgent(_base_config())
        ai = {
            "available": True,
            "signal": "BUY",
            "confidence": 80,
            "reasoning": "Strong bullish context",
            "supportive_evidence": ["a", "b", "c"],
            "ai_warnings": [],
        }
        signal, confidence, _ = agent._final_decision(_classic_buy(), ai, _session())
        assert signal == "BUY"
        assert confidence >= 60

    def test_external_model_sell_above_min_confidence_returns_sell(self):
        agent = DecisionAgent(_base_config())
        ai = {
            "available": True,
            "signal": "SELL",
            "confidence": 75,
            "reasoning": "Strong bearish context",
            "supportive_evidence": ["a", "b"],
            "ai_warnings": [],
        }
        classic = dict(_classic_buy())
        classic["decision"] = "SELL"
        signal, confidence, _ = agent._final_decision(classic, ai, _session())
        assert signal == "SELL"
        assert confidence >= 60

    def test_ai_wait_is_ignored_in_classic_consensus_mode(self):
        agent = DecisionAgent(_base_config())
        ai = {
            "available": True,
            "signal": "WAIT",
            "confidence": 50,
            "reasoning": "Mixed signals",
            "supportive_evidence": [],
            "ai_warnings": [],
        }
        signal, _, reason = agent._final_decision(_classic_buy(), ai, _session())
        assert signal == "BUY"
        assert "Classic" in reason

    def test_ai_unavailable_is_ignored_in_classic_consensus_mode(self):
        cfg = _base_config()
        cfg["external_service"]["fallback_to_classic"] = False
        agent = DecisionAgent(cfg)
        ai = {"available": False, "error": "no API key"}
        signal, _, reason = agent._final_decision(_classic_buy(), ai, _session())
        assert signal == "BUY"
        assert "Classic" in reason


class TestAnalyzeAsyncRegression:
    """analyze_async used to crash with NameError when external_service returns
    an available response. This test exercises the production path with a
    fake AI that mimics a successful external model call.
    """

    @pytest.mark.asyncio
    async def test_analyze_async_with_available_ai_does_not_raise_name_error(self):
        class FakeAI:
            async def _call_ai(self, prompt, agent_type):
                class R:
                    success = True
                    content = (
                        '{"final_signal":"BUY","confidence":80,'
                        '"reasoning":"ok","supportive_evidence":["a","b","c"],'
                        '"opposing_evidence":[],"invalidation":"3000",'
                        '"alternative_scenario":"2900"}'
                    )
                    provider = "external_model"
                    model = "test"
                    tokens_used = 0
                    cost = 0.0
                    error = None

                return R()

            async def analyze_chart(self, **kwargs):
                class R:
                    success = False
                    content = ""
                    provider = "external_model"
                    model = "test"
                    tokens_used = 0
                    cost = 0.0
                    error = "fallback"

                return R()

            def parse_json_response(self, txt):
                import json

                if not txt:
                    return None
                try:
                    return json.loads(txt)
                except Exception:
                    return None

        agent = DecisionAgent(_base_config(), )
        results = {
            "all_agents_results": {
                "technical": {"signal": "BUY", "confidence": 80},
                "classical": {"signal": "BUY", "confidence": 80},
                "smc": {"signal": "BUY", "confidence": 80},
            },
            "session": _session(),
            "indicators": {},
            "price_data": {"symbol": "XAUUSD", "timeframe": "15m"},
        }
        # This used to raise NameError.
        result = await agent.analyze_async(results)
        assert "signal" in result
        assert "confidence" in result
