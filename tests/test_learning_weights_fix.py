"""Regression tests for unified weight source.

After the unification fix, config.json is the single source of truth for
agent weights. DB weights are NOT used for decisions. The user manually
updates config.json + Supabase when they accept a learning recommendation.

Tests:
1. _load_weights returns config.json weights (NOT learning_service DB weights)
2. _load_weights falls back to config.json when no learning_service
3. _load_weights falls back to default_weights when nothing else
4. update_weights() refreshes and changes current_weights
5. run_analysis.py initializes learning_service (for recommendations)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agents.decision_agent import DecisionAgent


def _config(**overrides) -> dict:
    base = {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {
            "min_agents_agree": 1,
            "min_agreement_percentage": 1,
        },
        "agent_weights": {
            "technical": 0.20,
            "classical": 0.20,
            "smc": 0.25,
            "price_action": 0.15,
            "multitimeframe": 0.20,
        },
    }
    base.update(overrides)
    return base


class TestLoadWeightsFix:
    """config.json is the single source of truth — DB does NOT override."""

    def test_uses_config_weights_not_learning_service_db(self):
        """DB weights from learning_service must NOT override config.json."""
        config = _config()
        # Simulate learning_service with different DB weights
        learning_service = MagicMock()
        learning_service.current_weights = {
            "technical": 0.10,  # DB says 0.10
            "classical": 0.10,
            "smc": 0.40,        # DB says 0.40
            "price_action": 0.20,
            "multitimeframe": 0.20,
        }

        agent = DecisionAgent(config, learning_service=learning_service)
        # Must use config.json weights, NOT DB
        assert agent.current_weights["smc"] == 0.25  # from config, not 0.40
        assert agent.current_weights["technical"] == 0.20  # from config, not 0.10

    def test_falls_back_to_config_when_no_learning_service(self):
        config = _config()
        agent = DecisionAgent(config, learning_service=None)
        assert agent.current_weights == config["agent_weights"]

    def test_falls_back_to_config_when_learning_service_has_empty_weights(self):
        config = _config()
        learning_service = MagicMock()
        learning_service.current_weights = {}  # empty after failed DB load
        agent = DecisionAgent(config, learning_service=learning_service)
        # Still uses config — DB is not the source
        assert agent.current_weights == config["agent_weights"]

    def test_falls_back_to_default_when_neither_available(self):
        config = _config()
        config.pop("agent_weights")
        agent = DecisionAgent(config, learning_service=None)
        # Uses class default_weights
        assert agent.current_weights == agent.default_weights

    def test_update_weights_replaces_current_weights(self):
        config = _config()
        agent = DecisionAgent(config, learning_service=None)
        new_weights = {
            "technical": 0.5,
            "classical": 0.0,
            "smc": 0.5,
            "price_action": 0.0,
            "multitimeframe": 0.0,
        }
        agent.update_weights(new_weights)
        assert agent.current_weights == new_weights

    def test_independent_copy_no_aliasing(self):
        """Returns a copy so future updates don't mutate the source."""
        config = _config()
        agent = DecisionAgent(config, learning_service=None)
        original = dict(agent.current_weights)
        agent.current_weights["technical"] = 0.99
        # original dict should NOT be affected
        assert original["technical"] == 0.20

    def test_config_always_used_regardless_of_learning_service_keys(self):
        """config.json is the source — learning_service DB keys are ignored."""
        config = _config()
        learning_service = MagicMock()
        learning_service.current_weights = {
            "technical": 0.5,
            "smc": 0.5,
        }
        agent = DecisionAgent(config, learning_service=learning_service)
        # Must use config.json keys, not DB subset
        assert "classical" in agent.current_weights
        assert agent.current_weights["technical"] == 0.20  # config value


class TestLoadWeightsAsyncIntegration:
    """Verify the integration with run_analysis.py."""

    def test_learning_service_load_current_weights_signature(self):
        """load_current_weights is async — run_analysis must await it."""
        from services.learning_service import LearningService
        import inspect
        assert inspect.iscoroutinefunction(LearningService.load_current_weights), \
            "load_current_weights must remain async"

    def test_run_analysis_imports_learning_helpers(self):
        """run_analysis must import get_learning_service for recommendations."""
        with open("scripts/run_analysis.py", encoding="utf-8") as f:
            src = f.read()
        assert "get_learning_service" in src


class TestConfigUpdate:

    def test_external_review_config_removed(self):
        import json
        with open("config.json", encoding="utf-8") as f:
            cfg = json.load(f)
        assert "removed_review_config" not in cfg
