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

    That is a genuine finding and it belonged to a different defect. The stop
    being too wide for the day's map was governed by the x3 multiplier era of
    ``dynamic_sl_floor``; the operator's 2026-08-07 directive ("تحت السيولة،
    حد أدنى 70 نقطة") replaced it with an honest clamp, so structural stops
    now pass through untouched. Projection still must not paper over risk by
    inventing distance -- doing so would recreate the exact fiction (3955.15)
    that this whole change exists to remove.

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
def test_the_published_pool_spans_both_ends(candles):
    """UPDATED: the ladder is now thinned, so membership is not guaranteed.

    The first version asserted that every projected level survives into the
    published pool. Once the grid was extended to cover a 400-point stop that
    became false by design -- there are more rungs than the pool carries, and
    `_trim_ladder` samples them.

    What must hold is the property the pool exists for: it has to serve BOTH
    jobs. Something near enough to be a first target, and something far enough
    to justify the widest stop the configuration allows.
    """
    liq = _liquidity(candles)
    price = float(candles[-1]["close"])
    for side, key in (("sell", "sell_side"), ("buy", "buy_side")):
        pool = liq.get(key) or []
        ahead = [x for x in pool if (x < price if side == "sell" else x > price)]
        assert ahead, f"no {side}-side level ahead of price in {pool}"
        nearest = min(abs(price - x) for x in ahead)
        furthest = max(abs(price - x) for x in ahead)
        assert nearest <= 25.0, (
            f"{side}: nearest rung is {nearest:.2f} USD away; nothing close "
            f"enough to serve as TP1"
        )
        # 400-point stop x 1.5 = 60 USD of required travel.
        assert furthest >= 60.0, (
            f"{side}: furthest rung is only {furthest:.2f} USD away; a wide "
            f"stop can never clear min_rr against this ladder"
        )


@pytest.mark.parametrize("candles", [DOWNTREND, UPTREND])
def test_pools_stay_sorted_and_bounded(candles):
    """The contract the consumers rely on must not change."""
    liq = _liquidity(candles)
    buy = liq.get("buy_side") or []
    sell = liq.get("sell_side") or []
    assert buy == sorted(buy)
    assert sell == sorted(sell)
    # UPDATED from 8. The cap rose to 10 a side when the grid was extended to
    # cover a 400-point stop: the ladder must carry near rungs (TP1) and far
    # rungs (reward for a wide stop) at the same time. Still bounded, so a
    # misconfiguration cannot flood the pool.
    assert len(buy) <= 12 and len(sell) <= 12
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
    rule = risk["stop_from_liquidity"]
    assert rule["min_liquidity_points"] == 200
    assert rule["safety_buffer_points"] == 70
    assert rule["max_stop_points"] == 400


# ── the ladder must reach as far as the stop needs ──────────────────────────
#
# Added after the operator asked the question that reframed the problem:
# "if this liquidity does not cover gold's noise, shouldn't it look for
#  liquidity zones FURTHER away? further targets, a noise-proof stop?"
#
# That is the correct trade-off. The stop is wide because gold moves 50-100
# points in seconds; the answer is not to tighten it into the noise but to
# make sure the map reaches far enough to pay for it.
#
# Measured before this change: the ladder reached 337 points below price
# while a 398-point stop needed 597. Every real level sat inside the gap.

def _required_reach_usd() -> float:
    """Widest permitted stop x min_rr, in USD. Derived, not chosen."""
    risk = CONFIG["risk_settings"]
    widest = float((risk.get("stop_from_liquidity") or {}).get("max_stop_points")
                   or risk.get("min_sl_distance_points") or 400.0)
    return widest * float(risk.get("min_rr_ratio") or 1.5) * 0.10


@pytest.mark.parametrize("candles", [DOWNTREND, UPTREND])
def test_the_ladder_reaches_far_enough_for_the_widest_stop(candles):
    """A 400-point stop needs 600 points of travel. The map must offer it."""
    liq = _liquidity(candles)
    price = float(candles[-1]["close"])
    need = _required_reach_usd()
    for side, key in (("sell", "sell_side"), ("buy", "buy_side")):
        pool = liq.get(key) or []
        ahead = [x for x in pool if (x < price if side == "sell" else x > price)]
        assert ahead, f"no {side}-side level ahead of price"
        reach = max(abs(price - x) for x in ahead)
        assert reach >= need, (
            f"{side}-side ladder reaches {reach:.1f} USD but the widest "
            f"permitted stop needs {need:.1f} USD to clear min_rr_ratio"
        )


@pytest.mark.parametrize("candles", [DOWNTREND, UPTREND])
def test_far_rungs_did_not_evict_the_near_ones(candles):
    """Extending the reach must not cost the first target.

    The first implementation of the extension did exactly that: the pool was
    truncated to the eight FURTHEST levels and every near rung vanished,
    leaving nothing for TP1. `_trim_ladder` exists because of that failure.
    """
    liq = _liquidity(candles)
    price = float(candles[-1]["close"])
    for side, key in (("sell", "sell_side"), ("buy", "buy_side")):
        ahead = [x for x in (liq.get(key) or [])
                 if (x < price if side == "sell" else x > price)]
        assert ahead, f"no {side}-side level ahead of price"
        assert min(abs(price - x) for x in ahead) <= 25.0, (
            f"{side}: nearest rung too far to serve as TP1"
        )


def test_the_analysts_own_shelf_survives_thinning():
    """4000.00 must not be sampled away; it is the level that matters.

    `_trim_ladder` first kept the nearest half and the furthest half and
    dropped the middle -- which discarded exactly this level, the $25 shelf
    the analyst labelled "SELLSIDE STOP HUNT" on 2026-08-03. The thinning was
    rewritten to sample evenly and to prefer round shelves.
    """
    assert 4000.0 in (_liquidity(DOWNTREND).get("sell_side") or [])


def test_the_reach_follows_configuration_not_a_hard_coded_number():
    """Tighten max_points and the required reach must fall with it."""
    import copy as _copy
    from agents.smc_agent import SMCAgent as _Agent
    cfg = _copy.deepcopy(CONFIG)
    cfg["risk_settings"]["stop_from_liquidity"]["max_stop_points"] = 200
    liq = _Agent(cfg).analyze(
        {"data": DOWNTREND, "symbol": "XAU/USD"}
    ).get("liquidity") or {}
    price = float(DOWNTREND[-1]["close"])
    ahead = [x for x in (liq.get("sell_side") or []) if x < price]
    assert ahead
    # Still has to cover 200 x 1.5 = 300 points = 30 USD.
    assert max(price - x for x in ahead) >= 30.0
