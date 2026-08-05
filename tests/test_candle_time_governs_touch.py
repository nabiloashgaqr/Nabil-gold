"""Candle timestamps govern touch detection — no phantom fills, no phantom stops.

Operator directive (2026-08-05), after TRADE_20260805_015055_001242_2e09b05c:
a BUY LIMIT at 4090.25 was "activated" while every displayed price stayed
above 4093, and its trailing stop later "hit" at 4104.16 with the current
price shown at 4126.21. The manager had been trusting "the latest candle"
with no timestamp, on an age proxy alone.

Three guards, all pinned here:
  1. STALENESS: a latest candle older than max_candle_age_minutes (10) is a
     price-reading failure — its extremes collapse to the live price, and a
     pending order waits for a fresh bar instead of activating.
  2. ORDER: a fresh candle may only fill an order with a window that started
     at or after the order existed; a straddling bar is judged by the live
     price alone.
  3. The legacy age proxy survives ONLY for candles with no timestamp.

FAULT INJECTION: revert either guard in `agents/open_trades_manager.py`
(the staleness collapse in `evaluate_trade` / `_evaluate_pending`, or the
timestamp ordering block) and these tests fail — the phantom returns.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.open_trades_manager import OpenTradesManager

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

NOW = datetime(2026, 8, 5, 2, 7, 0, tzinfo=timezone.utc)
ENTRY = 4090.25          # the phantom trade's entry
STOP = 4075.25
TP1 = 4105.94
TP2 = 4150.00


def _manager() -> OpenTradesManager:
    return OpenTradesManager(CONFIG)


def _pending(age_minutes: float, order_type: str = "BUY_LIMIT",
             entry: float = ENTRY) -> dict:
    stamp = (NOW - timedelta(minutes=age_minutes)).isoformat()
    return {
        "id": "TRADE_PHANTOM_GUARD",
        "type": "BUY",
        "order_type": order_type,
        "status": "PENDING",
        "entry_price": entry,
        "stop_loss": STOP,
        "initial_stop_loss": STOP,
        "tp1": TP1,
        "tp2": TP2,
        "created_at": stamp,
        "entry_time": stamp,
        "updates_sent": [],
        # Non-empty: the manager reuses the trade's own snapshot object for
        # the pending_runtime audit (an empty dict detaches it in memory).
        "signal_snapshot": {"signal": {"type": "BUY", "entry": {"price": entry}}},
        "setup_context": {"pending_plan_role": "PRIMARY"},
    }


def _evaluate(trade, price, *, high=None, low=None, candle_time=None):
    return _manager().evaluate_trade(
        trade, price, NOW,
        candle_high=high, candle_low=low,
        market_data_source="twelvedata",
        candle_time=candle_time,
    )


def _filled(result) -> bool:
    return "ORDER_FILLED" in (result.get("events") or [])


# --- guard 1: staleness -----------------------------------------------------

def test_a_stale_candle_cannot_fill_an_old_order() -> None:
    """The exact phantom: order 20 min old (past the legacy age proxy), the
    'latest' candle is 25 min old with a low through the entry, and the live
    price never came near it. The stale bar must not activate the order."""
    trade = _pending(age_minutes=20)
    stale_time = (NOW - timedelta(minutes=25)).isoformat()
    result = _evaluate(trade, 4098.41, high=4100.0, low=4088.0, candle_time=stale_time)
    assert _filled(result) is False, "a stale candle filled the order — the phantom is back"
    assert result.get("new_status") == "PENDING"


def test_pending_waits_out_a_stale_bar_but_a_fresh_bar_fills() -> None:
    """Same geometry two minutes later, with a fresh candle: the fill stands."""
    trade = _pending(age_minutes=20)
    fresh_time = (NOW - timedelta(minutes=2)).isoformat()
    result = _evaluate(trade, 4098.41, high=4100.0, low=4088.0, candle_time=fresh_time)
    assert _filled(result) is True
    runtime = (trade.get("signal_snapshot") or {}).get("pending_runtime") or {}
    assert runtime.get("fill_candle_time") == fresh_time, "the filling candle must be named in the audit"
    assert runtime.get("fill_judged_by") == "candle_timestamp"


# --- guard 2: ordering ------------------------------------------------------

def test_a_straddling_candle_is_judged_by_the_live_price_only() -> None:
    """The bar started before the order and ends after it: its extreme may
    belong to the minutes before the order existed. Only the live price may
    fill it — and here the live price does not cross the trigger."""
    trade = _pending(age_minutes=2, order_type="BUY_STOP", entry=4095.0)
    straddling = (NOW - timedelta(minutes=4)).isoformat()  # 5m bar spans creation
    result = _evaluate(trade, 4093.0, high=4097.0, low=4092.0, candle_time=straddling)
    assert _filled(result) is False


def test_a_straddling_candle_still_fills_when_the_live_price_crosses() -> None:
    trade = _pending(age_minutes=2, order_type="BUY_STOP", entry=4095.0)
    straddling = (NOW - timedelta(minutes=4)).isoformat()
    result = _evaluate(trade, 4096.0, high=4097.0, low=4092.0, candle_time=straddling)
    assert _filled(result) is True


def test_a_bar_wholly_before_the_order_cannot_fill_it_even_when_old_enough() -> None:
    """Order created 2 min ago; the bar printed 9-4 min ago — its window
    ENDED before the order existed. Fresh enough to pass staleness, and the
    order's age is irrelevant: the timestamp refuses the fill."""
    trade = _pending(age_minutes=2, order_type="BUY_LIMIT", entry=ENTRY)
    ancient = (NOW - timedelta(minutes=9)).isoformat()
    result = _evaluate(trade, 4098.41, high=4100.0, low=4088.0, candle_time=ancient)
    assert _filled(result) is False


# --- guard 3: legacy compatibility ------------------------------------------

def test_a_timestampless_candle_keeps_the_legacy_age_proxy() -> None:
    """No timestamp -> the old age proxy still governs (20 min >= 15 min)."""
    trade = _pending(age_minutes=20)
    result = _evaluate(trade, 4098.41, high=4100.0, low=4088.0, candle_time=None)
    assert _filled(result) is True
    runtime = (trade.get("signal_snapshot") or {}).get("pending_runtime") or {}
    assert runtime.get("fill_judged_by") == "legacy_age_proxy"


# --- staleness also protects live trades ------------------------------------

def test_a_stale_candle_cannot_stop_out_a_live_trade() -> None:
    """The trailing phantom's mirror: a 25-min-old bar's low sits through the
    stop while the live price stands far above it. No SL_HIT."""
    trade = {
        "id": "TRADE_PHANTOM_STOP",
        "type": "BUY",
        "order_type": "BUY_LIMIT",
        "status": "OPEN",
        "entry_price": ENTRY,
        "stop_loss": STOP,
        "initial_stop_loss": STOP,
        "tp1": TP1,
        "tp2": TP2,
        "created_at": (NOW - timedelta(hours=1)).isoformat(),
        "entry_time": (NOW - timedelta(minutes=55)).isoformat(),
        "last_updated": (NOW - timedelta(minutes=5)).isoformat(),
        "updates_sent": [],
    }
    stale_time = (NOW - timedelta(minutes=25)).isoformat()
    result = _evaluate(trade, 4126.21, high=4127.0, low=4070.0, candle_time=stale_time)
    assert result.get("new_status") != "SL_HIT", "a stale candle stopped out a live trade"
    fresh_time = (NOW - timedelta(minutes=1)).isoformat()
    result_fresh = _evaluate(trade, 4126.21, high=4127.0, low=4070.0, candle_time=fresh_time)
    assert result_fresh.get("new_status") == "SL_HIT", "a fresh candle through the stop must still close the trade"


# --- the 80-point rule --------------------------------------------------------

def test_the_80_point_market_rule_is_pinned_and_live() -> None:
    """Operator directive: only a distance ABOVE 80 points may rest as a
    pending order; anything nearer enters MARKET immediately."""
    from utils.helpers import load_config
    import scripts.run_analysis as ra

    cfg = load_config()
    assert float(cfg["order_execution"]["market_threshold_points"]) == 80

    # BUY: 70 pts away -> MARKET; 81 pts away -> LIMIT may rest.
    near = ra._planned_order_type(cfg, "BUY", 4093.0, 4100.0, "XAU/USD")
    assert near == "BUY_MARKET"
    far = ra._planned_order_type(cfg, "BUY", 4091.9, 4100.0, "XAU/USD")
    assert far == "BUY_LIMIT"
