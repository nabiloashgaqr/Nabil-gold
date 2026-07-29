"""An archetype's confidence floor must reflect the evidence it required.

Background
----------
``archetype conviction is LOW`` is the single largest refusal reason in the
operator's 300-cycle sample: 99 of 300 cycles (33%). The obvious response --
lowering ``medium_conviction_confidence`` from 60 -- was explicitly refused,
and rightly: that buys quantity by discarding quality.

The real defect is that the floors are arbitrary constants that do not track
the evidence each branch demanded:

    FAILED_RECLAIM_DAY            1 condition   floor 60   -> MEDIUM
    CONTINUATION_AFTER_SWEEP_DAY  3 conditions  floor 58   -> LOW
    LIQUIDITY_REVERSAL_DAY        1 condition   floor 55   -> LOW
    STRUCTURE_BIAS_DAY            0 conditions  floor 50   -> LOW

A branch requiring three agreeing facts scores *below* one requiring a single
trigger, so it is refused at its own floor whenever thesis dominance is weak.

Worse, the two continuation branches never read ``sweep.confirmation`` at all:
a STRONG sweep that closed back inside the level and a WEAK one that merely
poked through both produce the identical number 58.

The fix keeps ``medium_conviction_confidence`` at 60 untouched. It makes the
floor a function of the evidence actually confirmed, so a well-evidenced day
can reach the bar on merit while a thin one still cannot.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.smc_agent import SMCAgent
from services.session_planner import SessionPlannerService


CONFIG = {"symbol": "XAU/USD"}
MEDIUM_BAR = 60.0  # session_planner.archetype_conviction.medium_conviction_confidence


def _agent() -> SMCAgent:
    return SMCAgent(CONFIG)


def _sweep(confirmation: str, side: str = "buy_side") -> dict:
    return {"occurred": True, "type": side, "confirmation": confirmation,
            "reference_type": "session_high"}


def _candidate(setup_type: str = "ORDER_BLOCK_PULLBACK", dominance: float = 0.0) -> dict:
    return {"setup_type": setup_type, "trigger_state": "AT_POI_WAIT_TRIGGER",
            "thesis_dominance_score": dominance}


def _classify(agent: SMCAgent, *, trend: str, sweep: dict, zone: str,
              quality: str = "STRONG", candidates: list | None = None) -> dict:
    return agent._day_archetype(
        direction="SELL" if trend == "BEARISH" else "BUY",
        market_structure={"trend": trend, "structure_quality": quality},
        liquidity={"recent_sweep": sweep},
        zone=zone,
        setup_candidates=candidates if candidates is not None else [_candidate()],
    )


# ── The refusal that costs the most cycles ─────────────────────────────────

def test_strong_evidence_continuation_can_reach_the_conviction_bar() -> None:
    """Bearish structure + STRONG buy-side sweep + premium: three facts agree.

    This is a textbook continuation-after-sweep day. It must be able to clear
    the 60 bar on its own evidence, without the bar moving.

    Failure injection: restoring the flat ``max(58.0, top_conf)`` floor makes
    this fail at 58.
    """
    archetype = _classify(_agent(), trend="BEARISH",
                          sweep=_sweep("STRONG"), zone="PREMIUM")

    assert archetype["name"] == "CONTINUATION_AFTER_SWEEP_DAY"
    assert archetype["confidence"] >= MEDIUM_BAR, (
        "a continuation day with a STRONG confirmed sweep, aligned structure "
        f"and matching zone must reach {MEDIUM_BAR:.0f}, got "
        f"{archetype['confidence']}"
    )


def test_weak_evidence_continuation_still_falls_short() -> None:
    """The same shape with a WEAK sweep must remain below the bar.

    This is the guard that keeps the change honest: if every continuation day
    now clears 60, the floor has simply been lowered by another name.
    """
    archetype = _classify(_agent(), trend="BEARISH",
                          sweep=_sweep("WEAK"), zone="PREMIUM",
                          quality="WEAK")

    assert archetype["name"] == "CONTINUATION_AFTER_SWEEP_DAY"
    assert archetype["confidence"] < MEDIUM_BAR, (
        "a continuation day resting on an unconfirmed sweep and weak "
        f"structure must stay below {MEDIUM_BAR:.0f}, got "
        f"{archetype['confidence']}"
    )


def test_sweep_confirmation_changes_the_score() -> None:
    """STRONG and WEAK sweeps must not produce the same number.

    The continuation branches ignored ``sweep.confirmation`` entirely, so the
    quality of the sweep -- the fact the whole thesis rests on -- had no
    influence on conviction.
    """
    agent = _agent()
    strong = _classify(agent, trend="BEARISH", sweep=_sweep("STRONG"), zone="PREMIUM")
    moderate = _classify(agent, trend="BEARISH", sweep=_sweep("MODERATE"), zone="PREMIUM")
    weak = _classify(agent, trend="BEARISH", sweep=_sweep("WEAK"), zone="PREMIUM")

    assert strong["confidence"] > moderate["confidence"] > weak["confidence"], (
        "sweep confirmation must be reflected in conviction: got "
        f"STRONG={strong['confidence']}, MODERATE={moderate['confidence']}, "
        f"WEAK={weak['confidence']}"
    )


def test_structure_quality_is_reflected_too() -> None:
    """A thesis on strong structure outranks the same thesis on weak."""
    agent = _agent()
    strong = _classify(agent, trend="BEARISH", sweep=_sweep("MODERATE"),
                       zone="PREMIUM", quality="STRONG")
    weak = _classify(agent, trend="BEARISH", sweep=_sweep("MODERATE"),
                     zone="PREMIUM", quality="WEAK")

    assert strong["confidence"] > weak["confidence"]


# ── Guards: the bar itself must not move ───────────────────────────────────

def test_the_conviction_threshold_is_untouched() -> None:
    """medium_conviction_confidence stays at 60. The floors move, not the bar."""
    planner = SessionPlannerService({"symbol": "XAU/USD",
                                     "session_planner": {"enabled": True}})
    assert planner.archetype_medium_conviction == 60.0
    assert planner.archetype_high_conviction == 75.0


def test_evidence_free_fallback_stays_refused() -> None:
    """STRUCTURE_BIAS_DAY has no confirmed evidence and must remain LOW.

    It is the branch reached when nothing cleaner matched. If this ever clears
    60, the archetype layer has stopped filtering anything.
    """
    archetype = _classify(
        _agent(), trend="BEARISH",
        sweep={"occurred": False, "type": None}, zone="EQUILIBRIUM",
        quality="WEAK", candidates=[_candidate(setup_type="SMC_CONTEXT")],
    )

    assert archetype["name"] in {"STRUCTURE_BIAS_DAY", "RANGE_TRAP_DAY"}
    assert archetype["confidence"] < MEDIUM_BAR, (
        "the no-evidence fallback must never reach the conviction bar"
    )


def test_range_day_without_evidence_stays_refused() -> None:
    """A ranging market with no sweep is the weakest possible read."""
    archetype = _classify(
        _agent(), trend="RANGING",
        sweep={"occurred": False, "type": None}, zone="EQUILIBRIUM",
        quality="WEAK", candidates=[_candidate(setup_type="SMC_CONTEXT")],
    )

    assert archetype["name"] == "RANGE_TRAP_DAY"
    assert archetype["confidence"] < MEDIUM_BAR


def test_dominance_still_lifts_conviction_above_the_floor() -> None:
    """A dominant thesis must still raise the score above its own floor."""
    agent = _agent()
    thin = _classify(agent, trend="BEARISH", sweep=_sweep("STRONG"), zone="PREMIUM",
                     candidates=[_candidate(dominance=0.0)])
    dominant = _classify(agent, trend="BEARISH", sweep=_sweep("STRONG"), zone="PREMIUM",
                         candidates=[_candidate(dominance=85.0)])

    assert dominant["confidence"] > thin["confidence"]
    assert dominant["confidence"] <= 90, "the branch cap must still apply"


def test_confidence_never_exceeds_the_branch_cap() -> None:
    """Earned floors must not let a branch outrun its ceiling."""
    agent = _agent()
    for trend, sweep, zone, cap in (
        ("BEARISH", _sweep("STRONG"), "PREMIUM", 90),
        ("BULLISH", _sweep("STRONG", "sell_side"), "DISCOUNT", 90),
    ):
        archetype = _classify(agent, trend=trend, sweep=sweep, zone=zone,
                              candidates=[_candidate(dominance=100.0)])
        assert archetype["confidence"] <= cap


def test_reversal_after_sweep_floor_is_unchanged() -> None:
    """The reversal branch already cleared the bar; it must not regress."""
    archetype = _classify(
        _agent(), trend="BULLISH", sweep=_sweep("STRONG", "buy_side"),
        zone="PREMIUM", quality="STRONG",
        candidates=[_candidate(setup_type="LIQUIDITY_REVERSAL")],
    )

    assert archetype["name"] == "REVERSAL_AFTER_SWEEP_DAY"
    assert archetype["confidence"] >= 66
