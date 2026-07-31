"""A setup the SMC agent ranked and declined must not become an order.

2026-07-31, TRADE_20260731_152110_326407_a5520ee6 shipped as a live
SELL LIMIT carrying:

    Setup: Liquidity Reversal · role REJECTED · state ENTRY_ARMED
           · lead smc · quality C
    POI selection: reach 40.8 · dominance 47.6 · revisit LOW

``selection_role`` is SMCAgent's verdict on its own candidates
(agents/smc_agent.py:1148): rank 1 becomes PRIMARY, a qualifying rank 2
becomes STANDBY, and everything else is labelled REJECTED. It is not a
neutral label -- it means the agent that found the setup looked at it and
did not choose it.

Both of that card's scores also sit below the planner's own floors
(``min_primary_dominance`` 50, ``min_return_probability`` 42), so the planner
path would have refused it outright. The dual-agent path does not run those
floors, and nothing else looked at the role, so it went out.

TWO LEAKS, TWO FIXES
--------------------
1. ``_select_setup_candidate`` re-sorted every candidate by quality score and
   returned the top one, never reading the role. A declined candidate could
   be handed to a signal purely by out-scoring the other leftovers. Selected
   roles now win absolutely; quality still orders within them.

2. Nothing refused to publish. ``_rejected_setup_execution_block`` now stops
   the order at the same gate that already checks geometry, reachability and
   bets against a live winner.

SCOPE
-----
This is a quality gate, not a risk gate. No stop, target, ratio or size is
touched. A candidate with no role at all is treated as selected, so snapshots
written before the labelling behave exactly as they did.
"""

from __future__ import annotations

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "run_analysis_rejected", os.path.join(ROOT, "scripts", "run_analysis.py")
)
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)

from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()

# The card, verbatim.
THE_CARD = {
    "decision": "SELL", "symbol": "XAU/USD", "current_price": 4044.28,
    "signal": {
        "order_type": "SELL_LIMIT", "entry": {"price": 4057.07},
        "stop_loss": 4097.07, "tp1": 4007.07, "tp2": 3967.07,
    },
    "setup_context": {
        "selection_role": "REJECTED", "quality_grade": "C",
        "thesis_dominance_score": 47.6, "return_probability_score": 40.8,
    },
}


def _violations(decision) -> list[str]:
    return [v for v in ra.validate_signal_before_send(decision, CONFIG, []) if "ranked" in v]


def _with_role(role: str | None) -> dict:
    setup = dict(THE_CARD["setup_context"])
    if role is None:
        setup.pop("selection_role", None)
    else:
        setup["selection_role"] = role
    return {**THE_CARD, "setup_context": setup}


# ── the published card ──────────────────────────────────────────────────────

def test_the_published_card_is_now_refused() -> None:
    violations = _violations(THE_CARD)
    assert len(violations) == 1
    assert "REJECTED" in violations[0]
    assert "47.6" in violations[0] and "40.8" in violations[0], (
        "the message must carry the numbers, so the refusal can be audited"
    )


def test_its_scores_were_below_the_planner_floors() -> None:
    """Why this card should never have reached execution."""
    planner = CONFIG["session_planner"]
    assert THE_CARD["setup_context"]["thesis_dominance_score"] < float(
        planner["min_primary_dominance"]
    )
    assert THE_CARD["setup_context"]["return_probability_score"] < float(
        planner["min_return_probability"]
    )


# ── selected roles still pass ───────────────────────────────────────────────

def test_a_primary_setup_is_untouched() -> None:
    assert _violations(_with_role("PRIMARY")) == []


def test_a_standby_setup_is_untouched() -> None:
    assert _violations(_with_role("STANDBY")) == []


def test_starter_and_add_on_legs_are_untouched() -> None:
    for role in ("STARTER", "ADD_ON"):
        assert _violations(_with_role(role)) == [], f"{role} is a selected leg"


def test_an_absent_role_is_not_blocked() -> None:
    """Older snapshots carry no role; absence of data is not a verdict."""
    assert _violations(_with_role(None)) == []


def test_a_wait_decision_is_not_evaluated() -> None:
    assert _violations({**THE_CARD, "decision": "WAIT"}) == []


def test_the_block_can_be_disabled_by_config() -> None:
    relaxed = {**CONFIG, "execution_guards": {
        **(CONFIG.get("execution_guards") or {}), "allow_rejected_setups": True,
    }}
    assert [
        v for v in ra.validate_signal_before_send(THE_CARD, relaxed, []) if "ranked" in v
    ] == []


# ── candidate selection ─────────────────────────────────────────────────────

def _pool(*candidates) -> dict:
    return {"smc": {"setup_candidates": list(candidates)}}


def test_a_selected_role_outranks_a_higher_scoring_rejected_one() -> None:
    chosen = ra._select_setup_candidate("SELL", _pool(
        {"direction": "SELL", "selection_role": "REJECTED", "quality_score": 79, "id": "rejected-high"},
        {"direction": "SELL", "selection_role": "PRIMARY", "quality_score": 71, "id": "primary-low"},
    ))
    assert chosen["id"] == "primary-low", (
        "quality score must not promote a candidate the agent declined"
    )


def test_quality_still_orders_within_the_selected_group() -> None:
    chosen = ra._select_setup_candidate("SELL", _pool(
        {"direction": "SELL", "selection_role": "STANDBY", "quality_score": 74, "id": "standby"},
        {"direction": "SELL", "selection_role": "PRIMARY", "quality_score": 88, "id": "primary"},
    ))
    assert chosen["id"] == "primary"


def test_a_rejected_candidate_is_still_returned_when_it_is_all_there_is() -> None:
    """The payload feeds reporting too; refusing to publish is a separate gate."""
    chosen = ra._select_setup_candidate("SELL", _pool(
        {"direction": "SELL", "selection_role": "REJECTED", "quality_score": 60, "id": "only"},
    ))
    assert chosen["id"] == "only"


def test_the_direction_filter_still_applies() -> None:
    chosen = ra._select_setup_candidate("SELL", _pool(
        {"direction": "BUY", "selection_role": "PRIMARY", "quality_score": 95, "id": "wrong-side"},
        {"direction": "SELL", "selection_role": "REJECTED", "quality_score": 55, "id": "right-side"},
    ))
    assert chosen["id"] == "right-side"


def test_an_empty_pool_returns_empty() -> None:
    assert ra._select_setup_candidate("SELL", {"smc": {}}) == {}


# ── risk untouched ──────────────────────────────────────────────────────────

def test_no_risk_setting_was_changed() -> None:
    risk = CONFIG["risk_settings"]
    assert float(risk["min_sl_distance_points"]) == 400.0
    assert float(risk["min_rr_ratio"]) == 1.5
    assert float(risk["max_rr_ratio"]) == 4.0
    assert int(risk["max_open_trades"]) == 3
    dual = CONFIG.get("dual_agent_mode") or {}
    if dual:
        assert int(dual.get("cross_entry_distance_points", 200)) == 200, (
            "the 200-pt cross-entry barrier is unchanged by this fix"
        )


def test_fault_injection_the_old_selection_promoted_the_rejected_setup() -> None:
    """Rebuild the pre-fix selection and show it picks the declined candidate."""
    candidates = [
        {"direction": "SELL", "selection_role": "REJECTED", "quality_score": 79, "id": "rejected-high"},
        {"direction": "SELL", "selection_role": "PRIMARY", "quality_score": 71, "id": "primary-low"},
    ]
    # The old body, verbatim: sort by quality alone, take the top.
    old = sorted(
        candidates,
        key=lambda c: float((c.get("setup_quality") or {}).get("score", c.get("quality_score", 0)) or 0),
        reverse=True,
    )[0]
    assert old["id"] == "rejected-high", (
        "scoring alone hands a declined setup to the signal — this is how "
        "a5520ee6 reached execution"
    )
    assert ra._select_setup_candidate("SELL", _pool(*candidates))["id"] == "primary-low"
