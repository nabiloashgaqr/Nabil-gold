"""Golden dual-entry exception (operator directive 2026-08-07).

Active ONLY while a same-direction pending order is alive. When price tags
the 0.618 golden retracement of the active impulse and a completed candle
CLOSES back beyond it (confirmation, not a touch), with >=2 qualified agents
supporting, and the resting pending sits inside the 0.618-0.786 band, the
system enters FULL market now and keeps the pending as a second full entry --
the single exception to the >=200-pt second-entry rule. The two trades are
managed independently by the existing per-trade management.

Invalidation: a completed candle closing beyond 0.786 kills the exception
(the pending returns to ordinary single-order governance).
"""

from __future__ import annotations

from typing import Any, Dict, List

GOLDEN = 0.618
DEEP = 0.786


def fib_ladder(swing_low: float, swing_high: float) -> Dict[str, float]:
    """Retracement levels of the impulse low->high (BUY orientation)."""
    rng = swing_high - swing_low
    return {
        "0.5": swing_high - 0.5 * rng,
        "0.618": swing_high - GOLDEN * rng,
        "0.786": swing_high - DEEP * rng,
    }


def _ladder_for(direction: str, swing_low: float, swing_high: float) -> Dict[str, float]:
    if direction == "SELL":
        rng = swing_high - swing_low
        return {
            "0.5": swing_low + 0.5 * rng,
            "0.618": swing_low + GOLDEN * rng,
            "0.786": swing_low + DEEP * rng,
        }
    return fib_ladder(swing_low, swing_high)


def review_golden_dual_entry(
    *,
    direction: str,
    candles: List[Dict[str, Any]],
    swing_low: float,
    swing_high: float,
    pending_entry: float,
    qualified_support: int,
    config: Dict[str, Any] | None = None,
    min_support: int = 2,
) -> Dict[str, Any]:
    """Return {"action": "GOLDEN_DUAL_ENTRY", ...} or {"action": "NONE", reason}."""

    def none(reason: str) -> Dict[str, Any]:
        return {"action": "NONE", "reason": reason}

    if direction not in {"BUY", "SELL"}:
        return none("not a directional decision")
    if swing_high <= swing_low:
        return none("no valid impulse swing")
    if not candles:
        return none("no candles")
    last = candles[-1]
    low = float(last.get("low") or 0.0)
    high = float(last.get("high") or 0.0)
    close = float(last.get("close") or 0.0)
    if low <= 0 or high <= 0 or close <= 0:
        return none("bad candle")

    ladder = _ladder_for(direction, swing_low, swing_high)
    l618 = ladder["0.618"]
    l786 = ladder["0.786"]

    if pending_entry <= 0:
        return none("no pending entry")
    if direction == "BUY":
        pending_in_band = l786 <= pending_entry <= l618
        touched = low <= l618
        confirmed_close = close > l618
        invalidated = close < l786
    else:
        pending_in_band = l618 <= pending_entry <= l786
        touched = high >= l618
        confirmed_close = close < l618
        invalidated = close > l786

    if not pending_in_band:
        return none(f"pending {pending_entry:.2f} outside the 0.618-0.786 band "
                    f"({l786:.2f}-{l618:.2f})")
    if invalidated:
        return none(f"candle closed beyond 0.786 ({l786:.2f}) -- impulse invalidated")
    if not touched:
        return none("price did not tag 0.618")
    if not confirmed_close:
        return none("0.618 touched but the candle did not CLOSE back beyond it")
    if qualified_support < min_support:
        return none(f"only {qualified_support} qualified supporters (< {min_support})")

    return {
        "action": "GOLDEN_DUAL_ENTRY",
        "reason": (
            f"golden touch: 0.618 ({l618:.2f}) tagged and closed back beyond it; "
            f"pending {pending_entry:.2f} inside the 0.786 band; "
            f"{qualified_support} qualified supporters -> FULL market + pending "
            f"kept as second entry (200-pt separation exception)"
        ),
        "levels": {"l618": round(l618, 2), "l786": round(l786, 2)},
        "qualified_support": qualified_support,
    }
