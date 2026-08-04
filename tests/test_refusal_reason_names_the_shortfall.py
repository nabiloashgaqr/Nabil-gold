"""A refusal must be reported in the words of a refusal.

THE DEFECT
----------
``_resolve_authority`` builds its reason from the evidence that SUPPORTS the
direction: which sources agree, whether a sweep aligned, whether the
premium/discount zone backs the thesis. That is the right text when the
authority is CONFIRMED.

When the state comes back WEAK the same string is stored as ``plan_reason``
by ``_fallback_day_map``, so a refusal was being reported like a success::

    "SELL alignment from macro; premium map supports the thesis"

Nothing in that sentence explains why nothing was published. Read from the
outside it looks like the system contradicting itself -- macro agrees, the
zone agrees, and still no map.

MEASURED
--------
``analyze_plan_rejections --limit 300`` on 2026-08-04::

    116 refused SELL maps
     38  "SELL alignment from macro; premium map supports ..."
     12  "SELL alignment from macro; aligned liquidity swe..."
      1  "SELL alignment from macro"
    ---
     51 of 116 (44%) refusals described only what was PRESENT

The real cause in every one of those cases is arithmetic: ``count`` aligned
sources against ``min_authority_alignment_count`` (2). One source is not two.

THE FIX
-------
When the state is not CONFIRMED, the shortfall is appended to the reason:

    "... ; but authority WEAK — only 1 aligned source(s), needs 2
     (or 1 with both an aligned sweep and zone support)"

CONFIRMED reasons are untouched, so nothing that already read correctly
changes.

WHAT THIS IS NOT
----------------
No threshold moved. ``min_authority_alignment_count`` is still 2 and no map
that was refused becomes publishable. This changes the WORDS of a refusal,
not the decision -- the same class of fix as the activation card reporting
the fill instead of the plan.

FAULT INJECTION
---------------
Remove the ``if state != "CONFIRMED"`` block from ``_resolve_authority`` and
``test_a_weak_authority_says_what_is_missing`` fails: the reason comes back
reading like a success.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services.session_planner import SessionPlannerService  # noqa: E402
from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()
PLANNER = SessionPlannerService(CONFIG)
REQUIRED = int(
    (CONFIG.get("session_planner") or {}).get("min_authority_alignment_count", 2) or 2
)


def _authority(**over):
    base = dict(
        daily_bias={"bias": "NEUTRAL"},
        macro={"bias": "NEUTRAL"},
        market_structure={"trend": "RANGING"},
        recent_sweep={},
        zone_context="PREMIUM",
        reversal_watch={},
    )
    base.update(over)
    return PLANNER._resolve_authority(**base)


# ── the incident ────────────────────────────────────────────────────────────

def test_a_weak_authority_says_what_is_missing():
    """One aligned source must not be reported as if it were enough."""
    result = _authority(macro={"bias": "BEARISH_GOLD"})
    assert result["state"] == "WEAK", result
    reason = result["reason"]

    assert "WEAK" in reason, (
        f"a refusal reported in the words of a success: {reason!r}"
    )
    assert str(result["count"]) in reason, "the shortfall must be quantified"
    assert str(REQUIRED) in reason, "the bar that was missed must be named"


def test_the_supporting_evidence_is_still_reported():
    """Naming the shortfall must not hide why the direction was credible."""
    reason = _authority(macro={"bias": "BEARISH_GOLD"})["reason"]
    assert "SELL alignment from macro" in reason
    assert "premium map supports" in reason


def test_a_confirmed_authority_reads_exactly_as_before():
    """Nothing that already made sense may change."""
    result = _authority(
        macro={"bias": "BEARISH_GOLD"},
        market_structure={"trend": "BEARISH"},
    )
    assert result["state"] == "CONFIRMED"
    assert "WEAK" not in result["reason"], result["reason"]
    assert "needs" not in result["reason"], result["reason"]


@pytest.mark.parametrize(
    "bias, structure, expected_state",
    [
        ("BEARISH_GOLD", "RANGING", "WEAK"),        # 1 source
        ("BEARISH_GOLD", "BEARISH", "CONFIRMED"),   # 2 sources
        ("BULLISH_GOLD", "RANGING", "WEAK"),        # 1 source, other side
        ("NEUTRAL", "RANGING", "WEAK"),             # none
    ],
)
def test_every_weak_state_explains_itself(bias, structure, expected_state):
    result = _authority(
        macro={"bias": bias}, market_structure={"trend": structure}
    )
    assert result["state"] == expected_state
    if expected_state == "WEAK":
        reason = result["reason"]
        explained = "WEAK" in reason or "no directional authority sources" in reason
        assert explained, f"unexplained refusal: {reason!r}"


def test_no_direction_at_all_is_still_reported_plainly():
    result = _authority()
    assert result["direction"] is None
    assert "no directional authority sources" in result["reason"]


# ── the threshold itself must not have moved ────────────────────────────────

def test_no_threshold_was_changed():
    planner = CONFIG.get("session_planner") or {}
    assert int(planner.get("min_authority_alignment_count", 2)) == 2
    assert float(planner.get("min_primary_quality_score", 70)) == 70
    conviction = planner.get("archetype_conviction") or {}
    assert float(conviction.get("medium_conviction_confidence", 60)) == 60
    assert float(conviction.get("high_conviction_confidence", 75)) == 75


def test_a_refused_map_is_still_refused():
    """This is a wording fix; it must not publish anything new."""
    weak = _authority(macro={"bias": "BEARISH_GOLD"})
    assert weak["state"] != "CONFIRMED"


# ── the report reads these strings, so they must stay groupable ─────────────

def test_the_reason_still_starts_with_the_direction():
    """analyze_plan_rejections truncates to 48 chars when grouping."""
    reason = _authority(macro={"bias": "BEARISH_GOLD"})["reason"]
    assert reason.startswith("SELL alignment from"), reason


def test_the_shortfall_survives_the_report_truncation():
    """The grouping key is the first 48 characters; the detail must not be
    the only place the cause appears, or the report will still be opaque.

    This is why the shortfall is appended rather than prefixed: the family
    label stays stable, and the full reason carries the cause for anyone
    reading a single row.
    """
    reason = _authority(macro={"bias": "BEARISH_GOLD"})["reason"]
    assert len(reason) > 48
    assert "WEAK" in reason[48:], (
        "the cause must be present in the stored reason even though the "
        "report groups on the prefix"
    )
