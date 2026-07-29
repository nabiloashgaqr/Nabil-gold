"""Day-map authority must be earned from evidence, not self-declared.

Background
----------
On 2026-07-29 at 12:21 UTC the 5-agent consensus produced SELL at 87.3% with
zero qualified opposition -- the same call a manual analyst made when he sold
4040 and took +310 points. The system published a BUY instead and lost 198
points.

The consensus was not overruled by a better argument. It was overruled by a
stamp: the planner's main path wrote ``authority_state = "CONFIRMED"`` as a
string literal, derived from a single SMC candidate, and both
DirectionalAuthorityService and DayMapSanityService treat that stamp as
binding. Daily Bias (BEARISH 95%), Multi-Timeframe (BEARISH, 100% aligned)
and Technical (SELL 92%) were never consulted.

``_resolve_authority`` already exists and does the right thing: it counts four
independent sources and returns CONFIRMED / WEAK / CONFLICTED. It was only
ever called from the fallback path.

These tests pin the rule: a map may keep its direction, but it only keeps
veto power while independent evidence agrees with it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.directional_authority import DirectionalAuthorityService
from services.session_planner import SessionPlannerService


def _candidate(role: str, *, direction: str, entry_price: float,
               stop_loss: float, target_price: float) -> dict:
    return {
        "id": f"CAND::{role}",
        "state_key": f"STATE::{role}",
        "direction": direction,
        "setup_type": "STRUCTURE_CONTINUATION",
        "setup_state": "POI_MARKED",
        "selection_role": role,
        "selection_rank": 1 if role == "PRIMARY" else 2,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "target_liquidity": target_price,
        "poi_type": "order_block",
        "poi_zone": {"top": entry_price + 2.0, "bottom": entry_price - 2.0},
        "poi_low": entry_price - 2.0,
        "poi_high": entry_price + 2.0,
        "poi_quality_score": 78,
        "return_probability_score": 61,
        "thesis_dominance_score": 68,
        "trigger_state": "AT_POI_WAIT_TRIGGER",
        "trigger_score": 58,
        "trigger_ready": False,
        "expected_revisit_window": "NEAR",
        "displacement_score": 12.0,
        "quality_score": 78,
        "quality_grade": "B",
        "details": {"poi": {"mitigation_status": "FRESH"}},
    }


def _aligned_results() -> dict:
    """Every independent source agrees with the SELL candidate."""
    return {
        "symbol": "XAU/USD",
        "current_price": 4012.0,
        "session": {"trading_allowed": True, "allow_signals": True,
                    "current_session": "London + New York Afternoon",
                    "session_quality": "HIGH"},
        "news": {"can_trade": True, "market_status": "SAFE",
                 "macro_direction": {"bias": "BEARISH_GOLD", "confidence": 64}},
        "macro_fundamental": {"macro_direction": {"bias": "BEARISH_GOLD",
                                                  "confidence": 64}},
        "daily_bias": {"bias": "BEARISH", "confidence": 95},
        "technical": {"signal": "SELL", "confidence": 82},
        "classical": {"signal": "SELL", "confidence": 80},
        "price_action": {"signal": "WAIT", "confidence": 40},
        "multitimeframe": {"signal": "WAIT", "confidence": 35},
        "smc": {
            "signal": "SELL",
            "confidence": 84,
            "day_archetype": "CONTINUATION_AFTER_SWEEP_DAY",
            "day_archetype_confidence": 78,
            "day_archetype_reason": "bearish structure + buy-side sweep",
            "preferred_execution_family": "MITIGATION_LADDER",
            "zone": "PREMIUM",
            "dealing_range": {"high": 4048.0, "low": 3970.0,
                              "midpoint": 4009.0, "current_position_pct": 0.72},
            "market_structure": {"trend": "BEARISH",
                                 "structure_quality": "STRONG"},
            "liquidity": {
                "recent_sweep": {"occurred": True, "type": "buy_side",
                                 "reference_type": "session_high",
                                 "confirmation": "STRONG"},
                "previous_day_levels": {"high": 4046.0, "low": 3984.0},
                "session_liquidity": {"label": "London", "high": 4038.0,
                                      "low": 3992.0},
            },
            "setup_candidates": [
                _candidate("PRIMARY", direction="SELL", entry_price=4020.0,
                           stop_loss=4044.0, target_price=3965.0),
                _candidate("STANDBY", direction="SELL", entry_price=4009.0,
                           stop_loss=4030.0, target_price=3950.0),
            ],
        },
    }


def _contradicted_results() -> dict:
    """The 12:21 book: a BUY candidate while every source reads bearish."""
    results = _aligned_results()
    results["current_price"] = 4021.96
    results["daily_bias"] = {"bias": "BEARISH", "confidence": 95}
    results["news"]["macro_direction"] = {"bias": "BEARISH_GOLD",
                                          "confidence": 64}
    results["macro_fundamental"]["macro_direction"] = {"bias": "BEARISH_GOLD",
                                                       "confidence": 64}
    results["technical"] = {"signal": "SELL", "confidence": 92}
    results["price_action"] = {"signal": "SELL", "confidence": 79}
    results["multitimeframe"] = {"signal": "SELL", "confidence": 92}
    results["smc"]["market_structure"] = {"trend": "BEARISH",
                                          "structure_quality": "STRONG"}
    # A lone BUY candidate, exactly as the SMC map produced that cycle.
    results["smc"]["setup_candidates"] = [
        _candidate("PRIMARY", direction="BUY", entry_price=4028.77,
                   stop_loss=4013.77, target_price=4089.0),
    ]
    return results


def _service(tmp_path: Path) -> SessionPlannerService:
    service = SessionPlannerService({"symbol": "XAU/USD",
                                     "session_planner": {"enabled": True}})
    service.storage_path = tmp_path / "session_plans.json"
    return service


# ── The regression this fix exists to prevent ───────────────────────────────

def test_smc_cannot_break_an_authority_tie_in_its_own_favour() -> None:
    """The exact 12:21 mechanism, isolated.

    That cycle had one independent source (daily bias, BEARISH 95%) against
    one SMC-derived source (structure, BULLISH). ``_resolve_authority`` sees a
    1-1 tie and calls ``_market_objective`` to break it -- but that helper
    reads structure_trend, the sweep and the premium/discount zone, all of
    which come from SMC. The tie between SMC and a non-SMC source is therefore
    broken by SMC again, and the result is stamped CONFIRMED on a count of 1,
    below ``min_authority_alignment_count`` of 2.

    Failure injection: reinstating the unconditional objective tiebreak makes
    this fail with state=CONFIRMED, direction=BUY.
    """
    service = SessionPlannerService({"symbol": "XAU/USD",
                                     "session_planner": {"enabled": True}})

    authority = service._resolve_authority(
        daily_bias={"bias": "BEARISH", "confidence": 95},
        macro={"bias": "NEUTRAL", "confidence": 50},
        market_structure={"trend": "BULLISH", "structure_quality": "STRONG"},
        recent_sweep={"occurred": True, "type": "sell_side",
                      "confirmation": "STRONG"},
        zone_context="DISCOUNT",
        reversal_watch={},
    )

    assert not (
        str(authority["state"]).upper() == "CONFIRMED"
        and str(authority["direction"] or "").upper() == "BUY"
    ), (
        "an SMC-derived tiebreak must not hand SMC a CONFIRMED stamp over an "
        f"opposing daily bias (got {authority['state']} {authority['direction']}, "
        f"count={authority['count']}, sources={authority['sources']})"
    )


def test_confirmed_authority_requires_the_configured_source_count() -> None:
    """CONFIRMED on a count of 1 contradicts min_authority_alignment_count.

    The tiebreak branch returned CONFIRMED while reporting count=1, so the
    configured floor of 2 independent sources was silently bypassed.
    """
    service = SessionPlannerService({"symbol": "XAU/USD",
                                     "session_planner": {"enabled": True}})
    assert service.min_authority_alignment_count == 2

    authority = service._resolve_authority(
        daily_bias={"bias": "BEARISH", "confidence": 95},
        macro={"bias": "NEUTRAL", "confidence": 50},
        market_structure={"trend": "BULLISH", "structure_quality": "STRONG"},
        recent_sweep={"occurred": True, "type": "sell_side",
                      "confirmation": "STRONG"},
        zone_context="DISCOUNT",
        reversal_watch={},
    )

    if str(authority["state"]).upper() == "CONFIRMED":
        assert int(authority["count"]) >= service.min_authority_alignment_count, (
            f"CONFIRMED claimed on only {authority['count']} aligned source(s); "
            f"the configured floor is {service.min_authority_alignment_count}"
        )


def test_ready_map_contradicted_by_independent_sources_is_not_confirmed(
    tmp_path: Path,
) -> None:
    """End-to-end through the main path, on a plan that really is published.

    This drives the ``planner_source == "setup_candidates"`` branch -- the one
    that used to write ``"authority_state": "CONFIRMED"`` as a literal. The
    candidate stays SELL so the plan is built and published, while daily bias
    and macro both read bullish, so no independent evidence backs the stamp.

    Failure injection: restoring the hard-coded literal on that branch makes
    this fail with CONFIRMED. (An earlier version of this test used a fixture
    that was refused upstream for lacking reversal proof, so it never reached
    the stamp and passed no matter what the code did.)
    """
    results = _aligned_results()
    results["daily_bias"] = {"bias": "BULLISH", "confidence": 92}
    results["news"]["macro_direction"] = {"bias": "BULLISH_GOLD", "confidence": 70}
    results["macro_fundamental"]["macro_direction"] = {"bias": "BULLISH_GOLD",
                                                       "confidence": 70}

    plan = _service(tmp_path).build_plan(results)

    assert plan["plan_ready"] is True, (
        "this fixture must reach the main publishing path for the assertion "
        "below to mean anything"
    )
    assert plan["planner_source"] == "setup_candidates"
    assert plan["session_bias"] == "SELL"
    assert str(plan["authority_state"]).upper() == "WEAK", (
        "a SELL map opposed by daily bias and macro must not stamp itself "
        f"CONFIRMED (got {plan['authority_state']!r})"
    )
    assert "not backed by day-map authority" in str(plan["authority_reason"])


def test_map_contradicted_by_every_source_does_not_claim_confirmed_authority(
    tmp_path: Path,
) -> None:
    """The literal 12:21 book, which the planner refuses upstream.

    Kept as a boundary case: whatever the refusal reason, a contradicted map
    must never leave this function carrying a CONFIRMED stamp.
    """
    plan = _service(tmp_path).build_plan(_contradicted_results())

    assert str(plan.get("authority_state", "")).upper() != "CONFIRMED", (
        "a BUY map contradicted by daily bias, macro and structure must not "
        f"stamp itself CONFIRMED (got {plan.get('authority_state')!r})"
    )


def test_weak_authority_cannot_veto_the_opposite_consensus() -> None:
    """A map without earned authority must not overrule the live vote.

    This is the second half of the 12:21 failure: the stamp is what
    DirectionalAuthorityService reads before cancelling a signal.
    """
    service = DirectionalAuthorityService({"directional_authority":
                                           {"enabled": True}})
    decision = {
        "decision": "SELL",
        "confidence": 87.3,
        "symbol": "XAU/USD",
        "setup_context": {"setup_type": "STRUCTURE_CONTINUATION",
                          "trigger_state": "DETECTED",
                          "trigger_score": 0.0,
                          "sweep_side": "buy_side"},
    }

    confirmed = service.review(
        decision, {"authority_state": "CONFIRMED", "authority_direction": "BUY"}, []
    )
    assert confirmed["action"] == "BLOCK_OPPOSITE_LOCAL", (
        "a genuinely confirmed map should still hold the line"
    )

    weak = service.review(
        decision, {"authority_state": "WEAK", "authority_direction": "BUY"}, []
    )
    assert weak["action"] == "ALLOW", (
        "a map whose authority was never earned must not cancel an 87% "
        f"consensus (got {weak['action']!r})"
    )


# ── Guards: the fix must not loosen a map that genuinely agrees ─────────────

def test_map_backed_by_every_source_keeps_confirmed_authority(
    tmp_path: Path,
) -> None:
    """Evidence-backed maps must be unaffected: no silent loosening."""
    plan = _service(tmp_path).build_plan(_aligned_results())

    assert plan["plan_ready"] is True
    assert plan["session_bias"] == "SELL"
    assert str(plan["authority_state"]).upper() == "CONFIRMED", (
        "daily bias + macro + structure all read bearish behind a SELL map; "
        f"authority must remain CONFIRMED (got {plan['authority_state']!r})"
    )
    assert plan["authority_direction"] == "SELL"


def test_authority_reason_names_the_sources_it_counted(tmp_path: Path) -> None:
    """The stamp must be auditable, not an unexplained verdict."""
    plan = _service(tmp_path).build_plan(_aligned_results())
    reason = str(plan.get("authority_reason") or "").lower()
    assert reason, "a plan that claims authority must say what it rests on"
    assert any(src in reason for src in ("daily_bias", "macro", "structure")), (
        f"authority_reason should name its evidence, got {reason!r}"
    )
