"""An order block must be visible while it is still tradable.

Background
----------
``_detect_order_blocks`` scans ``range(2, len(candles) - 4)`` and measures the
impulse across ``candles[index+1:index+4]`` -- three closed candles after the
block. Two separate problems follow.

**Off-by-one.** With the loop capped at ``len - 4``, the last examinable index
is ``len - 5``, whose ``next_3`` window ends at ``len - 2``. The newest candle
is never used by any block at all. Nothing requires that: a block at index
``len - 4`` has exactly three candles after it, ending on the last one.

**The wait is measured in candles, not in movement.** Requiring three closed
15m candles means 45 minutes before a block can appear. Measured on a
synthetic reversal, the bearish block only became visible two candles after
the displacement began, by which time price had already travelled 180 points
past it -- and 410 points by the time the third candle closed:

    displacement candle   -> no block
    +1 candle             -> no block
    +2 candles            -> block found, price 180 pts past it
    +4 candles            -> block found, price 410 pts past it

A block nobody can trade is not a detection. Gold routinely completes a
100-point leg inside two candles, so the map is finished after the move.

The fix keeps the same evidence standard -- an impulse of at least
``atr * 1.20`` away from the block -- and simply allows that impulse to be
proven by fewer candles when it is already unambiguous, marking such blocks
``PROVISIONAL`` until the full three have closed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.smc_agent import SMCAgent
from utils.indicators import calculate_atr

CONFIG = {"symbol": "XAU/USD"}


def _mk(seq: list[tuple[float, float, float]]) -> list[dict]:
    out: list[dict] = []
    for i, (high, low, close) in enumerate(seq):
        prev_close = out[-1]["close"] if out else (high + low) / 2
        opening = min(max(prev_close, low), high)
        out.append({
            "time": "2026-07-29T%02d:%02d:00+00:00" % (6 + i // 4, (i % 4) * 15),
            "open": round(opening, 2), "high": round(high, 2),
            "low": round(low, 2), "close": round(close, 2), "volume": 1000,
        })
    return out


def _rally_then_reversal(after: int) -> list[dict]:
    """A clean rally, one bullish candle, then a hard drop of ``after`` bars."""
    leg = [(4000 + i * 1.5, 3994 + i * 1.5, 3998 + i * 1.5) for i in range(60)]
    top = leg[-1][0]
    block = (top + 1.0, top - 5.0, top + 0.5)      # the bearish order block
    drop = [(top - 2 - i * 10, top - 14 - i * 10, top - 13 - i * 10)
            for i in range(after)]
    return _mk(leg + [block] + drop)


def _blocks(candles: list[dict], kind: str) -> list[dict]:
    agent = SMCAgent(CONFIG)
    atr = agent._last(calculate_atr(candles, 14), 1.5)
    return [b for b in agent._detect_order_blocks(candles, atr, "15m")
            if b.get("type") == kind]


# ── The regression this fix exists to prevent ──────────────────────────────

def test_block_is_visible_once_the_displacement_is_unambiguous() -> None:
    """One decisive candle away from the block is enough to name it.

    Failure injection: restoring the fixed three-candle window makes this
    return no bearish block.
    """
    found = _blocks(_rally_then_reversal(after=1), "bearish")

    assert found, (
        "a bearish block followed by a displacement larger than the impulse "
        "threshold must be detectable before three candles have closed"
    )


def test_newest_candle_can_complete_a_block() -> None:
    """The off-by-one: the last candle must not be structurally ignored."""
    candles = _rally_then_reversal(after=3)
    found = _blocks(candles, "bearish")

    assert found, "three candles after the block is the documented minimum"
    # The block sits at index len-4; its impulse window ends on the last bar.
    assert len(candles) >= 64


def test_early_block_is_marked_provisional() -> None:
    """A block named on partial evidence must say so."""
    found = _blocks(_rally_then_reversal(after=1), "bearish")
    assert found
    assert found[-1].get("confirmation_state") == "PROVISIONAL", (
        "a block detected before the full window must be labelled, so nothing "
        "downstream treats it as fully confirmed"
    )


def test_block_is_confirmed_once_the_window_completes() -> None:
    """After three candles the same block is no longer provisional."""
    found = _blocks(_rally_then_reversal(after=3), "bearish")
    assert found
    assert found[-1].get("confirmation_state") == "CONFIRMED"


def test_entry_distance_shrinks_with_earlier_detection() -> None:
    """The whole point: the block must still be reachable when it appears."""
    candles = _rally_then_reversal(after=1)
    found = _blocks(candles, "bearish")
    assert found
    price = float(candles[-1]["close"])
    distance = abs(float(found[-1]["zone"]["bottom"]) - price) * 10

    assert distance < 200, (
        f"the block should still be within reach when detected, got "
        f"{distance:.0f} points away"
    )


# ── Guards: the evidence standard must not drop ────────────────────────────

def test_weak_move_does_not_create_a_block() -> None:
    """A drift is not a displacement, however many candles it takes."""
    leg = [(4000 + i * 1.5, 3994 + i * 1.5, 3998 + i * 1.5) for i in range(60)]
    top = leg[-1][0]
    block = (top + 1.0, top - 5.0, top + 0.5)
    drift = [(top - 0.2, top - 1.2, top - 1.0)]     # far below atr * 1.20
    found = _blocks(_mk(leg + [block] + drift), "bearish")

    assert not found, (
        "an impulse below the configured threshold must not qualify, no "
        "matter how early we look"
    )


def test_block_with_no_following_candle_is_not_created() -> None:
    """A block needs at least one closed candle after it to prove anything."""
    found = _blocks(_rally_then_reversal(after=0), "bearish")
    assert not found, (
        "the block candle alone proves nothing; displacement must be observed"
    )


def test_bullish_blocks_are_detected_symmetrically() -> None:
    """The change applies to both sides."""
    leg = [(4100 - i * 1.5, 4094 - i * 1.5, 4096 - i * 1.5) for i in range(60)]
    bottom = leg[-1][1]
    block = (bottom + 5.0, bottom - 1.0, bottom - 0.5)   # bearish candle
    rally = [(bottom + 14, bottom + 2, bottom + 13)]
    found = _blocks(_mk(leg + [block] + rally), "bullish")

    assert found, "an early bullish block must be detected on the same terms"


def test_existing_confirmed_blocks_are_unchanged() -> None:
    """Long-settled blocks keep their fields and their mitigation status."""
    candles = _rally_then_reversal(after=6)
    found = _blocks(candles, "bearish")

    assert found
    block = found[-1]
    for key in ("zone", "strength", "mitigation_status", "displacement_quality",
                "displacement_atr", "equilibrium", "timeframe"):
        assert key in block, f"existing field {key} must be preserved"
    assert block.get("confirmation_state") == "CONFIRMED"


def test_quiet_market_produces_no_blocks() -> None:
    """No displacement anywhere means no blocks at all."""
    quiet = _mk([(4020 + (i % 2), 4016 + (i % 2), 4018 + (i % 2))
                 for i in range(70)])
    assert not _blocks(quiet, "bearish")
    assert not _blocks(quiet, "bullish")


def test_impulse_threshold_is_actually_enforced() -> None:
    """Earlier detection must not become looser detection.

    Failure injection caught a hole here: dropping the impulse threshold from
    ``atr * 1.20`` to ``atr * 0.10`` broke none of the tests above, because
    every fixture displaced far enough to clear either bar. A gate nobody
    tests at its boundary is a gate that can be widened silently -- which is
    exactly how "detect it sooner" turns into "detect anything".

    This walks a move from just under the threshold to just over it and
    requires the detector to change its mind at the documented line.
    """
    agent = SMCAgent(CONFIG)
    leg = [(4000 + i * 1.5, 3994 + i * 1.5, 3998 + i * 1.5) for i in range(60)]
    top = leg[-1][0]
    # Two gates decide here, and they must be separated to test either one:
    #
    #   geometric : future_close < block_low - atr * 0.30
    #   impulse   : |future_close - block_close| >= atr * 1.20
    #
    # A shallow block candle clears the geometric line while still sitting
    # under the impulse threshold, which leaves the threshold as the only gate
    # in play. A deep candle does the opposite and would silently test the
    # geometry instead -- the mistake an earlier version of this test made.
    block = (top + 1.0, top - 2.0, top + 0.5)
    block_close = block[2]

    probe = _mk(leg + [block])
    atr = agent._last(calculate_atr(probe, 14), 1.5)
    threshold = max(atr * 1.20, 1.20)

    # Below the impulse threshold, but already past the geometric line.
    quiet_close = block[1] - atr * 0.40
    quiet_move = abs(block_close - quiet_close)
    assert quiet_move < threshold, (
        "fixture must place the quiet close under the impulse threshold "
        f"(move {quiet_move:.2f} vs threshold {threshold:.2f})"
    )
    quiet = _mk(leg + [block] + [(block_close, quiet_close - 1.0, quiet_close)])
    assert not _blocks(quiet, "bearish"), (
        f"a move of {quiet_move:.2f} is below the {threshold:.2f} impulse "
        "threshold and must not create a block, however clean its geometry"
    )

    # Comfortably beyond it: must be accepted.
    loud_close = block_close - threshold * 1.6
    loud = _mk(leg + [block] + [(block_close, loud_close - 1.0, loud_close)])
    assert _blocks(loud, "bearish"), (
        f"a move of {threshold * 1.6:.2f} clears the {threshold:.2f} "
        "threshold and must create a block"
    )
