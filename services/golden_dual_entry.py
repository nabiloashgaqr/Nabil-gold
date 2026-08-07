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
MIN_PENDING_RATIO = 0.70


def fib_ladder(swing_low: float, swing_high: float) -> Dict[str, float]:
    """Retracement levels of the impulse low->high (BUY orientation)."""
    rng = swing_high - swing_low
    return {
        "0.5": swing_high - 0.5 * rng,
        "0.618": swing_high - GOLDEN * rng,
        "0.786": swing_high - 0.786 * rng,
    }


def _ladder_for(direction: str, swing_low: float, swing_high: float) -> Dict[str, float]:
    rng = swing_high - swing_low
    if direction == "SELL":
        return {
            "0.618": swing_low + GOLDEN * rng,
            "0.70": swing_low + MIN_PENDING_RATIO * rng,
        }
    return {
        "0.618": swing_high - GOLDEN * rng,
        "0.70": swing_high - MIN_PENDING_RATIO * rng,
    }


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
    l70 = ladder["0.70"]

    if pending_entry <= 0:
        return none("no pending entry")
    # Operator 2026-08-07 revision: accept ONLY pendings at >= 0.70 fibo
    # (deeper discount). A close beyond any fibo line does NOT invalidate the
    # exception -- exits are the stop's / thesis-exit's job, not fibo's.
    if direction == "BUY":
        pending_deep_enough = pending_entry <= l70
        touched = low <= l618
        confirmed_close = close > l618
    else:
        pending_deep_enough = pending_entry >= l70
        touched = high >= l618
        confirmed_close = close < l618

    if not pending_deep_enough:
        return none(f"pending {pending_entry:.2f} shallower than 0.70 fibo "
                    f"({l70:.2f}) -- dual exception needs a deep-discount pending")
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
            f"pending {pending_entry:.2f} at >= 0.70 fibo; "
            f"{qualified_support} qualified supporters -> FULL market + pending "
            f"kept as second entry (200-pt separation exception). Exits stay "
            f"with each trade's stop / thesis exit."
        ),
        "levels": {"l618": round(l618, 2), "l70": round(l70, 2)},
        "qualified_support": qualified_support,
    }
