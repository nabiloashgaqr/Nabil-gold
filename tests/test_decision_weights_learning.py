"""Tests for DecisionAgent's weight loading — config.json is the single source of truth.

After the unification fix, DecisionAgent._load_weights() reads ONLY from
config.json (or its hardcoded defaults).  DB weights via learning_service
are no longer used for decisions — only for learning recommendations and
dashboard display.  The user manually updates config.json + Supabase
when they accept a recommendation.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.decision_agent import DecisionAgent
from services.learning_service import LearningService


def base_config():
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
        "external_model_observation_mode": {"enabled": True, "allow_single_agent_context": True},
    }


def minimal_agents_results():
    return {
        "technical": {"agent": "technical", "signal": "WAIT", "confidence": 0},
        "classical": {"agent": "classical", "signal": "WAIT", "confidence": 0},
        "smc": {"agent": "smc", "signal": "WAIT", "confidence": 0},
        "price_action": {"agent": "price_action", "signal": "WAIT", "confidence": 0},
        "multitimeframe": {"agent": "multitimeframe", "signal": "WAIT", "confidence": 0},
        "news": {"market_status": "SAFE", "can_trade": True, "summary": "safe"},
        "current_price": 2350.0,
    }


class FakeDatabase:
    """Minimal stand-in for DatabaseService.execute_query."""

    def __init__(self, rows=None, raise_error=False):
        self._rows = rows
        self._raise_error = raise_error
        self.queried = False

    async def execute_query(self, query, params=None):
        self.queried = True
        if self._raise_error:
            raise RuntimeError("DB unavailable")
        if "from agent_weights" in query.lower():
            return self._rows or []
        return []


@pytest.mark.asyncio
async def test_analyze_async_uses_config_weights_not_db():
    """config.json is the single source of truth — DB weights must NOT override."""
    rows = [
        {"agent_name": "technical", "weight": 0.40},
        {"agent_name": "classical", "weight": 0.10},
        {"agent_name": "smc", "weight": 0.30},
        {"agent_name": "price_action", "weight": 0.10},
        {"agent_name": "multitimeframe", "weight": 0.10},
    ]
    db = FakeDatabase(rows=rows)
    learning_service = LearningService(db, base_config())
    agent = DecisionAgent(base_config(), learning_service=learning_service)

    # Weights must come from config.json, NOT from DB
    assert agent.current_weights["technical"] == 0.20
    assert agent.current_weights["classical"] == 0.20

    await agent.analyze_async(minimal_agents_results())

    # After analyze_async, weights still from config.json — DB does not override
    assert agent.current_weights["technical"] == 0.20
    assert agent.current_weights["classical"] == 0.20


@pytest.mark.asyncio
async def test_analyze_async_keeps_config_weights_when_no_learning_service():
    agent = DecisionAgent(base_config(), learning_service=None)
    config_weights = dict(agent.current_weights)

    await agent.analyze_async(minimal_agents_results())

    assert agent.current_weights == config_weights


@pytest.mark.asyncio
async def test_analyze_async_stable_on_db_error():
    """DB errors must not crash analysis — config weights are always available."""
    db = FakeDatabase(raise_error=True)
    learning_service = LearningService(db, base_config())
    agent = DecisionAgent(base_config(), learning_service=learning_service)
    config_weights = dict(agent.current_weights)

    # Must not raise — config weights are the source, DB is irrelevant
    await agent.analyze_async(minimal_agents_results())

    # Weights unchanged from config
    assert agent.current_weights == config_weights


@pytest.mark.asyncio
async def test_analyze_async_ignores_empty_db_result():
    """Empty DB result is irrelevant — config weights are always used."""
    db = FakeDatabase(rows=[])
    learning_service = LearningService(db, base_config())
    agent = DecisionAgent(base_config(), learning_service=learning_service)
    config_weights = dict(agent.current_weights)

    await agent.analyze_async(minimal_agents_results())

    assert agent.current_weights == config_weights
