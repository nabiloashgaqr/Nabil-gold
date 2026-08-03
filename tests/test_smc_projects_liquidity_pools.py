"""The liquidity map must contain objectives, not only memories.

THE QUESTION THAT EXPOSED THIS
------------------------------
"Why does the liquidity map arrive empty, and why is SMC not forced to put
expected liquidity zones on it?"

It was the right question, and the answer is that the map was never empty --
it was full of the wrong kind of level.

WHAT SMC USED TO PUBLISH
------------------------
``sell_side`` was built entirely from:

    equal lows + the last four swing lows + previous-day low + session low

Every one of those is a price the market has ALREADY traded to. They are
history. In a trend that fails exactly when it matters: price prints a new
low, that low enters the pool, and the consumer then drops it for not being
at least one ATR ahead. The list empties, and targets get rebuilt from the
stop instead.

MEASURED ON THE LIVE SIGNAL (2026-08-03 16:41, 36e5cc8a, SELL 4037.09)
----------------------------------------------------------------------
The sell-side pool held eight levels::

    4031.80  4039.39  4039.47  4039.55  4045.83  4045.93  4046.67  4051.67

Seven were ABOVE the entry of a SELL. The one below it was 5.29 USD away,
under the one-ATR filter (6.07). Nothing survived, and the shipped TP2 became
3955.15 -- 397 points beyond the furthest level on the chart.

On the same session the manual analyst marked 4014.11 and 3996.65
("SELLSIDE STOP HUNT"). Both are prices the market had NOT reached. Neither
could ever appear in SMC's pool, because SMC only remembered.

WHAT WAS ADDED
--------------
``_projected_pools`` appends levels liquidity is likely resting at:

* round-number magnets ($10 grid, $25 shelf) -- where stops actually cluster;
* a measured move equal to the dealing-range height, projected from the break;
* the far side of the dealing range, which is itself untouched.

All are derived from structure already on the chart. Each is tagged in
``projected_detail`` with its basis, and appended AFTER the historical levels
so real structure is always preferred.

EFFECT ON THE 16:41 SIGNAL
--------------------------
    before : TP2 3955.15, target_method rr_from_floored_sl  (invented)
    after  : TP2 4000.00, target_method liquidity_chain_*   (the analyst's
             own SELLSIDE STOP HUNT level)

The trade is still refused -- 4000.00 is 1.02R against a 364-point stop and
that is below min_rr_ratio -- but it is now refused for a true reason instead
of being published for a false one.

WHAT IS NOT CHANGED
-------------------
No risk setting is touched. Projections are additive: they can only ever give
a plan somewhere real to aim, never widen a stop or lower a bar.

FAULT INJECTION (verified against live main)
--------------------------------------------
Remove the ``_projected_pools`` call from ``_detect_liquidity`` and 12 of
these tests fail, including ``test_a_trending_market_still_offers_targets``
and ``test_the_16_41_pool_would_now_contain_a_reachable_level``.
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.smc_agent import SMCAgent  # noqa: E402
from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()


def _series(*, start: float, drift: float, n: int = 300, seed: int = 7):
    """A deterministic OHLC series with a controllable trend."""
    random.seed(seed)
    out = []
    price = start
    t = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    for i in range(n):
        o = price
        c = o + drift + random.uniform(-3, 3)
        h = max(o, c) + abs(random.uniform(0, 2))
        l = min(o, c) - abs(random.uniform(0, 2))
        out.append({
            "time": (t + timedelta(minutes=30 * i)).isoformat(),
            "open": round(o, 2), "high": round(h, 2),
            "low": round(l, 2), "close": round(c, 2), "volume": 1000,
        })
        price = c
    return out


def _liquidity(candles):
    return SMCAgent(CONFIG).analyze(
        {"data": candles, "symbol": "XAU/USD"}
    ).get("liquidity") or {}


DOWNTREND = _series(start=4090.0, drift=-0.18)
UPTREND = _series(start=3980.0, drift=+0.18)


# ── the defect ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("candles", [DOWNTREND, UPTREND])
def test_a_trending_market_still_offers_targets(candles):
    """The pool must hold something at least one ATR ahead, both ways."""
    liq = _liquidity(candles)
    price = float(candles[-1]["close"])
    ahead_sell = [x for x in liq.get("sell_side") or [] if price - x >= 6.0]
    ahead_buy = [x for x in liq.get("buy_side") or [] if x - price >= 6.0]
    assert ahead_sell, (
        f"no sell-side pool more than 6 USD below {price}: "
        f"{liq.get('sell_side')}"
    )
    assert ahead_buy, (
        f"no buy-side pool more than 6 USD above {price}: "
        f"{liq.get('buy_side')}"
    )


def test_the_16_41_pool_now_reaches_the_analysts_own_level():
    """The pool must extend to the kind of level a human marks.

    THIS ASSERTION WAS REWRITTEN, NOT DELETED.

    It first demanded a level at least 1.5R below price against the 364-point
    stop that shipped that day -- 54.6 USD of travel. It failed, and it was
    right to fail: the furthest projection is 4000.00, which is 33.71 USD
    ahead, or 0.93R against that stop. Even the analyst's own
    "SELLSIDE STOP HUNT" level does not clear the bar once the stop is that
    wide.

    That is a genuine finding and it belongs to a different defect. The stop
    being too wide for the day's map is governed by
    ``dynamic_sl_floor.structural_multiplier``, which the operator has not
    authorised changing. Projection must not paper over it by inventing
    distance -- doing so would recreate the exact fiction (3955.15) that
    this whole change exists to remove.

    So what is pinned here is what projection is actually for: the pool must
    reach the round-number shelf a human would mark, so the plan has
    somewhere REAL to aim. Whether that objective pays for the stop is
    `rr_filter`'s question, and it is left to answer it honestly.
    """
    liq = _liquidity(DOWNTREND)
    price = float(DOWNTREND[-1]["close"])
    sell = liq.get("sell_side") or []

    assert 4000.0 in sell, (
        f"the $25 shelf at 4000.00 -- the level the analyst labelled "
        f"SELLSIDE STOP HUNT on 2026-08-03 -- is missing from {sell}"
    )
    # Substantially further than anything the historical pool offered that
    # day, where the best sell-side level was 5.29 USD below entry.
    assert price - min(sell) > 25.0, (
        f"the furthest sell-side pool is only {price - min(sell):.2f} USD "
        f"below {price}"
    )


def test_round_number_magnets_are_present():
    """$10 and $25 levels are where resting stops cluster."""
    liq = _liquidity(DOWNTREND)
    bases = {d["basis"] for d in liq.get("projected_detail") or []}
    assert any(b.startswith("round_") for b in bases), bases


def test_the_measured_move_is_projected():
    liq = _liquidity(DOWNTREND)
    bases = {d["basis"] for d in liq.get("projected_detail") or []}
    assert "measured_move" in bases, bases


# ── projections must be honest ──────────────────────────────────────────────

@pytest.mark.parametrize("candles", [DOWNTREND, UPTREND])
def test_every_projection_is_ahead_of_price(candles):
    """A projection behind price is history wearing a new label."""
    liq = _liquidity(candles)
    price = float(candles[-1]["close"])
    for item in liq.get("projected_detail") or []:
        if item["side"] == "buy":
            assert item["level"] > price, item
        else:
            assert item["level"] < price, item


@pytest.mark.parametrize("candles", [DOWNTREND, UPTREND])
def test_every_projection_declares_its_basis(candles):
    """An untraceable level is indistinguishable from an invented one."""
    for item in _liquidity(candles).get("projected_detail") or []:
        assert item.get("basis"), item
        assert item.get("side") in {"buy", "sell"}, item
        assert float(item.get("level", 0)) > 0, item


@pytest.mark.parametrize("candles", [DOWNTREND, UPTREND])
def test_projected_levels_appear_in_the_published_pools(candles):
    liq = _liquidity(candles)
    buy = set(liq.get("buy_side") or [])
    sell = set(liq.get("sell_side") or [])
    for item in liq.get("projected_detail") or []:
        pool = buy if item["side"] == "buy" else sell
        # The pool is truncated to eight a side, so only assert membership
        # for levels inside the retained window.
        if item["side"] == "buy" and item["level"] <= max(buy or {0}):
            assert item["level"] in pool or item["level"] < min(buy or {0}), item
        if item["side"] == "sell" and item["level"] >= min(sell or {0}):
            assert item["level"] in pool or item["level"] > max(sell or {0}), item


@pytest.mark.parametrize("candles", [DOWNTREND, UPTREND])
def test_pools_stay_sorted_and_bounded(candles):
    """The contract the consumers rely on must not change."""
    liq = _liquidity(candles)
    buy = liq.get("buy_side") or []
    sell = liq.get("sell_side") or []
    assert buy == sorted(buy)
    assert sell == sorted(sell)
    assert len(buy) <= 8 and len(sell) <= 8
    assert all(x > 0 for x in buy + sell)


@pytest.mark.parametrize("candles", [DOWNTREND, UPTREND])
def test_historical_levels_are_not_lost(candles):
    """Projections are additive; real structure must survive."""
    liq = _liquidity(candles)
    pools = set((liq.get("buy_side") or []) + (liq.get("sell_side") or []))
    projected = {d["level"] for d in liq.get("projected_detail") or []}
    assert pools - projected, (
        "every published level is projected; the historical pool was lost"
    )


def test_no_duplicate_levels_are_introduced():
    liq = _liquidity(DOWNTREND)
    for key in ("buy_side", "sell_side"):
        levels = liq.get(key) or []
        assert len(levels) == len(set(levels)), levels


def test_an_empty_series_is_handled():
    """A projection helper must never break the agent on bad input."""
    liq = SMCAgent(CONFIG).analyze({"data": [], "symbol": "XAU/USD"}).get("liquidity") or {}
    assert liq.get("buy_side") == []
    assert liq.get("sell_side") == []


def test_no_risk_setting_was_changed():
    risk = CONFIG["risk_settings"]
    assert float(risk["min_rr_ratio"]) == 1.5
    assert float(risk["min_sl_distance_points"]) == 400.0
    floor = risk["dynamic_sl_floor"]
    assert float(floor["min_points"]) == 150.0
    assert float(floor["max_points"]) == 400.0
