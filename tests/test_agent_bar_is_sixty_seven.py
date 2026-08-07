"""The configured agent bar must be the bar that is actually applied.

THE DECISION (operator, 2026-08-04)
-----------------------------------
``signal_requirements.agent_min_confidence`` 70 -> 67.

The evidence: at 12:38 the planner published a BUY day map graded A 80.2%
with a mapped entry of 4066.39 and TP1 at 4084.01. No order was created,
because only two agents cleared the 70% bar -- Technical 92 and
Multi-Timeframe 92 -- while Price Action read BUY at **68%**, two points
short, and was skipped. Gold then traded to 4088.85, so TP1 was reached while
the system stood aside.

67 keeps the genuinely undecided agents out of the count (Classical 25%,
SMC 37% on that same card) and admits only agreement that is real.

THE SECOND DOOR
---------------
Changing config alone would not have worked. ``DecisionAgent._strategy_profile``
merged the profile with ``setdefault``, and every profile in
services/strategy_profiles.py carries a hard-coded ``agent_min_confidence``
(70 for classic_consensus, 68 for the reversal families). ``setdefault`` only
fills a MISSING key, so the compiled constant always won and the configured
value was never applied on that path.

Measured before the fix: config set to 67, ``select_strategy_profile``
returned 70.

A per-profile override in config.json is still authoritative -- an operator
tuning one profile means it. What loses is the constant in the source.

WHAT IS NOT CHANGED
-------------------
``min_agents_agree`` stays 3. Three agents must still agree; the change is
only which reads qualify to be counted.

FAULT INJECTION
---------------
Set the config value back to 70 and
``test_the_12_38_card_would_now_place_an_order`` fails with support_count 2.
Restore the plain ``setdefault`` and ``test_the_profile_honours_the_config``
fails with 70 against a configured 67.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.decision_agent import DecisionAgent  # noqa: E402
from utils.helpers import load_config  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "run_analysis_agent_bar", os.path.join(ROOT, "scripts", "run_analysis.py")
)
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)

CONFIG = load_config()

# The 12:38 card, exactly as delivered.
CARD_AGENTS = {
    "technical": {"direction": "BUY", "confidence": 92},
    "classical": {"direction": "WAIT", "confidence": 25},
    "smc": {"direction": "WAIT", "confidence": 37},
    "price_action": {"direction": "BUY", "confidence": 68},
    "multitimeframe": {"direction": "BUY", "confidence": 92},
}


def _decision(agents=None):
    return {
        "decision": "WAIT",
        "agent_details": copy.deepcopy(agents or CARD_AGENTS),
        "session_plan": {
            "plan_ready": True, "session_bias": "BUY",
            "poi_classification": "HIGH_PROBABILITY_POI",
            "scenario_type": "FAILED_RECLAIM_CONTINUATION",
            "structure_trend": "BULLISH",
            "market_objective_direction": "BUY",
            "objective_alignment": "ALIGNED_WITH_MARKET_OBJECTIVE",
            "recent_sweep": {"type": "buy_side"},
        },
    }


# ── the decision ────────────────────────────────────────────────────────────

def test_the_configured_bar_is_sixty_seven():
    req = CONFIG["signal_requirements"]
    assert int(req["agent_min_confidence"]) == 67
    assert int(req["two_agent_entry"]["agent_min_confidence"]) == 67


def test_min_agents_agree_was_not_touched():
    """Three agents must still agree. Only who qualifies changed."""
    assert int(CONFIG["signal_requirements"]["min_agents_agree"]) == 3


def test_the_12_38_card_would_now_place_an_order():
    gate = ra._planner_execution_gate(_decision(), CONFIG)
    assert gate["allow"] is True, gate.get("reason")
    assert gate["support_count"] == 3
    assert set(gate["support_agents"]) == {"technical", "price_action", "multitimeframe"}


def test_the_undecided_agents_are_still_excluded():
    """25% and 37% are not agreement; they must stay out of the count."""
    gate = ra._planner_execution_gate(_decision(), CONFIG)
    assert "classical" not in gate["support_agents"]
    assert "smc" not in gate["support_agents"]


@pytest.mark.parametrize("confidence, counted", [
    (66.0, False), (66.9, False), (67.0, True), (68.0, True), (92.0, True),
])
def test_the_boundary_is_exactly_sixty_seven(confidence, counted):
    agents = copy.deepcopy(CARD_AGENTS)
    agents["price_action"]["confidence"] = confidence
    gate = ra._planner_execution_gate(_decision(agents), CONFIG)
    assert ("price_action" in gate.get("support_agents", [])) is counted


def test_a_disagreeing_agent_is_never_counted_however_confident():
    agents = copy.deepcopy(CARD_AGENTS)
    agents["smc"] = {"direction": "SELL", "confidence": 95}
    gate = ra._planner_execution_gate(_decision(agents), CONFIG)
    assert "smc" not in gate.get("support_agents", [])
    assert "smc" in gate.get("oppose_agents", [])


# ── the second door ─────────────────────────────────────────────────────────

def test_the_profile_honours_the_config():
    """A constant compiled into strategy_profiles must not outrank config."""
    results = {"smc": {"setup_structure": {"setup_type": "FAILED_RECLAIM_CONTINUATION"}}}
    profile = DecisionAgent(CONFIG)._strategy_profile(results)
    assert int(profile["agent_min_confidence"]) == 67, (
        "the built-in profile default overrode the operator's configured bar"
    )


def test_an_explicit_per_profile_override_still_wins():
    """Tuning one profile in config.json is a decision, and must be kept."""
    cfg = copy.deepcopy(CONFIG)
    cfg["strategy_profiles"] = {"classic_consensus": {"agent_min_confidence": 75}}
    results = {"smc": {"setup_structure": {"setup_type": "FAILED_RECLAIM_CONTINUATION"}}}
    profile = DecisionAgent(cfg)._strategy_profile(results)
    assert int(profile["agent_min_confidence"]) == 75


def test_changing_the_config_moves_the_profile():
    cfg = copy.deepcopy(CONFIG)
    cfg["signal_requirements"]["agent_min_confidence"] = 80
    results = {"smc": {"setup_structure": {"setup_type": "FAILED_RECLAIM_CONTINUATION"}}}
    profile = DecisionAgent(cfg)._strategy_profile(results)
    assert int(profile["agent_min_confidence"]) == 80


# ── nothing else moved ──────────────────────────────────────────────────────

def test_no_risk_setting_was_changed():
    risk = CONFIG["risk_settings"]
    assert float(risk["min_rr_ratio"]) == 1.5
    assert float(risk["min_sl_distance_points"]) == 400.0
    rule = risk["stop_from_liquidity"]
    assert rule["min_liquidity_points"] == 200
    assert rule["safety_buffer_points"] == 70
    assert rule["max_stop_points"] == 400


def test_no_planner_threshold_was_changed():
    planner = CONFIG["session_planner"]
    assert float(planner["min_primary_quality_score"]) == 70
    assert int(planner["min_authority_alignment_count"]) == 2
    conviction = planner.get("archetype_conviction") or {}
    assert float(conviction.get("medium_conviction_confidence", 60)) == 60
