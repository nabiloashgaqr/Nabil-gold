"""A pending order must be judged on the evidence it actually carries.

2026-07-30, three minutes apart:

    14:03  A 88.9 — 3-AGENT CONSENSUS, 4/5 qualified agents, no opposition,
                    POI dominance 99.3, entry 4103.34
    14:06  D 59.0 — DUAL-AGENT + MACRO, 2/5 qualified, dominance 63.8
           -> "Scenario Family Replaced ... Pending Orders Cancelled: 1"

The strong order was evicted by the weak one. `_plan_score` read the
incumbent's `session_plan`, but a consensus signal is admitted through a
different path and stores none, so it scored 0.0. The governor therefore saw
an improvement of 59 - 0 = 59 against a 4.0 threshold and cancelled the
better order.

The incumbent now falls back to the quality score it was actually graded
with, so it defends itself with its own number instead of a zero it never
earned.

Fault injection: restore `old_score = self._plan_score(incumbent_plan)` and
`test_a_weak_plan_cannot_evict_a_strong_consensus_order` fails.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.scenario_governor import ScenarioGovernor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOL = "XAU/USD"


def _config() -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _consensus_order(quality: float = 88.9, dominance: float = 99.3) -> dict:
    """A 3-AGENT CONSENSUS pending order: graded, but with no session_plan."""
    return {
        "id": "TRADE_20260730_140308_261151_aa5714c0", "symbol": SYMBOL,
        "type": "BUY", "status": "PENDING", "entry_price": 4103.34,
        "signal_snapshot": {
            "setup_context": {"thesis_dominance_score": dominance, "quality": "A+"},
            "quality": {"score": quality},
        },
    }


def _planner_order(planner_confidence: float, dominance: float = 70.0) -> dict:
    return {
        "id": "INC_PLANNER", "symbol": SYMBOL, "type": "BUY", "status": "PENDING",
        "entry_price": 4072.59,
        "signal_snapshot": {
            "session_plan": {"planner_confidence": planner_confidence},
            "setup_context": {"thesis_dominance_score": dominance},
        },
    }


def _new_plan(score: float, dominance: float) -> dict:
    return {
        "plan_ready": True, "symbol": SYMBOL, "session_bias": "BUY",
        "scenario_id": "SCENARIO::XAU/USD::20260730::LONDON::BUY::FAILED_RECLAIM",
        "planner_confidence": score,
        "primary_poi": {"thesis_dominance_score": dominance},
    }


def _review(incumbent, plan):
    return ScenarioGovernor(_config()).review_new_plan(plan, [incumbent])


# ── the live incident ──────────────────────────────────────────────────────

def test_a_weak_plan_cannot_evict_a_strong_consensus_order() -> None:
    result = _review(_consensus_order(), _new_plan(59.0, 63.8))
    assert result["action"] == "KEEP_EXISTING_FAMILY", (
        "a D 59.0 plan replaced an A 88.9 consensus order"
    )
    assert not result["cancelled_ids"]


def test_the_incumbent_is_scored_on_its_own_quality() -> None:
    governor = ScenarioGovernor(_config())
    assert governor._incumbent_score(_consensus_order()) == 88.9


def test_a_planner_order_still_scores_from_its_plan() -> None:
    """The original path is unchanged; the fallback is only for its absence."""
    governor = ScenarioGovernor(_config())
    assert governor._incumbent_score(_planner_order(83.5)) == 83.5


# ── replacement must still be possible ─────────────────────────────────────

def test_a_clearly_stronger_plan_still_replaces() -> None:
    result = _review(_consensus_order(quality=59.0, dominance=60.0),
                     _new_plan(88.0, 95.0))
    assert result["action"] == "REPLACE_PENDING_FAMILY"


def test_a_weak_incumbent_plan_is_still_replaceable() -> None:
    """A genuinely stronger plan still evicts a weak one.

    The margin is taken from config so this keeps testing the rule after a
    tuning pass rather than the number it was tuned to.
    """
    from utils.helpers import load_config
    governor = load_config()["scenario_governor"]
    score_bar = float(governor["min_plan_score_improvement"])
    dom_bar = float(governor["min_primary_dominance_improvement"])
    result = _review(
        _planner_order(50.0),
        _new_plan(50.0 + score_bar, 70.0 + dom_bar),
    )
    assert result["action"] == "REPLACE_PENDING_FAMILY"


def test_an_ungraded_incumbent_does_not_become_unbeatable() -> None:
    """No score anywhere means no defence: it must not lock the symbol."""
    bare = {"id": "BARE", "symbol": SYMBOL, "type": "BUY", "status": "PENDING",
            "entry_price": 4100.0, "signal_snapshot": {}}
    assert _review(bare, _new_plan(59.0, 63.8))["action"] == "REPLACE_PENDING_FAMILY"


def test_a_marginal_improvement_is_refused() -> None:
    """The 4-point floor exists to stop churn between near-identical signals."""
    result = _review(_consensus_order(quality=88.9, dominance=99.3),
                     _new_plan(92.0, 99.9))
    assert result["action"] == "KEEP_EXISTING_FAMILY", (
        "a 3.1-point improvement should not cancel a live pending order"
    )


# ── the fallback chain ─────────────────────────────────────────────────────

def test_confidence_is_used_when_no_quality_block_exists() -> None:
    governor = ScenarioGovernor(_config())
    trade = {
        "id": "C", "symbol": SYMBOL, "type": "BUY", "status": "PENDING",
        "entry_price": 4100.0,
        "signal_snapshot": {"confidence": 82.9, "setup_context": {}},
    }
    assert governor._incumbent_score(trade) == 82.9


def test_a_malformed_snapshot_scores_zero_rather_than_crashing() -> None:
    governor = ScenarioGovernor(_config())
    for snapshot in ("not json at all", None, 42, {"quality": "A+"}):
        trade = {"id": "X", "symbol": SYMBOL, "type": "BUY",
                 "status": "PENDING", "signal_snapshot": snapshot}
        assert governor._incumbent_score(trade) == 0.0


# ── an improvement on one axis must not hide a collapse on the other ───────
#
# The rule was a plain OR: better score OR better dominance. So a plan whose
# dominance rose by 6 could evict an incumbent whose quality it halved. On
# 2026-07-30 the governor cancelled two pending orders in a row this way.

def test_a_dominance_gain_cannot_mask_a_quality_collapse() -> None:
    incumbent = _consensus_order(quality=88.0, dominance=99.0)
    result = _review(incumbent, _new_plan(59.0, 105.0))
    assert result["action"] == "KEEP_EXISTING_FAMILY", (
        "dominance +6 let a plan 29 quality points worse take over"
    )


def test_a_quality_gain_cannot_mask_a_dominance_collapse() -> None:
    incumbent = _consensus_order(quality=59.0, dominance=63.0)
    result = _review(incumbent, _new_plan(88.0, 40.0))
    assert result["action"] == "KEEP_EXISTING_FAMILY"


def test_better_on_both_axes_still_replaces() -> None:
    incumbent = _consensus_order(quality=59.0, dominance=63.0)
    assert _review(incumbent, _new_plan(88.0, 70.0))["action"] == "REPLACE_PENDING_FAMILY"


def test_better_on_one_axis_and_flat_on_the_other_still_replaces() -> None:
    """Flat is not a regression: a genuine edge on either axis is enough.

    The edge is read from config rather than hardcoded. The bars were raised
    from 4/5 to 12/15 after 2026-08-03, when a five-minute-old FRESH order was
    cancelled for a four-point improvement; a test that pins the old number
    would have to be rewritten on every tuning pass, and would quietly stop
    testing the RULE.
    """
    from utils.helpers import load_config
    bar = float(load_config()["scenario_governor"]["min_primary_dominance_improvement"])
    incumbent = _consensus_order(quality=59.0, dominance=63.8)
    assert _review(
        incumbent, _new_plan(59.0, 63.8 + bar)
    )["action"] == "REPLACE_PENDING_FAMILY"
