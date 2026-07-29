"""A sweep is a raid on a swing high, not on a 12-bar window maximum.

Background
----------
The uploaded 2026-07-29 chart shows a textbook turtle soup: price pokes above
the 4047.5 swing high, closes back beneath it, and falls 4040 -> 4023 -> 3996.

Feeding that chart to the real ``SMCAgent.analyze()`` produced:

    structure=RANGING  zone=EQUILIBRIUM  sweep=None/None
    archetype=None     conf=0            SELL candidates=0

No sweep. No archetype. No candidates. Every later fix -- earned authority,
map retirement, evidence-based floors -- sits *downstream* of this detector,
so none of them can fire when the raid itself is invisible.

The cause is the level ``_recent_sweep`` tests against::

    if high > level + tolerance and close < level

``level`` is the maximum high of the 12 candles immediately before. On this
chart the rally had already printed an equal-highs shelf at 4040 inside that
window, so:

    high  4050.0 > 4040 + 1.3   -> True
    close 4044.0 < 4040         -> False   (closes above the shelf)

A turtle soup is by definition a poke through a *swing high* followed by a
close back under **that** high. Whenever the swept swing is not also the
window maximum, the pattern cannot be seen.

``detect_swing_points`` already runs every cycle and ``market_structure`` is
built from its output -- but ``_recent_sweep`` never receives it and rebuilds
its own notion of a level from a fixed lookback. Correct information exists;
the consumer does not get it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.smc_agent import SMCAgent

CONFIG = {"symbol": "XAU/USD"}


def _mk(seq: list[tuple[float, float, float]]) -> list[dict]:
    """(high, low, close) triples -> 15m candles.

    The open is clamped inside the stated high/low rather than inherited
    verbatim from the previous close. An earlier version let a low previous
    close drag the open below the stated low, which silently widened the
    candle's range; ``close_position`` is measured against that range, so a
    faithful rejection candle was graded WEAK purely because the fixture had
    stretched it. The bar's own high/low must stay authoritative.
    """
    out: list[dict] = []
    for i, (high, low, close) in enumerate(seq):
        prev_close = out[-1]["close"] if out else (high + low) / 2
        opening = min(max(prev_close, low), high)
        out.append({
            "time": "2026-07-29T%02d:%02d:00+00:00" % (6 + i // 4, (i % 4) * 15),
            "open": round(opening, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": 1000,
        })
    return out


def _uploaded_chart(sweep: tuple[float, float, float]) -> list[dict]:
    """The 2026-07-29 leg: rally, equal-highs shelf at 4040, then the raid."""
    leg = [
        (4015, 4009, 4011), (4014, 4008, 4013), (4018, 4011, 4017),
        (4021, 4015, 4019), (4019, 4013, 4014), (4017, 4010, 4016),
        (4024, 4016, 4023), (4029, 4022, 4028), (4034, 4027, 4033),
        (4040, 4032, 4039), (4040, 4035, 4036), (4039, 4033, 4034),
        (4036, 4028, 4029), (4031, 4022, 4023), (4027, 4021, 4026),
        (4032, 4025, 4031), (4038, 4030, 4037), (4043, 4036, 4042),
        (4046, 4040, 4045), (4047, 4042, 4044),
    ]
    return _mk((leg * 4)[:72] + [sweep])


# The wick pierces 4047.5 and closes back under it -- the analyst's read.
TURTLE_SOUP = (4050.0, 4043.0, 4044.0)


# ── The regression this fix exists to prevent ──────────────────────────────

def test_uploaded_chart_produces_a_buy_side_sweep() -> None:
    """The raid above the swing high must be detected.

    Failure injection: removing swing levels from ``_recent_sweep``'s
    reference list restores ``sweep=None`` here.
    """
    result = SMCAgent(CONFIG).analyze({
        "symbol": "XAU/USD", "timeframe": "15m",
        "data": _uploaded_chart(TURTLE_SOUP),
    })
    sweep = (result.get("liquidity") or {}).get("recent_sweep") or {}

    assert sweep.get("occurred") is True, (
        "the wick above the swing high, closing back beneath it, is a "
        f"textbook liquidity raid and must be detected (got {sweep})"
    )
    assert sweep.get("type") == "buy_side"
    assert sweep.get("confirmation") in {"STRONG", "MODERATE", "WEAK"}


def test_sweep_level_is_the_swing_high_not_the_window_maximum() -> None:
    """The recorded level must be the swing that was actually raided."""
    result = SMCAgent(CONFIG).analyze({
        "symbol": "XAU/USD", "timeframe": "15m",
        "data": _uploaded_chart(TURTLE_SOUP),
    })
    sweep = (result.get("liquidity") or {}).get("recent_sweep") or {}
    level = float(sweep.get("level") or 0)

    assert 4044.0 <= level <= 4048.5, (
        "the swept level should be the ~4047 swing high the wick pierced, "
        f"not the 4040 equal-highs shelf (got {level})"
    )


def test_uploaded_chart_yields_sell_setup_candidates() -> None:
    """Detection is worthless unless it reaches the planner as candidates."""
    result = SMCAgent(CONFIG).analyze({
        "symbol": "XAU/USD", "timeframe": "15m",
        "data": _uploaded_chart(TURTLE_SOUP),
    })
    candidates = result.get("setup_candidates") or []
    sells = [c for c in candidates
             if str(c.get("direction") or "").upper() == "SELL"]

    assert sells, (
        "a confirmed buy-side raid in premium must produce at least one SELL "
        f"candidate; got {len(candidates)} candidates, none SELL"
    )


# ── Guards: the detector must not become trigger-happy ─────────────────────

def test_close_back_above_the_swing_is_not_a_sweep() -> None:
    """Piercing and holding above is a breakout, not a raid.

    This is the guard that keeps the change honest: if any poke above a swing
    now counts, the detector has been loosened rather than corrected.
    """
    breakout = (4050.0, 4043.0, 4049.5)   # closes above the ~4047 swing
    result = SMCAgent(CONFIG).analyze({
        "symbol": "XAU/USD", "timeframe": "15m",
        "data": _uploaded_chart(breakout),
    })
    sweep = (result.get("liquidity") or {}).get("recent_sweep") or {}

    if sweep.get("occurred"):
        assert sweep.get("type") != "buy_side", (
            "a candle closing above the swing it pierced is a breakout; it "
            "must not be recorded as a buy-side raid"
        )


def test_shallow_poke_inside_tolerance_is_not_a_sweep() -> None:
    """A wick that barely grazes the level is noise."""
    graze = (4047.6, 4044.0, 4045.0)   # a few cents over the swing
    result = SMCAgent(CONFIG).analyze({
        "symbol": "XAU/USD", "timeframe": "15m",
        "data": _uploaded_chart(graze),
    })
    sweep = (result.get("liquidity") or {}).get("recent_sweep") or {}

    assert not (sweep.get("occurred") and sweep.get("type") == "buy_side"), (
        "a poke inside the ATR tolerance must not register as a raid"
    )


def test_quiet_range_produces_no_sweep() -> None:
    """A market with no raid must report none."""
    quiet = _mk([(4020 + (i % 3), 4014 + (i % 3), 4017 + (i % 3))
                 for i in range(70)])
    result = SMCAgent(CONFIG).analyze({
        "symbol": "XAU/USD", "timeframe": "15m", "data": quiet,
    })
    sweep = (result.get("liquidity") or {}).get("recent_sweep") or {}

    assert not sweep.get("occurred"), (
        f"a quiet range must not manufacture a sweep (got {sweep})"
    )


def test_existing_window_based_sweep_still_detected() -> None:
    """Raids the old rule already caught must keep being caught."""
    rising = [(4000 + i * 2, 3994 + i * 2, 3998 + i * 2) for i in range(72)]
    top = rising[-1][0]
    raid = (top + 12.0, top - 6.0, top - 4.0)   # clears and closes back under
    result = SMCAgent(CONFIG).analyze({
        "symbol": "XAU/USD", "timeframe": "15m", "data": _mk(rising + [raid]),
    })
    sweep = (result.get("liquidity") or {}).get("recent_sweep") or {}

    assert sweep.get("occurred") is True
    assert sweep.get("type") == "buy_side"


def test_sell_side_swing_raid_is_symmetric() -> None:
    """The same correction must apply below the market."""
    leg = [
        (4045, 4039, 4041), (4046, 4040, 4042), (4042, 4034, 4035),
        (4038, 4030, 4031), (4034, 4026, 4027), (4030, 4024, 4029),
        (4033, 4027, 4032), (4036, 4030, 4031), (4032, 4025, 4026),
        (4028, 4020, 4021), (4024, 4020, 4023), (4026, 4021, 4025),
    ]
    series = (leg * 7)[:72]
    raid = (4026.0, 4012.0, 4025.0)   # pierces the ~4020 swing low, closes back over
    result = SMCAgent(CONFIG).analyze({
        "symbol": "XAU/USD", "timeframe": "15m", "data": _mk(series + [raid]),
    })
    sweep = (result.get("liquidity") or {}).get("recent_sweep") or {}

    assert sweep.get("occurred") is True
    assert sweep.get("type") == "sell_side"


# ── The reversal pool must stay narrow ─────────────────────────────────────

def _agent() -> SMCAgent:
    return SMCAgent(CONFIG)


def test_weak_raid_does_not_open_a_reversal_pool() -> None:
    """An unconfirmed raid is not evidence of a reversal.

    A WEAK grade means price poked the level and did *not* close back inside
    it. Opening a counter-trend pool on that would let any wick against the
    trend manufacture a thesis -- exactly the "quantity over quality" trade
    the operator refused.

    Failure injection: adding "WEAK" to the accepted confirmations makes this
    fail.
    """
    direction = _agent()._reversal_direction(
        market_structure={"trend": "BULLISH", "structure_quality": "STRONG"},
        liquidity={"recent_sweep": {"occurred": True, "type": "buy_side",
                                    "confirmation": "WEAK"}},
        zone="PREMIUM",
    )
    assert direction is None, (
        "a WEAK raid must not open a counter-trend pool (got %r)" % direction
    )


def test_reversal_pool_requires_the_matching_extreme() -> None:
    """A raid in mid-range is not a reversal location.

    Selling a buy-side raid only makes sense from premium; the same raid at
    equilibrium or in discount is continuation fuel, not a turn.

    Failure injection: dropping the zone condition makes this fail.
    """
    agent = _agent()
    for zone in ("EQUILIBRIUM", "DISCOUNT"):
        direction = agent._reversal_direction(
            market_structure={"trend": "BULLISH", "structure_quality": "STRONG"},
            liquidity={"recent_sweep": {"occurred": True, "type": "buy_side",
                                        "confirmation": "STRONG"}},
            zone=zone,
        )
        assert direction is None, (
            "a buy-side raid at %s must not open a SELL pool (got %r)"
            % (zone, direction)
        )

    assert agent._reversal_direction(
        market_structure={"trend": "BULLISH", "structure_quality": "STRONG"},
        liquidity={"recent_sweep": {"occurred": True, "type": "buy_side",
                                    "confirmation": "STRONG"}},
        zone="PREMIUM",
    ) == "SELL"


def test_no_raid_means_no_reversal_pool() -> None:
    """Absence of a raid cannot imply a reversal."""
    assert _agent()._reversal_direction(
        market_structure={"trend": "BULLISH", "structure_quality": "STRONG"},
        liquidity={"recent_sweep": {"occurred": False, "type": None}},
        zone="PREMIUM",
    ) is None


def test_reversal_pool_is_symmetric_below_the_market() -> None:
    """A sell-side raid in discount opens a BUY pool, on the same terms."""
    assert _agent()._reversal_direction(
        market_structure={"trend": "BEARISH", "structure_quality": "STRONG"},
        liquidity={"recent_sweep": {"occurred": True, "type": "sell_side",
                                    "confirmation": "STRONG"}},
        zone="DISCOUNT",
    ) == "BUY"


def test_raid_aligned_with_the_trend_opens_nothing_new() -> None:
    """A sell-side raid inside a bullish leg is continuation, not reversal."""
    assert _agent()._reversal_direction(
        market_structure={"trend": "BULLISH", "structure_quality": "STRONG"},
        liquidity={"recent_sweep": {"occurred": True, "type": "sell_side",
                                    "confirmation": "STRONG"}},
        zone="PREMIUM",
    ) is None
