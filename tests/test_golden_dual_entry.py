"""Golden dual-entry exception -- fault injection (operator 2026-08-07).

Approved spec: ONLY with a live same-direction pending inside the
0.618-0.786 band; price must tag 0.618 AND a completed candle must CLOSE back
beyond it; >=2 qualified supporters. A close beyond 0.786 invalidates.
"""

from __future__ import annotations

import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.golden_dual_entry import review_golden_dual_entry  # noqa: E402

# His chart: 0 = 4371.70, 1 = 4301.46 -> 0.618 = 4328.29, 0.786 = 4316.49
LOW, HIGH = 4301.46, 4371.70
L618, L786 = 4328.29, 4316.49
PENDING = 4319.46


def _review(low, close, pending=PENDING, support=2, direction="BUY"):
    candle = {"time": "t", "open": close, "high": max(low, close) + 1,
              "low": low, "close": close}
    return review_golden_dual_entry(
        direction=direction, candles=[candle], swing_low=LOW, swing_high=HIGH,
        pending_entry=pending, qualified_support=support)


def test_golden_touch_with_close_confirmation_arms_dual() -> None:
    r = _review(low=4327.0, close=4330.0)   # tags 0.618, closes back above
    assert r["action"] == "GOLDEN_DUAL_ENTRY"
    assert r["levels"]["l618"] == 4328.29
    assert r["levels"]["l70"] == 4322.53


def test_touch_without_close_confirmation_is_refused() -> None:
    r = _review(low=4327.0, close=4326.0)   # wick only, close below 0.618
    assert r["action"] == "NONE"
    assert "CLOSE" in r["reason"]


def test_close_between_0618_and_0786_still_arms() -> None:
    """Revision 2026-08-07: a close inside the deep zone does NOT invalidate;
    exits belong to the stop / thesis exit, not to fibo."""
    r = _review(low=4315.0, close=4329.0)
    assert r["action"] == "GOLDEN_DUAL_ENTRY"


def test_shallow_pending_keeps_ordinary_governance() -> None:
    r = _review(low=4327.0, close=4330.0, pending=4340.0)
    assert r["action"] == "NONE"
    assert "0.70" in r["reason"]


def test_pending_at_079_is_accepted() -> None:
    """0.79 fibo = 4316.21 -- deeper than 0.70, so the dual applies."""
    r = _review(low=4327.0, close=4330.0, pending=4316.21)
    assert r["action"] == "GOLDEN_DUAL_ENTRY"


def test_fewer_than_two_qualified_supporters_refuses() -> None:
    r = _review(low=4327.0, close=4330.0, support=1)
    assert r["action"] == "NONE"


def test_no_tag_no_trade() -> None:
    r = _review(low=4332.0, close=4334.0)   # never touched 0.618
    assert r["action"] == "NONE"


def test_sell_side_mirrors() -> None:
    # Down-impulse: swing high->low mirrored; 0.618 above the low.
    r = review_golden_dual_entry(
        direction="SELL",
        candles=[{"time": "t", "open": 4340.0, "high": 4345.0,
                  "low": 4338.0, "close": 4340.0}],
        swing_low=4301.46, swing_high=4371.70,
        pending_entry=4352.0,   # SELL: >= 0.70 level 4350.63
        qualified_support=2)
    assert r["action"] == "GOLDEN_DUAL_ENTRY"
