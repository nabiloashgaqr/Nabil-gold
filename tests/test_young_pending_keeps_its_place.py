"""A freshly placed order gets time to fill before it can be replaced.

2026-08-03, 14:03 -> 14:08.

    14:03  SELL LIMIT 03ed828a published at 4045.99
           quality B 74.0 · dominance 77.3 · freshness FRESH
    14:08  "Scenario Family Replaced ... Pending Orders Cancelled: 1"

Five minutes. The order was not stale, not invalidated, not offside -- it had
simply not been reached yet, which is the entire premise of a LIMIT order.
It was cancelled because a new plan scored four points higher.

TWO FAULTS, ONE SYMPTOM
-----------------------
1. ``ScenarioGovernor`` never looked at how long an order had been resting.
   Nothing in the class read a timestamp. An order one minute old and an
   order six hours old were treated identically.

2. The replacement bars were 4.0 (score) and 5.0 (dominance) on measures that
   routinely move by that much between cycles. A four-point difference is
   inside the noise of the thing being measured, so "improvement" meant
   "recalculated".

Reproduced before the fix: incumbent 74.0, newcomer 78.0 -> REPLACE.

THE RULES NOW
-------------
    replace_grace_minutes = 30          (new)
    min_plan_score_improvement = 12     (was 4)
    min_primary_dominance_improvement = 15  (was 5)

A stale or revalidation-required family is still replaceable at ANY age. The
grace protects an order that is merely young; it never protects an order that
is wrong. That distinction is the whole design: the check sits after
``stale_family`` precisely so a dead order cannot hide behind its birthday.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.scenario_governor import ScenarioGovernor  # noqa: E402
from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()
SYMBOL = "XAU/USD"
GOVERNOR = CONFIG["scenario_governor"]


def _incumbent(minutes_old: float, *, quality: float = 74.0,
               dominance: float = 77.3, freshness: str = "FRESH",
               with_timestamp: bool = True) -> dict:
    born = datetime.now(timezone.utc) - timedelta(minutes=minutes_old)
    trade = {
        "id": "TRADE_20260803_140309_647497_03ed828a",
        "symbol": SYMBOL, "type": "SELL", "status": "PENDING",
        "entry_price": 4045.99,
        "signal_snapshot": {
            "quality": {"score": quality, "grade": "B"},
            "pending_runtime": {"freshness_state": freshness},
            "setup_context": {"thesis_dominance_score": dominance,
                              "selection_role": "PRIMARY"},
        },
    }
    if with_timestamp:
        trade["created_at"] = born.isoformat()
        trade["entry_time"] = born.isoformat()
    return trade


def _plan(score: float, dominance: float) -> dict:
    return {
        "symbol": SYMBOL, "session_bias": "SELL", "plan_ready": True,
        "scenario_id": "SCENARIO::XAU/USD::20260803::SELL::CONTINUATION_BREAKDOWN",
        "planner_confidence": score,
        "primary_poi": {"thesis_dominance_score": dominance, "direction": "SELL"},
    }


def _review(incumbent: dict, plan: dict, config=None) -> dict:
    return ScenarioGovernor(config or CONFIG).review_new_plan(plan, [incumbent])


# ── the configured rules ────────────────────────────────────────────────────

def test_the_configured_grace_and_bars() -> None:
    assert float(GOVERNOR["replace_grace_minutes"]) == 30.0
    assert float(GOVERNOR["min_plan_score_improvement"]) == 12.0
    assert float(GOVERNOR["min_primary_dominance_improvement"]) == 15.0


# ── the incident ────────────────────────────────────────────────────────────

def test_the_five_minute_old_order_keeps_its_place() -> None:
    """The incident. Either guard alone is enough to save it."""
    result = _review(_incumbent(5), _plan(80.0, 78.0))

    assert result["action"] == "KEEP_EXISTING_FAMILY"
    assert result["cancelled_ids"] == []
    # With the raised bars a +6 score gap fails the score test first, so the
    # reason quotes the scores rather than the age. Both guards would refuse
    # it; the threshold simply reaches the verdict earlier. An earlier version
    # of this test demanded the age wording and was wrong to -- see
    # test_the_grace_alone_saves_a_genuinely_stronger_plan for the case that
    # isolates the grace.
    assert "old_score=74.0" in result["reason"]


def test_the_grace_alone_saves_a_genuinely_stronger_plan() -> None:
    """Isolate the age guard: a newcomer that clears BOTH raised bars.

    Without the grace this would replace. Inside it, the young order keeps
    its place and the reason says so.
    """
    score_bar = float(GOVERNOR["min_plan_score_improvement"])
    dom_bar = float(GOVERNOR["min_primary_dominance_improvement"])
    strong = _plan(74.0 + score_bar, 77.3 + dom_bar)

    result = _review(_incumbent(5), strong)
    assert result["action"] == "KEEP_EXISTING_FAMILY"
    assert "min old" in result["reason"], (
        "when the age is what saved the order, the message must say so"
    )
    assert "30 min" in result["reason"]

    # The same plan against the same order, 45 minutes later.
    assert _review(_incumbent(45), strong)["action"] == "REPLACE_PENDING_FAMILY"


def test_the_old_bars_would_have_cancelled_it() -> None:
    """Fault injection on the thresholds alone."""
    relaxed = {
        **CONFIG,
        "scenario_governor": {
            **GOVERNOR, "replace_grace_minutes": 0,
            "min_plan_score_improvement": 4, "min_primary_dominance_improvement": 5,
        },
    }
    result = _review(_incumbent(5), _plan(80.0, 78.0), config=relaxed)
    assert result["action"] == "REPLACE_PENDING_FAMILY", (
        "at the old 4-point bar with no grace, a +6 recalculation evicted a "
        "five-minute-old order -- this is the behaviour being replaced"
    )


def test_a_much_stronger_plan_still_waits_out_the_grace() -> None:
    """Inside the grace, strength is not the question -- time is."""
    result = _review(_incumbent(5), _plan(95.0, 99.0))
    assert result["action"] == "KEEP_EXISTING_FAMILY"


# ── after the grace ─────────────────────────────────────────────────────────

def test_after_the_grace_a_marginal_plan_is_still_refused() -> None:
    """The raised bar keeps working once the grace has passed."""
    result = _review(_incumbent(45), _plan(80.0, 78.0))
    assert result["action"] == "KEEP_EXISTING_FAMILY"


def test_after_the_grace_a_genuinely_stronger_plan_replaces() -> None:
    score_bar = float(GOVERNOR["min_plan_score_improvement"])
    dom_bar = float(GOVERNOR["min_primary_dominance_improvement"])
    result = _review(
        _incumbent(45), _plan(74.0 + score_bar, 77.3 + dom_bar)
    )
    assert result["action"] == "REPLACE_PENDING_FAMILY"


# ── the grace must not protect a dead order ─────────────────────────────────

def test_a_stale_order_is_replaceable_at_any_age() -> None:
    result = _review(_incumbent(2, freshness="STALE"), _plan(80.0, 78.0))
    assert result["action"] == "REPLACE_PENDING_FAMILY", (
        "the grace protects an order that is young, never one that is dead"
    )


def test_a_revalidation_required_order_is_replaceable_at_any_age() -> None:
    result = _review(
        _incumbent(1, freshness="REVALIDATION_REQUIRED"), _plan(80.0, 78.0)
    )
    assert result["action"] == "REPLACE_PENDING_FAMILY"


def test_an_order_with_no_timestamp_is_not_protected() -> None:
    """Absence of a birthday is not evidence of youth."""
    score_bar = float(GOVERNOR["min_plan_score_improvement"])
    dom_bar = float(GOVERNOR["min_primary_dominance_improvement"])
    result = _review(
        _incumbent(1, with_timestamp=False),
        _plan(74.0 + score_bar, 77.3 + dom_bar),
    )
    assert result["action"] == "REPLACE_PENDING_FAMILY"


def test_the_grace_can_be_disabled() -> None:
    off = {**CONFIG, "scenario_governor": {**GOVERNOR, "replace_grace_minutes": 0}}
    score_bar = float(GOVERNOR["min_plan_score_improvement"])
    dom_bar = float(GOVERNOR["min_primary_dominance_improvement"])
    result = _review(
        _incumbent(5), _plan(74.0 + score_bar, 77.3 + dom_bar), config=off
    )
    assert result["action"] == "REPLACE_PENDING_FAMILY"


# ── the age helper ──────────────────────────────────────────────────────────

def test_age_is_measured_from_the_youngest_leg() -> None:
    """A family is protected while any leg still has a chance to fill."""
    old_leg = _incumbent(120)
    new_leg = _incumbent(3)
    new_leg["id"] = "SIBLING"
    age = ScenarioGovernor._youngest_age_minutes([old_leg, new_leg])
    assert age is not None and age < 5


def test_an_unreadable_timestamp_yields_no_age() -> None:
    broken = _incumbent(5)
    broken["created_at"] = "not-a-date"
    broken["entry_time"] = ""
    assert ScenarioGovernor._youngest_age_minutes([broken]) is None


# ── risk untouched ──────────────────────────────────────────────────────────

def test_no_risk_setting_was_changed() -> None:
    risk = CONFIG["risk_settings"]
    assert float(risk["min_sl_distance_points"]) == 400.0
    assert float(risk["min_rr_ratio"]) == 1.5
    assert int(risk["max_open_trades"]) == 3
    post_tp2 = CONFIG["post_tp2_reentry"]
    assert float(post_tp2["min_distance_points"]) == 250.0
    # 3.0 -> 2.5 on 2026-08-03 at the operator's request; still pinned.
    assert float(post_tp2["window_hours"]) == 2.5
