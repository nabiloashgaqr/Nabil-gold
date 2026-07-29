"""A day map must be falsifiable by the live book it claims to describe.

Background
----------
Phase A stopped a map from *inventing* authority: a stamp is now derived from
independent evidence instead of asserted by the path that built the candidate.

That fixed maps that never earned authority. It did nothing for maps that did
earn it and then went stale, because the veto is structurally one-directional:

    DirectionalAuthorityService.review(decision, session_plan, open_trades)

There is no agents parameter. The five voting agents cannot reach this
function, so no amount of live disagreement can retire a map. A map confirmed
at 02:00 by daily bias and structure still owns the symbol at 14:00 while
three qualified agents read the other way, and the only escape hatch -- the
regime-flip branch -- demands a reversal-grade setup with REJECTION_CONFIRMED
and an aligned fresh sweep. A plain continuation move in the opposite
direction can never satisfy it, however strong.

The market is allowed to prove a thesis wrong. These tests pin that rule:
when a decisive, qualified majority of live agents opposes a mapped
direction, the map yields. The bar is deliberately high -- this is a
retirement clause, not a mood swing.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.directional_authority import DirectionalAuthorityService


CONFIG = {
    "symbol": "XAU/USD",
    "directional_authority": {
        "enabled": True,
        "min_confidence_for_flip": 88,
        "min_trigger_score_for_flip": 70,
        "require_reversal_setup_for_flip": True,
        "require_rejection_confirmed_for_flip": True,
        "require_fresh_sweep_for_flip": True,
    },
    "signal_requirements": {"agent_min_confidence": 70},
    "session_planner": {"max_opposing_agents_for_ready": 1},
}

CONFIRMED_BUY_MAP = {
    "plan_ready": True,
    "authority_state": "CONFIRMED",
    "authority_direction": "BUY",
}


def _book(**agents) -> dict:
    """Build an agent_details payload: name -> (direction, confidence)."""
    return {
        name: {"direction": direction, "confidence": confidence}
        for name, (direction, confidence) in agents.items()
    }


DECISIVE_SELL_BOOK = _book(
    technical=("SELL", 92),
    price_action=("SELL", 79),
    multitimeframe=("SELL", 92),
    classical=("WAIT", 30),
    smc=("WAIT", 31),
)


def _sell_decision(agent_details: dict | None = None) -> dict:
    """A continuation SELL: the shape that can never satisfy the flip branch."""
    decision = {
        "decision": "SELL",
        "confidence": 91.0,
        "symbol": "XAU/USD",
        "setup_context": {
            "setup_type": "STRUCTURE_CONTINUATION",
            "trigger_state": "DETECTED",
            "trigger_score": 0.0,
            "sweep_side": "buy_side",
        },
    }
    if agent_details is not None:
        decision["agent_details"] = agent_details
    return decision


# ── The asymmetry this phase exists to remove ──────────────────────────────

def test_decisive_live_majority_retires_a_stale_map() -> None:
    """Three qualified agents against the map, none for it: the map yields.

    Failure injection: removing the live-book branch from
    DirectionalAuthorityService restores BLOCK_OPPOSITE_LOCAL here.
    """
    review = DirectionalAuthorityService(CONFIG).review(
        _sell_decision(DECISIVE_SELL_BOOK), CONFIRMED_BUY_MAP, []
    )

    assert review["action"] != "BLOCK_OPPOSITE_LOCAL", (
        "a confirmed BUY map opposed by 3 qualified agents and supported by "
        f"none must not keep vetoing (got {review['action']}: {review['reason']})"
    )
    assert review["action"] == "ALLOW_MAP_RETIRED"
    assert "3" in review["reason"]


def test_retirement_is_reported_for_the_audit_trail() -> None:
    """A map being overruled is a significant event; it must be legible."""
    review = DirectionalAuthorityService(CONFIG).review(
        _sell_decision(DECISIVE_SELL_BOOK), CONFIRMED_BUY_MAP, []
    )

    assert review.get("authority_direction") == "BUY"
    assert review.get("signal_direction") == "SELL"
    assert review.get("opposing_agents"), "the dissenting agents must be named"
    assert set(review["opposing_agents"]) == {
        "technical", "price_action", "multitimeframe",
    }


# ── Guards: retirement must be hard to trigger ─────────────────────────────

def test_map_survives_a_thin_majority() -> None:
    """Two agents is disagreement, not a verdict. The map holds."""
    thin = _book(
        technical=("SELL", 92),
        price_action=("SELL", 79),
        multitimeframe=("WAIT", 40),
        classical=("WAIT", 30),
        smc=("WAIT", 31),
    )
    review = DirectionalAuthorityService(CONFIG).review(
        _sell_decision(thin), CONFIRMED_BUY_MAP, []
    )

    assert review["action"] == "BLOCK_OPPOSITE_LOCAL", (
        "two opposing agents must not be enough to retire a confirmed map"
    )


def test_map_survives_when_the_book_is_split() -> None:
    """If any qualified agent still backs the map, it is not abandoned."""
    split = _book(
        technical=("SELL", 92),
        price_action=("SELL", 79),
        multitimeframe=("SELL", 92),
        classical=("BUY", 74),   # still defending the map
        smc=("WAIT", 31),
    )
    review = DirectionalAuthorityService(CONFIG).review(
        _sell_decision(split), CONFIRMED_BUY_MAP, []
    )

    assert review["action"] == "BLOCK_OPPOSITE_LOCAL", (
        "a map with live support must not be retired, even when outnumbered"
    )


def test_unqualified_dissent_does_not_count() -> None:
    """Agents below agent_min_confidence have no vote here either."""
    weak = _book(
        technical=("SELL", 69),
        price_action=("SELL", 68),
        multitimeframe=("SELL", 65),
        classical=("WAIT", 30),
        smc=("WAIT", 31),
    )
    review = DirectionalAuthorityService(CONFIG).review(
        _sell_decision(weak), CONFIRMED_BUY_MAP, []
    )

    assert review["action"] == "BLOCK_OPPOSITE_LOCAL", (
        "sub-threshold agents must not be able to retire a map"
    )


def test_missing_agent_book_leaves_the_veto_untouched() -> None:
    """No book means no evidence of dissent: the old behaviour stands."""
    review = DirectionalAuthorityService(CONFIG).review(
        _sell_decision(), CONFIRMED_BUY_MAP, []
    )

    assert review["action"] == "BLOCK_OPPOSITE_LOCAL", (
        "absence of an agent book must not be read as unanimous dissent"
    )


def test_aligned_signal_is_still_allowed() -> None:
    """Nothing about agreement changes."""
    review = DirectionalAuthorityService(CONFIG).review(
        {"decision": "BUY", "confidence": 80, "symbol": "XAU/USD",
         "agent_details": DECISIVE_SELL_BOOK},
        CONFIRMED_BUY_MAP, [],
    )
    assert review["action"] == "ALLOW"


def test_high_authority_regime_flip_still_works() -> None:
    """The existing reversal escape hatch is untouched."""
    review = DirectionalAuthorityService(CONFIG).review(
        {
            "decision": "SELL",
            "confidence": 90,
            "symbol": "XAU/USD",
            "setup_context": {
                "setup_type": "LIQUIDITY_REVERSAL",
                "trigger_state": "REJECTION_CONFIRMED",
                "trigger_score": 75,
                "sweep_side": "buy_side",
            },
        },
        CONFIRMED_BUY_MAP, [],
    )
    assert review["action"] == "ALLOW_REGIME_FLIP"


def test_retirement_can_be_disabled() -> None:
    """Operators keep the old, map-supreme behaviour if they want it."""
    config = {
        **CONFIG,
        "directional_authority": {
            **CONFIG["directional_authority"],
            "allow_live_book_retirement": False,
        },
    }
    review = DirectionalAuthorityService(config).review(
        _sell_decision(DECISIVE_SELL_BOOK), CONFIRMED_BUY_MAP, []
    )
    assert review["action"] == "BLOCK_OPPOSITE_LOCAL"


def test_live_trades_on_the_map_are_not_abandoned_by_a_vote() -> None:
    """Money already committed to the map is not reversed on a vote.

    Retiring a map while trades are open on it would admit a signal against
    live positions. Those trades have their own managed exits; this gate
    admits new signals, so it must not become a back-door reversal.
    """
    open_trades = [{"status": "OPEN", "type": "BUY", "symbol": "XAU/USD"}]
    review = DirectionalAuthorityService(CONFIG).review(
        _sell_decision(DECISIVE_SELL_BOOK), CONFIRMED_BUY_MAP, open_trades
    )

    assert review["action"] == "BLOCK_OPPOSITE_LOCAL", (
        "a map with live trades on it must not be retired by a vote; the "
        "open position is managed by its own stop and targets"
    )


# ── The retirement must actually propagate, not just be announced ──────────

def test_retired_map_stops_blocking_at_the_next_gate() -> None:
    """Retirement is worthless if the next gate re-reads the old stamp.

    DayMapSanityService independently refuses a signal whose side disagrees
    with a CONFIRMED map. If the authority layer retires a map but leaves
    ``authority_state`` reading CONFIRMED in the plan dict, the very next gate
    blocks the same signal for the same reason and the retirement is
    cosmetic. The caller must mark the plan so the decision is honoured
    end-to-end.

    Failure injection: dropping ``apply_retirement`` (or its call site in
    run_analysis) restores BLOCK_DIRECTION_MISMATCH here.
    """
    from services.day_map_sanity import DayMapSanityService

    service = DirectionalAuthorityService(CONFIG)
    plan = dict(CONFIRMED_BUY_MAP)
    review = service.review(_sell_decision(DECISIVE_SELL_BOOK), plan, [])
    assert review["action"] == "ALLOW_MAP_RETIRED"

    service.apply_retirement(plan, review)

    sanity = DayMapSanityService(
        {"symbol": "XAU/USD",
         "day_map_sanity": {"enabled": True, "block_when_plan_not_ready": True}}
    ).review(
        {"decision": "SELL", "confidence": 91.0, "symbol": "XAU/USD",
         "current_price": 4021.96,
         "signal": {"order_type": "SELL_MARKET", "entry": {"price": 4021.96}}},
        plan,
    )

    assert sanity["action"] == "ALLOW", (
        "a map retired by the authority layer must not keep blocking through "
        f"the day-map sanity gate (got {sanity['action']}: {sanity['reason']})"
    )


def test_apply_retirement_records_why_the_map_was_retired() -> None:
    """The plan must carry its own history for the audit trail."""
    service = DirectionalAuthorityService(CONFIG)
    plan = dict(CONFIRMED_BUY_MAP)
    review = service.review(_sell_decision(DECISIVE_SELL_BOOK), plan, [])
    service.apply_retirement(plan, review)

    assert plan["authority_state"] == "RETIRED"
    assert plan["authority_retired_by_live_book"] is True
    assert plan["authority_direction_before_retirement"] == "BUY"
    assert "qualified agents" in str(plan["authority_reason"])


def test_apply_retirement_ignores_other_verdicts() -> None:
    """Only a retirement verdict may alter the plan."""
    service = DirectionalAuthorityService(CONFIG)
    plan = dict(CONFIRMED_BUY_MAP)
    service.apply_retirement(plan, {"action": "BLOCK_OPPOSITE_LOCAL"})
    assert plan["authority_state"] == "CONFIRMED"
