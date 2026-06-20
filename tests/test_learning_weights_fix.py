"""Regression tests for the closed learning loop fix.

Bug: `_load_weights()` in DecisionAgent only read from config.json,
so the weights saved daily by run_learning.py were never used.
Fix: DecisionAgent now prefers learning_service.current_weights
(loaded from DB) over config.json.

Tests:
1. _load_weights returns learning_service.current_weights when present
2. _load_weights falls back to config.json when no learning_service
3. _load_weights falls back to default_weights when nothing else
4. update_weights() refreshes and changes current_weights
5. run_analysis.py calls load_current_weights() before DecisionAgent
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
    """The bug was: weights from DB were calculated but never used."""

    def test_uses_learning_service_weights_when_available(self):
        """The fix: prefer learning_service.current_weights over config.json."""
        config = _config()
        # Simulate learning_service after run_learning.py saved new weights
        learning_service = MagicMock()
        learning_service.current_weights = {
            "technical": 0.10,  # reduced
            "classical": 0.10,  # reduced
            "smc": 0.40,        # increased (top performer)
            "price_action": 0.20,
            "multitimeframe": 0.20,
        }

        agent = DecisionAgent(config, learning_service=learning_service)
        # The fix: current_weights must reflect learning_service, NOT config.json
        assert agent.current_weights["smc"] == 0.40  # from learning_service
        assert agent.current_weights["technical"] == 0.10  # from learning_service

    def test_falls_back_to_config_when_no_learning_service(self):
        config = _config()
        agent = DecisionAgent(config, learning_service=None)
        assert agent.current_weights == config["agent_weights"]

    def test_falls_back_to_config_when_learning_service_has_empty_weights(self):
        config = _config()
        learning_service = MagicMock()
        learning_service.current_weights = {}  # empty after failed DB load
        agent = DecisionAgent(config, learning_service=learning_service)
        # Falls back to config since current_weights is empty
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
        learning_service = MagicMock()
        learning_service.current_weights = {"technical": 0.5, "smc": 0.5}
        agent = DecisionAgent(config, learning_service=learning_service)
        agent.current_weights["technical"] = 0.99
        # learning_service's copy should NOT be affected
        assert learning_service.current_weights["technical"] == 0.5

    def test_does_not_use_config_when_learning_service_provided_even_with_different_keys(self):
        """If learning_service has weights, config is ignored entirely."""
        config = _config()
        # config has 'classical' but learning_service doesn't
        learning_service = MagicMock()
        learning_service.current_weights = {
            "technical": 0.5,
            "smc": 0.5,
        }
        agent = DecisionAgent(config, learning_service=learning_service)
        # Should use exactly what learning_service has (no merging with config)
        assert "classical" not in agent.current_weights


class TestLoadWeightsAsyncIntegration:
    """Verify the integration with run_analysis.py."""

    def test_learning_service_load_current_weights_signature(self):
        """load_current_weights is async — run_analysis must await it."""
        from services.learning_service import LearningService
        import inspect
        assert inspect.iscoroutinefunction(LearningService.load_current_weights), \
            "load_current_weights must remain async"

    def test_run_analysis_imports_load_helpers(self):
        """run_analysis must import get_learning_service and call load_current_weights."""
        with open("scripts/run_analysis.py", encoding="utf-8") as f:
            src = f.read()
        assert "get_learning_service" in src
        assert "load_current_weights" in src, \
            "run_analysis.py must call await learning_service.load_current_weights()"
        # The fix adds this line between get_learning_service and DecisionAgent
        idx_get = src.index("get_learning_service(database, config)")
        idx_load = src.index("load_current_weights")
        assert idx_get < idx_load, "load must happen after get_learning_service"


class TestConfigUpdate:
    """Verify the config change for max_reviews_per_run."""

    def test_config_has_updated_review_limits(self):
        import json
        with open("config.json", encoding="utf-8") as f:
            cfg = json.load(f)
        review_cfg = cfg.get("ai_trade_review", {})
        # Was 3, raised to 20 to handle more daily signals
        assert review_cfg.get("max_reviews_per_run") >= 15, \
            f"max_reviews_per_run should be raised, got {review_cfg.get('max_reviews_per_run')}"
        # recent_trades_limit raised to 50 to cover more history
        assert review_cfg.get("recent_trades_limit") >= 40, \
            f"recent_trades_limit should be raised, got {review_cfg.get('recent_trades_limit')}"
