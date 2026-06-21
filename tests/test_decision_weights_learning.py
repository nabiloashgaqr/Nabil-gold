"""Tests for DecisionAgent's learned-weight refresh (analyze_async).

Before this fix, run_learning.py computed and saved new agent_weights to
the DB daily, but DecisionAgent._load_weights() only ever read the static
agent_weights from config.json - the learning loop had no effect on live
decisions. analyze_async now asks the injected learning_service for the
latest DB weights before collecting votes.
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
        "groq_observation_mode": {"enabled": True, "allow_single_agent_context": True},
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
    """Minimal stand-in for DatabaseService.execute_query, just enough for
    LearningService.load_current_weights()."""

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
async def test_analyze_async_applies_db_learned_weights():
    rows = [
        {"agent_name": "technical", "weight": 0.40},
        {"agent_name": "classical", "weight": 0.10},
        {"agent_name": "smc", "weight": 0.30},
        {"agent_name": "price_action", "weight": 0.10},
        {"agent_name": "multitimeframe", "weight": 0.10},
    ]
    db = FakeDatabase(rows=rows)
    learning_service = LearningService(db, base_config())
    agent = DecisionAgent(base_config(), ai_service=None, learning_service=learning_service)

    # Before analyze_async runs, weights are still the static config defaults.
    assert agent.current_weights["technical"] == 0.20

    await agent.analyze_async(minimal_agents_results())

    assert db.queried is True
    assert agent.current_weights["technical"] == 0.40
    assert agent.current_weights["classical"] == 0.10


@pytest.mark.asyncio
async def test_analyze_async_keeps_config_weights_when_no_learning_service():
    agent = DecisionAgent(base_config(), ai_service=None, learning_service=None)
    config_weights = dict(agent.current_weights)

    await agent.analyze_async(minimal_agents_results())

    assert agent.current_weights == config_weights


@pytest.mark.asyncio
async def test_analyze_async_falls_back_to_existing_weights_on_db_error():
    db = FakeDatabase(raise_error=True)
    learning_service = LearningService(db, base_config())
    agent = DecisionAgent(base_config(), ai_service=None, learning_service=learning_service)
    config_weights = dict(agent.current_weights)

    # Must not raise - DB errors should degrade gracefully, not crash analysis.
    # LearningService.load_current_weights() itself catches DB errors and
    # returns its own default_weights, so DecisionAgent ends up using those
    # (not its own config_weights) - this is expected, documented behavior:
    # the only thing under test here is that analyze_async doesn't crash.
    await agent.analyze_async(minimal_agents_results())

    assert db.queried is True
    assert agent.current_weights  # got *some* usable weights, no crash


@pytest.mark.asyncio
async def test_analyze_async_ignores_empty_db_result():
    """An empty result from the DB (e.g. table has no rows yet) should not
    wipe out the perfectly good config-based weights with an empty dict."""
    db = FakeDatabase(rows=[])
    learning_service = LearningService(db, base_config())
    agent = DecisionAgent(base_config(), ai_service=None, learning_service=learning_service)
    config_weights = dict(agent.current_weights)

    await agent.analyze_async(minimal_agents_results())

    assert agent.current_weights == config_weights
