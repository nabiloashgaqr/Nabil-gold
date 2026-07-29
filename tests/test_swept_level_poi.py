"""The level a raid took is itself a place to trade from.

Background
----------
Phase G opened a SELL pool on the 2026-07-29 turtle-soup chart, and the agent
correctly detected the raid on 4047.0. But the entry it produced was 4027.50,
135 points below the order block the analyst drew at 4041 -- and nowhere near
the 4047 level price had just rejected.

The reason is that no bearish POI existed to trade from:

    order blocks : bullish 4013-4019 only
    SELL POIs    : equilibrium 4025.82-4029.18   <- range midpoint, a fallback

``_detect_order_blocks`` scans ``range(2, len(candles) - 4)``, so the last four
candles are never examined. The turtle-soup candle *is* the last one, so the
bearish block it creates is structurally invisible at the moment it matters.
Replaying with the decline appended shows the block does appear later -- at
4043-4050, by which time price is 4019 and the entry is 240 points away.
The map gets built after the move instead of before it.

The fix does not touch the order-block detector or lower any threshold. The
swept level is already known, already graded, and needs no further
confirmation: ``recent_sweep`` carries ``level``, ``confirmation`` and
``reference_type`` every cycle. Publishing it as a point of interest simply
lets the planner trade the level the market just rejected -- which is what the
manual analyst did.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.smc_agent import SMCAgent
from utils.indicators import calculate_atr, detect_swing_points

CONFIG = {"symbol": "XAU/USD"}


def _chart(sweep=(4050.0, 4043.0, 4044.0)):
    from test_swing_sweep_detection import _uploaded_chart
    return _uploaded_chart(sweep)


def _context(candles):
    agent = SMCAgent(CONFIG)
    atr = agent._last(calculate_atr(candles, 14), 1.5)
    swings = detect_swing_points(candles, lookback=3)
    tolerance = max(atr * 0.15, 0.60)
    liquidity = agent._detect_liquidity(candles, swings, tolerance)
    order_blocks = agent._detect_order_blocks(candles, atr, "15m")
    fvg = agent._detect_fvg(candles)
    price = float(candles[-1]["close"])
    zone, dealing_range = agent._premium_discount_zone(candles, swings, price)
    return agent, atr, liquidity, order_blocks, fvg, price, dealing_range


# ── The gap this closes ────────────────────────────────────────────────────

def test_swept_level_becomes_a_sell_poi() -> None:
    """The raided 4047 level must be offered as somewhere to sell from.

    Failure injection: removing the swept-level POI leaves only the
    equilibrium fallback and this fails.
    """
    candles = _chart()
    agent, atr, liquidity, obs, fvg, price, dr = _context(candles)

    pois = agent._poi_candidates("SELL", price, atr, obs, fvg, dr,
                                 liquidity=liquidity)
    kinds = [p["poi_type"] for p in pois]

    assert "swept_level" in kinds, (
        f"the raided level must be tradable; got POIs {kinds}"
    )
    swept = next(p for p in pois if p["poi_type"] == "swept_level")
    top = float(swept["zone"]["top"])
    bottom = float(swept["zone"]["bottom"])
    assert bottom < 4047.0 <= top or bottom <= 4047.0 < top, (
        f"the zone must contain the swept level 4047.0, got {bottom}-{top}"
    )


def test_entry_is_near_the_rejected_level_not_mid_range() -> None:
    """The candidate must sell into the rejection, not 135 points below it."""
    candles = _chart()
    result = SMCAgent(CONFIG).analyze(
        {"symbol": "XAU/USD", "timeframe": "15m", "data": candles})
    sells = [c for c in (result.get("setup_candidates") or [])
             if str(c.get("direction") or "").upper() == "SELL"]

    assert sells, "Phase G should already provide a SELL candidate"
    best = max(sells, key=lambda c: float(c.get("entry_price") or 0))
    entry = float(best.get("entry_price") or 0)

    assert entry >= 4040.0, (
        "the mapped entry should sit at the rejected level, near the "
        f"analyst's 4041 order block, not at the range midpoint (got {entry})"
    )


def test_swept_level_stop_sits_beyond_the_raid_high() -> None:
    """Invalidation must be above the wick, or the thesis is not testable."""
    candles = _chart()
    result = SMCAgent(CONFIG).analyze(
        {"symbol": "XAU/USD", "timeframe": "15m", "data": candles})
    sells = [c for c in (result.get("setup_candidates") or [])
             if str(c.get("direction") or "").upper() == "SELL"]
    best = max(sells, key=lambda c: float(c.get("entry_price") or 0))

    stop = float(best.get("stop_loss") or 0)
    entry = float(best.get("entry_price") or 0)
    assert stop > entry, "a SELL stop must sit above its entry"
    assert stop >= 4050.0, (
        f"the stop must clear the 4050 raid high, got {stop}"
    )


# ── Guards: it must not fabricate zones ────────────────────────────────────

def test_no_swept_level_poi_without_a_raid() -> None:
    """No raid, no level. The POI is evidence-backed or absent."""
    candles = _chart()
    agent, atr, _liq, obs, fvg, price, dr = _context(candles)

    pois = agent._poi_candidates(
        "SELL", price, atr, obs, fvg, dr,
        liquidity={"recent_sweep": {"occurred": False, "type": None}})
    assert "swept_level" not in [p["poi_type"] for p in pois]


def test_swept_level_respects_direction() -> None:
    """A buy-side raid offers a SELL level, never a BUY one."""
    candles = _chart()
    agent, atr, liquidity, obs, fvg, price, dr = _context(candles)

    buy_pois = agent._poi_candidates("BUY", price, atr, obs, fvg, dr,
                                     liquidity=liquidity)
    assert "swept_level" not in [p["poi_type"] for p in buy_pois], (
        "a buy-side raid must not become a place to buy"
    )


def test_unconfirmed_raid_yields_no_swept_level() -> None:
    """A WEAK raid means price never closed back inside; nothing to trade."""
    candles = _chart()
    agent, atr, _liq, obs, fvg, price, dr = _context(candles)

    pois = agent._poi_candidates(
        "SELL", price, atr, obs, fvg, dr,
        liquidity={"recent_sweep": {"occurred": True, "type": "buy_side",
                                    "level": 4047.0, "confirmation": "WEAK"}})
    assert "swept_level" not in [p["poi_type"] for p in pois]


def test_missing_liquidity_argument_is_safe() -> None:
    """Callers that pass no liquidity must keep working unchanged."""
    candles = _chart()
    agent, atr, _liq, obs, fvg, price, dr = _context(candles)

    pois = agent._poi_candidates("SELL", price, atr, obs, fvg, dr)
    assert isinstance(pois, list)
    assert "swept_level" not in [p["poi_type"] for p in pois]


def test_existing_poi_types_are_preserved() -> None:
    """Order blocks, FVGs and the equilibrium fallback all still appear."""
    candles = _chart()
    agent, atr, liquidity, obs, fvg, price, dr = _context(candles)

    buy_pois = agent._poi_candidates("BUY", price, atr, obs, fvg, dr,
                                     liquidity=liquidity)
    kinds = {p["poi_type"] for p in buy_pois}
    assert "order_block" in kinds
    assert "equilibrium" in kinds
