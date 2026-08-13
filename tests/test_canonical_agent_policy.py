"""Operator-approved unified five-agent policy (2026-08-12)."""
from __future__ import annotations

import json
from pathlib import Path

from agents.decision_agent import DecisionAgent
from services.session_planner import SessionPlannerService

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
CANONICAL = {
    "unified_trend": 0.20,
    "classical": 0.25,
    "smc": 0.20,
    "price_action": 0.20,
    "auction_flow": 0.15,
}


def test_shipped_config_has_one_weight_source_and_one_agent_bar() -> None:
    assert CONFIG["agent_weights"] == CANONICAL
    assert CONFIG["strategy_profile_weight_overrides_enabled"] is False
    assert CONFIG["unify_agent_min_confidence"] is True
    assert CONFIG["signal_requirements"]["agent_min_confidence"] == 67
    assert CONFIG["session_planner"]["agent_alignment_min_confidence"] == 67
    assert CONFIG["session_plan_delivery"]["only_when_ready"] is True
    assert CONFIG["all_agents_timeframes"]["required"] == ["5m", "15m", "1H", "4H"]
    assert CONFIG["all_agents_timeframes"]["require_native"] is True
    assert CONFIG["data_source"]["resample_timeframes_from_base"] is False
    for profile in CONFIG["strategy_profiles"].values():
        assert profile["agent_min_confidence"] == 67
        assert "weight_overrides" not in profile


def test_trend_profile_uses_canonical_weights_not_hidden_overrides() -> None:
    agent = DecisionAgent(CONFIG)
    results = {
        "unified_trend": {"signal": "BUY", "confidence": 92,
                          "setup_type": "TREND_CONTINUATION"},
        "classical": {"signal": "WAIT", "confidence": 30},
        "smc": {"signal": "WAIT", "confidence": 37,
                "setup_structure": {"setup_type": "ORDER_BLOCK_PULLBACK"}},
        "price_action": {"signal": "SELL", "confidence": 70},
        "auction_flow": {"signal": "BUY", "confidence": 92},
    }
    result = agent.analyze(results)
    assert result["strategy_profile"]["name"] == "trend_pullback"
    assert result["strategy_profile"]["weight_policy"] == "canonical_config_only"
    assert result["weights"] == CANONICAL
    buy_votes = {v["agent"]: v for v in result["votes"]["BUY"]}
    assert buy_votes["unified_trend"]["weight"] == 0.20
    assert buy_votes["auction_flow"]["weight"] == 0.15


def test_planner_reads_the_same_sixty_seven_bar() -> None:
    planner = SessionPlannerService(CONFIG)
    assert planner.agent_alignment_min_confidence == 67


def test_every_map_snapshot_persists_agent_observability() -> None:
    source = (ROOT / "scripts" / "run_analysis.py").read_text(encoding="utf-8")
    assert '"agent_opinions": _session_plan_agent_opinions' in source
    assert '"decision_context": {' in source
    assert '"weights": deepcopy(decision.get("weights") or {})' in source
