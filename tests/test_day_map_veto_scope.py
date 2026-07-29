"""A day map governs its own direction; it does not govern silence.

Background
----------
DayMapSanityService exists for a good reason: a small local execution zone
should not bypass a stronger planner view. That intent is sound and these
tests keep it intact.

But the gate was applying that intent in two situations it was never designed
for, and together they refused every trade on 2026-07-29:

  1. ``block_when_plan_not_ready`` refuses *any* directional signal while no
     map is ready. Over the user's own 300-cycle sample, 294 cycles (98%) had
     no ready map -- almost always because the planner refused its own map on
     archetype conviction, not because the market was unreadable. A consensus
     of 87.3% with zero opposition was therefore blocked by the absence of a
     second opinion rather than by any disagreement with one.

  2. A map whose authority is WEAK still blocked opposing execution through
     the zone check, even after the authority fix stopped it vetoing through
     DirectionalAuthorityService.

The rule these tests pin: a map blocks a trade when it disagrees with it on
evidence. A map that does not exist, or that never earned authority, has
nothing to disagree with.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.day_map_sanity import DayMapSanityService


CONFIG = {
    "symbol": "XAU/USD",
    "day_map_sanity": {
        "enabled": True,
        "block_when_plan_not_ready": True,
        "entry_zone_tolerance_points": 40,
        "require_planner_execution_for_extreme_poi": True,
    },
}


def _sell_decision() -> dict:
    """The 12:21 consensus: SELL 87.3%, three qualified agents, no dissent."""
    return {
        "decision": "SELL",
        "confidence": 87.3,
        "symbol": "XAU/USD",
        "current_price": 4021.96,
        "signal": {"order_type": "SELL_MARKET", "entry": {"price": 4021.96}},
    }


# ── The two refusals that cost the session ─────────────────────────────────

def test_absent_map_does_not_block_a_clean_consensus() -> None:
    """98% of cycles had no map. Silence is not disagreement.

    Failure injection: restoring the unconditional
    ``block_when_plan_not_ready`` refusal makes this fail with
    BLOCK_NO_DAY_MAP.
    """
    review = DayMapSanityService(CONFIG).review(
        _sell_decision(),
        {"plan_ready": False, "plan_reason": "archetype conviction is LOW"},
    )

    assert review["action"] == "ALLOW", (
        "a missing day map is an absence of opinion, not a veto; an 87.3% "
        f"consensus must not be refused for it (got {review['action']}: "
        f"{review['reason']})"
    )


def test_weak_authority_map_does_not_block_the_opposite_side() -> None:
    """A map that never earned authority cannot police direction."""
    review = DayMapSanityService(CONFIG).review(
        _sell_decision(),
        {
            "plan_ready": True,
            "authority_state": "WEAK",
            "authority_direction": "BUY",
        },
    )

    assert review["action"] == "ALLOW", (
        "a WEAK map lost its veto at the authority layer and must not "
        f"reimpose it through the zone check (got {review['action']})"
    )


# ── Guards: everything the gate was actually built to stop ─────────────────

def test_confirmed_opposing_map_still_blocks() -> None:
    """The core protection is untouched: earned authority still wins."""
    review = DayMapSanityService(CONFIG).review(
        _sell_decision(),
        {
            "plan_ready": True,
            "authority_state": "CONFIRMED",
            "authority_direction": "BUY",
        },
    )

    assert review["action"] == "BLOCK_DIRECTION_MISMATCH", (
        "a genuinely confirmed BUY map must still refuse a SELL execution"
    )


def test_confirmed_aligned_map_still_enforces_its_zones() -> None:
    """Zone discipline survives for maps that hold real authority."""
    review = DayMapSanityService(CONFIG).review(
        _sell_decision(),
        {
            "plan_ready": True,
            "authority_state": "CONFIRMED",
            "authority_direction": "SELL",
            "primary_entry_zone": {"low": 4060.0, "high": 4064.0},
        },
    )

    assert review["action"] == "BLOCK_ENTRY_OUTSIDE_DAY_MAP", (
        "a confirmed map must still keep execution inside its mapped zones"
    )


def test_extreme_poi_still_requires_planner_led_execution() -> None:
    """The EXTREME_POI bypass guard is unaffected."""
    review = DayMapSanityService(CONFIG).review(
        _sell_decision(),
        {
            "plan_ready": True,
            "authority_state": "CONFIRMED",
            "authority_direction": "SELL",
            "poi_classification": "EXTREME_POI",
            "primary_entry_zone": {"low": 4020.0, "high": 4024.0},
        },
    )

    assert review["action"] == "BLOCK_EXTREME_POI_BYPASS", (
        "extreme POIs must still be executed by the planner, not locally"
    )


def test_block_when_plan_not_ready_remains_configurable() -> None:
    """Operators who want the strict behaviour can still have it."""
    strict = {
        "symbol": "XAU/USD",
        "day_map_sanity": {
            "enabled": True,
            "block_when_plan_not_ready": True,
            "require_day_map_for_all_entries": True,
        },
    }
    review = DayMapSanityService(strict).review(
        _sell_decision(), {"plan_ready": False}
    )

    assert review["action"] == "BLOCK_NO_DAY_MAP", (
        "the strict opt-in must still be honoured when explicitly requested"
    )
