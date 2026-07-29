"""LIQUIDITY_REVERSAL_DAY must earn its floor from the evidence, like the rest.

Background
----------
Phase F gave CONTINUATION_AFTER_SWEEP_DAY an evidence-derived floor: base 52
plus credit for a confirmed sweep, structure quality and an extreme zone. The
reversal branch was deliberately left alone that round so the two changes
could be measured separately.

Replaying the uploaded 2026-07-29 turtle-soup chart shows why it now has to
follow. When the raid grades MODERATE rather than STRONG, the chart produces:

    sweep       : buy_side / MODERATE
    SELL cand.  : 1  (LIQUIDITY_REVERSAL, present thanks to Phase G)
    archetype   : LIQUIDITY_REVERSAL_DAY at 55  ->  refused, "conviction LOW"

A valid reversal thesis with a real candidate dies on a hard-coded 55 that
reads no evidence at all. The branch scores the same 55 whether the raid was
confirmed or not, whether structure is strong or weak, and whether the
rejection was actually printed.

The bar stays at 60. What changes is that this branch can reach it on merit,
and still cannot reach it without merit.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.smc_agent import SMCAgent
from services.session_planner import SessionPlannerService

CONFIG = {"symbol": "XAU/USD"}
BAR = 60.0  # session_planner.archetype_conviction.medium_conviction_confidence


def _classify(
    *,
    confirmation: str = "MODERATE",
    quality: str = "STRONG",
    zone: str = "PREMIUM",
    trigger: str = "REJECTION_CONFIRMED",
    dominance: float = 0.0,
    trend: str = "RANGING",
) -> dict:
    """Force the LIQUIDITY_REVERSAL_DAY branch and read its verdict.

    ``trend`` defaults to RANGING so the earlier continuation and
    reversal-after-sweep branches do not capture the case first.
    """
    return SMCAgent(CONFIG)._day_archetype(
        direction="SELL",
        market_structure={"trend": trend, "structure_quality": quality},
        liquidity={"recent_sweep": {"occurred": True, "type": "buy_side",
                                    "confirmation": confirmation}},
        zone=zone,
        setup_candidates=[{"setup_type": "LIQUIDITY_REVERSAL",
                           "trigger_state": trigger,
                           "thesis_dominance_score": dominance}],
    )


# ── The refusal this fix removes ───────────────────────────────────────────

def test_confirmed_reversal_can_reach_the_conviction_bar() -> None:
    """Moderate raid + strong structure + premium + confirmed rejection.

    Every fact the branch can observe is present. It must be able to clear
    the bar without the bar moving.

    Failure injection: restoring ``max(55.0, top_conf)`` makes this fail at 55.
    """
    archetype = _classify()

    assert archetype["name"] == "LIQUIDITY_REVERSAL_DAY"
    assert archetype["confidence"] >= BAR, (
        "a reversal with a confirmed raid, strong structure, premium pricing "
        f"and a confirmed rejection must reach {BAR:.0f}, got "
        f"{archetype['confidence']}"
    )


def test_uploaded_chart_qualifies_once_the_rejection_prints() -> None:
    """End to end on the real chart, at the grade that used to refuse it.

    At the moment of the raid the chart reads AWAY_FROM_POI: price is at 4045
    while the mapped entry sits near 4027, so the market has not yet returned
    to the level to reject it. An earlier version of this test demanded a pass
    at that instant, which would have meant crediting a reversal thesis whose
    central evidence -- the rejection -- had not happened. The floor is
    deliberately just short of the bar there.

    What must hold is that the thesis clears the bar the moment the rejection
    is actually printed, without the bar moving.
    """
    from test_swing_sweep_detection import _uploaded_chart

    result = SMCAgent(CONFIG).analyze({
        "symbol": "XAU/USD", "timeframe": "15m",
        "data": _uploaded_chart((4048.6, 4044.0, 4045.0)),
    })
    sweep = (result.get("liquidity") or {}).get("recent_sweep") or {}
    sells = [c for c in (result.get("setup_candidates") or [])
             if str(c.get("direction") or "").upper() == "SELL"]

    assert sweep.get("confirmation") == "MODERATE", (
        f"fixture drift: expected a MODERATE raid, got {sweep.get('confirmation')}"
    )
    assert sells, "Phase G should already provide the SELL candidate"

    # Still watching: raid confirmed, rejection not yet printed.
    watching = float(result.get("day_archetype_confidence") or 0)
    assert 55 < watching < BAR, (
        "before the rejection prints the map should sit just under the bar, "
        f"not be dismissed at 55 (got {watching})"
    )

    # Price returns to the mapped level and rejects it.
    structure = result.get("market_structure") or {}
    rejected = SMCAgent(CONFIG)._day_archetype(
        direction="SELL",
        market_structure=structure,
        liquidity=result.get("liquidity") or {},
        zone=str(result.get("zone") or ""),
        setup_candidates=[{**sells[0], "trigger_state": "REJECTION_CONFIRMED"}],
    )
    assert rejected["confidence"] >= BAR, (
        "once the rejection is printed the chart's thesis must clear the bar "
        f"(got {rejected['name']} at {rejected['confidence']})"
    )


# ── Guards: the floor must stay unreachable without evidence ───────────────

def test_unconfirmed_raid_stays_below_the_bar() -> None:
    """A WEAK raid means price never closed back inside the level."""
    archetype = _classify(confirmation="WEAK", quality="WEAK", zone="EQUILIBRIUM",
                          trigger="AWAY_FROM_POI")

    assert archetype["confidence"] < BAR, (
        "a reversal resting on an unconfirmed raid, weak structure and no "
        f"rejection must stay below {BAR:.0f}, got {archetype['confidence']}"
    )


def test_rejection_must_actually_be_printed() -> None:
    """The same setup without a confirmed rejection scores lower."""
    confirmed = _classify(trigger="REJECTION_CONFIRMED")
    waiting = _classify(trigger="AT_POI_WAIT_TRIGGER")

    assert confirmed["confidence"] > waiting["confidence"], (
        "a confirmed rejection is the core evidence of a reversal and must "
        f"outrank waiting for one (got {confirmed['confidence']} vs "
        f"{waiting['confidence']})"
    )


def test_raid_grade_changes_the_score() -> None:
    """MODERATE and WEAK must not collapse to one number.

    STRONG is deliberately excluded: a strong raid against the prevailing leg
    from the matching extreme is captured earlier by REVERSAL_AFTER_SWEEP_DAY,
    which is a different branch with its own floor and cap. Comparing across
    the two would measure branch selection, not this floor.
    """
    moderate = _classify(confirmation="MODERATE")
    weak = _classify(confirmation="WEAK")

    assert moderate["name"] == weak["name"] == "LIQUIDITY_REVERSAL_DAY"
    assert moderate["confidence"] > weak["confidence"], (
        f"got MODERATE={moderate['confidence']}, WEAK={weak['confidence']}"
    )


def test_mid_range_reversal_is_weaker_than_an_extreme_one() -> None:
    """Reverting from equilibrium is a weaker claim than from premium."""
    premium = _classify(zone="PREMIUM")
    equilibrium = _classify(zone="EQUILIBRIUM")

    assert premium["confidence"] > equilibrium["confidence"]


def test_the_conviction_threshold_is_untouched() -> None:
    """The bar does not move. Only what can reach it does."""
    planner = SessionPlannerService({"symbol": "XAU/USD",
                                     "session_planner": {"enabled": True}})
    assert planner.archetype_medium_conviction == 60.0
    assert planner.archetype_high_conviction == 75.0


def test_branch_cap_still_applies() -> None:
    """An earned floor must not let the branch outrun its 88 ceiling.

    Uses a MODERATE raid so the case stays inside LIQUIDITY_REVERSAL_DAY; a
    STRONG one is claimed by REVERSAL_AFTER_SWEEP_DAY, whose cap is 90.
    """
    archetype = _classify(confirmation="MODERATE", dominance=100.0)
    assert archetype["name"] == "LIQUIDITY_REVERSAL_DAY"
    assert archetype["confidence"] <= 88


def test_dominance_still_lifts_the_score() -> None:
    """A dominant thesis outranks a bare one on the same evidence."""
    thin = _classify(dominance=0.0)
    dominant = _classify(dominance=80.0)
    assert dominant["confidence"] > thin["confidence"]


def test_evidence_free_fallback_is_unaffected() -> None:
    """STRUCTURE_BIAS_DAY must remain refused; this change is scoped."""
    archetype = SMCAgent(CONFIG)._day_archetype(
        direction="SELL",
        market_structure={"trend": "BEARISH", "structure_quality": "WEAK"},
        liquidity={"recent_sweep": {"occurred": False, "type": None}},
        zone="EQUILIBRIUM",
        setup_candidates=[{"setup_type": "SMC_CONTEXT",
                           "trigger_state": "AWAY_FROM_POI",
                           "thesis_dominance_score": 0}],
    )
    assert archetype["name"] in {"STRUCTURE_BIAS_DAY", "RANGE_TRAP_DAY"}
    assert archetype["confidence"] < BAR
