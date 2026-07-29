"""A confirmed counter-trend raid is a named day, not an unclassified one.

Background
----------
Across 274 live cycles the archetype layer produced:

    125  STRUCTURE_BIAS_DAY      floor 50   -> always refused
     50  RANGE_TRAP_DAY          floor 50   -> always refused
      7  REVERSAL_AFTER_SWEEP_DAY

64% of cycles landed on a fallback whose floor sits below the conviction bar,
and the per-direction breakdown showed the consequence exactly:

    SELL  (25 refused)   25  100.0%  archetype conviction LOW
    BUY   ( 3 refused)    3  100.0%  archetype conviction LOW

Every directional plan that was refused died at that one gate. Not
reward-to-risk, not quality, not the counter-objective reversal proof --
conviction, every time.

Sweeping the classifier shows why. ``REVERSAL_AFTER_SWEEP_DAY`` demands four
facts at once, and one of them is a STRONG sweep grade:

    STRONG    LIQUIDITY_REVERSAL     -> REVERSAL_AFTER_SWEEP_DAY  66  ✅
    MODERATE  ORDER_BLOCK_PULLBACK   -> STRUCTURE_BIAS_DAY        50  ❌
    MODERATE  STRUCTURE_CONTINUATION -> STRUCTURE_BIAS_DAY        50  ❌

A MODERATE grade is not an unconfirmed sweep. The detector assigns it when
price pierced the level and *closed back inside it* -- the reversal has
already printed. Only the close position within the bar separates it from
STRONG. Dropping such a day into "no cleaner archetype dominated" discards a
real, observed event.

This widens the reversal branch to accept a MODERATE raid, while keeping the
grade visible in the score: a MODERATE-graded reversal day is classified, but
it is worth less than a STRONG one, and it still has to clear the bar on its
own evidence. The fallbacks keep their floor of 50 and stay refused.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.smc_agent import SMCAgent
from services.session_planner import SessionPlannerService

CONFIG = {"symbol": "XAU/USD"}
BAR = 60.0


def _classify(
    *,
    trend: str = "BULLISH",
    sweep_type: str | None = "buy_side",
    confirmation: str = "MODERATE",
    zone: str = "PREMIUM",
    setup: str = "ORDER_BLOCK_PULLBACK",
    quality: str = "STRONG",
    dominance: float = 0.0,
    trigger: str = "AT_POI_WAIT_TRIGGER",
) -> dict:
    return SMCAgent(CONFIG)._day_archetype(
        direction="SELL" if sweep_type == "buy_side" else "BUY",
        market_structure={"trend": trend, "structure_quality": quality},
        liquidity={"recent_sweep": {"occurred": sweep_type is not None,
                                    "type": sweep_type,
                                    "confirmation": confirmation}},
        zone=zone,
        setup_candidates=[{"setup_type": setup, "trigger_state": trigger,
                           "thesis_dominance_score": dominance}],
    )


# ── The coverage gap ───────────────────────────────────────────────────────

def test_moderate_counter_trend_raid_is_a_reversal_day() -> None:
    """Price closed back inside the level. That is a reversal, not a shrug.

    Failure injection: restoring ``sweep_confirmation == "STRONG"`` sends this
    back to STRUCTURE_BIAS_DAY at 50.
    """
    archetype = _classify(confirmation="MODERATE")

    assert archetype["name"] == "REVERSAL_AFTER_SWEEP_DAY", (
        "a confirmed counter-trend raid in the matching extreme must be "
        f"classified, got {archetype['name']} at {archetype['confidence']}"
    )


def test_moderate_reversal_can_clear_the_conviction_bar() -> None:
    """Classification is worthless if the plan still dies at the gate."""
    archetype = _classify(confirmation="MODERATE")
    assert archetype["confidence"] >= BAR, (
        f"got {archetype['confidence']}, bar {BAR}"
    )


def test_strong_raid_still_outranks_a_moderate_one() -> None:
    """The grade must keep meaning something."""
    strong = _classify(confirmation="STRONG")
    moderate = _classify(confirmation="MODERATE")

    assert strong["confidence"] > moderate["confidence"], (
        f"STRONG={strong['confidence']} must beat MODERATE={moderate['confidence']}"
    )


def test_reversal_works_on_both_sides() -> None:
    """A sell-side raid in discount is the mirror case."""
    archetype = _classify(trend="BEARISH", sweep_type="sell_side",
                          zone="DISCOUNT", confirmation="MODERATE")
    assert archetype["name"] == "REVERSAL_AFTER_SWEEP_DAY"
    assert archetype["confidence"] >= BAR


# ── Guards: the fallbacks must stay refused ────────────────────────────────

def test_weak_raid_still_falls_to_the_fallback() -> None:
    """WEAK means price never closed back inside. Nothing was proven."""
    archetype = _classify(confirmation="WEAK")

    assert archetype["name"] in {"STRUCTURE_BIAS_DAY", "RANGE_TRAP_DAY"}
    assert archetype["confidence"] < BAR


def test_raid_outside_the_matching_extreme_is_not_a_reversal() -> None:
    """A raid at equilibrium is continuation fuel, not a turn."""
    for zone in ("EQUILIBRIUM", "DISCOUNT"):
        archetype = _classify(confirmation="MODERATE", zone=zone)
        assert archetype["name"] != "REVERSAL_AFTER_SWEEP_DAY", (
            f"buy-side raid at {zone} must not be a sell reversal"
        )


def test_raid_aligned_with_the_trend_is_not_a_reversal() -> None:
    """A sell-side raid inside a bullish leg continues it."""
    archetype = _classify(trend="BULLISH", sweep_type="sell_side",
                          zone="PREMIUM", confirmation="MODERATE")
    assert archetype["name"] != "REVERSAL_AFTER_SWEEP_DAY"


def test_no_raid_means_no_reversal_day() -> None:
    """Absence of a sweep cannot produce a sweep archetype."""
    archetype = _classify(sweep_type=None)
    assert archetype["name"] in {"STRUCTURE_BIAS_DAY", "RANGE_TRAP_DAY"}
    assert archetype["confidence"] < BAR


def test_weak_structure_still_blocks_the_reversal_branch() -> None:
    """Structure quality remains a required fact."""
    archetype = _classify(confirmation="MODERATE", quality="WEAK")
    assert archetype["name"] != "REVERSAL_AFTER_SWEEP_DAY"


def test_fallback_floors_are_untouched() -> None:
    """STRUCTURE_BIAS_DAY and RANGE_TRAP_DAY stay below the bar."""
    for trend, expected in (("BEARISH", "STRUCTURE_BIAS_DAY"),
                            ("RANGING", "RANGE_TRAP_DAY")):
        archetype = _classify(trend=trend, sweep_type=None, quality="WEAK",
                              setup="SMC_CONTEXT", zone="EQUILIBRIUM")
        assert archetype["name"] == expected
        assert archetype["confidence"] < BAR


def test_the_conviction_threshold_is_untouched() -> None:
    """The bar does not move. Only what can reach it does."""
    planner = SessionPlannerService({"symbol": "XAU/USD",
                                     "session_planner": {"enabled": True}})
    assert planner.archetype_medium_conviction == 60.0
    assert planner.archetype_high_conviction == 75.0


def test_branch_cap_still_applies() -> None:
    """An earned floor must not outrun the branch ceiling."""
    archetype = _classify(confirmation="STRONG", dominance=100.0)
    assert archetype["confidence"] <= 90
