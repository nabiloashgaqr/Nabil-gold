"""A reversal after a sweep must be classifiable as one.

The archetype layer had two continuation branches and a catch-all. Both
branches require the trend to already point the way the trade wants to go --
which a reversal, by definition, cannot satisfy: the sweep is what ends the
prior leg. So a confirmed buy-side sweep, rejected back into premium, with a
reversal candidate leading the book, fell through to the generic
STRUCTURE_BIAS_DAY at its 50% floor and was refused as low conviction.

That is a classification gap, not strictness. The 60% conviction bar is
untouched here, as are the agent-consensus, dissent, RR and final-validation
gates. The only change is that a legitimate pattern now has a name.

Every condition is required, so this describes a cleaner picture than the
continuation branches rather than a cheaper one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.smc_agent import SMCAgent
from services.session_planner import SessionPlannerService

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

CONVICTION_BAR = 60.0


def _archetype(
    *,
    trend="BULLISH",
    sweep="buy_side",
    confirmation="STRONG",
    zone="PREMIUM",
    setup="ORDER_BLOCK_PULLBACK",
    structure_quality="MODERATE",
    dominance=50.0,
):
    """Dominance defaults to 50 -- the value that produced the live refusal."""
    return SMCAgent(CONFIG)._day_archetype(
        direction="SELL",
        market_structure={"trend": trend, "structure_quality": structure_quality},
        liquidity={"recent_sweep": {"type": sweep, "confirmation": confirmation,
                                    "occurred": bool(sweep)}},
        zone=zone,
        setup_candidates=(
            [{"setup_type": setup, "thesis_dominance_score": dominance, "trigger_state": ""}]
            if setup else []
        ),
    )


# --- the setup that was refused -----------------------------------------

def test_the_chart_setup_is_now_classified() -> None:
    """Confirmed sweep + rejection + premium + reversal candidate."""
    result = _archetype()

    assert result["name"] == "REVERSAL_AFTER_SWEEP_DAY"
    assert result["confidence"] >= CONVICTION_BAR
    assert result["preferred_execution_family"] == "REVERSAL_MAP"
    assert "buy-side sweep rejected back into premium" in result["reason"]


def test_the_mirror_case_classifies_too() -> None:
    """A sell-side sweep rejected out of discount is the same pattern."""
    result = _archetype(trend="BEARISH", sweep="sell_side", zone="DISCOUNT",
                        setup="LIQUIDITY_REVERSAL")

    assert result["name"] == "REVERSAL_AFTER_SWEEP_DAY"
    assert result["confidence"] >= CONVICTION_BAR


def test_a_ranging_structure_still_qualifies() -> None:
    """Reversals form out of ranges as readily as out of trends."""
    assert _archetype(trend="RANGING")["name"] == "REVERSAL_AFTER_SWEEP_DAY"


# --- what must still be refused ------------------------------------------

@pytest.mark.parametrize("confirmation", ["WEAK", ""])
def test_a_sweep_without_a_confirmed_rejection_is_refused(confirmation: str) -> None:
    """An unconfirmed raid never printed a reversal, so it is not one.

    UPDATED: MODERATE was removed from this list.

    The original reasoning was that STRONG is "the detector's own label for
    closing back inside the level". Reading the detector shows that is true of
    MODERATE as well -- both require ``close < level`` after the wick pierced
    it, and they differ only in where the close sits inside the bar (far third
    versus far half). The rejection has printed in both cases.

    Live data settled it. Over 274 cycles, 64% of days fell to a fallback
    archetype with a floor of 50, and every directional plan refused in that
    window died at the conviction gate -- 25 of 25 SELL maps and 3 of 3 BUY
    maps, all reporting "archetype conviction LOW". Excluding MODERATE was a
    large share of that.

    WEAK stays refused: there the close did not return inside the level, so no
    reversal was printed at all. That distinction is the one that matters and
    it is preserved.
    """
    result = _archetype(confirmation=confirmation)

    assert result["name"] != "REVERSAL_AFTER_SWEEP_DAY"
    assert result["confidence"] < CONVICTION_BAR


def test_a_moderate_rejection_is_classified_but_ranked_lower() -> None:
    """MODERATE qualifies, and STRONG still outranks it."""
    moderate = _archetype(confirmation="MODERATE")
    strong = _archetype(confirmation="STRONG")

    assert moderate["name"] == "REVERSAL_AFTER_SWEEP_DAY"
    assert moderate["confidence"] >= CONVICTION_BAR
    assert strong["confidence"] > moderate["confidence"]


def test_the_wrong_half_of_the_range_is_refused() -> None:
    """Selling a sweep from discount is not a premium reversal."""
    result = _archetype(zone="DISCOUNT")

    assert result["name"] != "REVERSAL_AFTER_SWEEP_DAY"
    assert result["confidence"] < CONVICTION_BAR


def test_weak_structure_is_refused() -> None:
    result = _archetype(structure_quality="WEAK")

    assert result["name"] != "REVERSAL_AFTER_SWEEP_DAY"
    assert result["confidence"] < CONVICTION_BAR


def test_a_non_reversal_candidate_is_refused() -> None:
    """The book has to be led by a reversal-family setup."""
    result = _archetype(setup="STRUCTURE_CONTINUATION")

    assert result["name"] != "REVERSAL_AFTER_SWEEP_DAY"
    assert result["confidence"] < CONVICTION_BAR


def test_no_sweep_means_no_reversal_archetype() -> None:
    result = _archetype(sweep="", confirmation="", zone="EQUILIBRIUM",
                        setup="STRUCTURE_CONTINUATION")

    assert result["name"] != "REVERSAL_AFTER_SWEEP_DAY"
    assert result["confidence"] < CONVICTION_BAR


# --- it must not displace the existing branches --------------------------

def test_continuation_after_sweep_still_wins_when_it_applies() -> None:
    """A with-trend sweep is continuation, not reversal."""
    result = _archetype(trend="BEARISH", sweep="buy_side", zone="PREMIUM",
                        setup="LIQUIDITY_REVERSAL")

    assert result["name"] == "CONTINUATION_AFTER_SWEEP_DAY"


def test_confirmed_triggers_still_take_priority() -> None:
    result = SMCAgent(CONFIG)._day_archetype(
        direction="SELL",
        market_structure={"trend": "BULLISH", "structure_quality": "MODERATE"},
        liquidity={"recent_sweep": {"type": "buy_side", "confirmation": "STRONG"}},
        zone="PREMIUM",
        setup_candidates=[{
            "setup_type": "ORDER_BLOCK_PULLBACK",
            "thesis_dominance_score": 50,
            "trigger_state": "FAILED_RECLAIM_CONFIRMED",
        }],
    )

    assert result["name"] == "FAILED_RECLAIM_DAY"


# --- confidence is earned, not pinned ------------------------------------

def test_a_dominant_thesis_lifts_confidence() -> None:
    floor = _archetype(dominance=50.0)["confidence"]
    strong = _archetype(dominance=84.0)["confidence"]

    assert strong > floor
    assert strong <= 90, "confidence must stay capped"


# --- family alignment ----------------------------------------------------

def test_the_reversal_family_accepts_an_order_block_pullback() -> None:
    """Otherwise the planner marks this very archetype as contradicting.

    The same structure reads as continuation when price returns to a block
    with the trend, and as reversal when it returns to one left behind by a
    confirmed counter-trend sweep.
    """
    assert "ORDER_BLOCK_PULLBACK" in SessionPlannerService.ARCHETYPE_FAMILY_SETUPS["REVERSAL_MAP"]


def test_the_planner_grants_full_conviction_to_the_new_archetype() -> None:
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})

    conviction = service._archetype_conviction(
        archetype="REVERSAL_AFTER_SWEEP_DAY",
        archetype_confidence=_archetype()["confidence"],
        preferred_execution_family="REVERSAL_MAP",
        primary={"setup_type": "ORDER_BLOCK_PULLBACK"},
    )

    assert conviction["family_aligned"] is True
    assert conviction["allow_execution"] is True
    assert conviction["level"] != "LOW"


def test_the_conviction_bar_itself_is_unchanged() -> None:
    """Quality was never traded for volume: the threshold did not move."""
    assert CONFIG["session_planner"]["archetype_conviction"]["medium_conviction_confidence"] == 60
