"""A pending order can only be filled by price action it witnessed.

A BUY STOP at 4028.77 reported:

    Status: PENDING → OPEN
    Current Price: 4017.65
    Waiting: 0.0h

Filled 111 points *below* its own trigger, in the same cycle it was created.

The cause is that `_evaluate_pending` is handed "the latest candle" with no
timestamp. On a 5m/15m frame that bar usually opened before the order did, so
its high belongs to price action from before the order existed. Gold had
printed ~4036 earlier in the bar and then collapsed to 4008; the order was
created at 12:21 into the wreckage and matched against a high it never saw.

Age is the only reliable proxy available here: once an order has outlived the
bar interval, any extreme in the current bar is necessarily after it. Below
that age, the live price alone decides.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.open_trades_manager import OpenTradesManager

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

NOW = datetime(2026, 7, 29, 12, 21, 30, tzinfo=timezone.utc)
ENTRY = 4028.77


def _order(age_minutes: float, side: str = "BUY", order_type: str = "BUY_STOP",
           entry: float = ENTRY):
    stamp = (NOW - timedelta(minutes=age_minutes)).isoformat()
    return {
        "id": "TRADE_TOUCH_GUARD",
        "type": side,
        "order_type": order_type,
        "status": "PENDING",
        "entry_price": entry,
        "stop_loss": 4013.77 if side == "BUY" else 4043.77,
        "initial_stop_loss": 4013.77 if side == "BUY" else 4043.77,
        "tp1": 4047.76 if side == "BUY" else 4009.77,
        "tp2": 4054.56 if side == "BUY" else 4003.77,
        "created_at": stamp,
        "entry_time": stamp,
        "updates_sent": [],
        "setup_context": {"pending_plan_role": "PRIMARY"},
    }


def _filled(trade, price, high, low, manager=None):
    manager = manager or OpenTradesManager(CONFIG)
    result = manager.evaluate_trade(
        trade, price, NOW, candle_high=high, candle_low=low,
        market_data_source="twelvedata",
    )
    return "ORDER_FILLED" in (result.get("events") or [])


# --- the live failure ----------------------------------------------------

def test_a_new_order_is_not_filled_by_a_high_that_predates_it() -> None:
    """The exact numbers from the live activation."""
    assert _filled(_order(age_minutes=0), price=4017.65, high=4036.0, low=4008.0) is False


def test_the_order_stays_pending_rather_than_being_cancelled() -> None:
    """Refusing a bad fill must not throw the setup away."""
    manager = OpenTradesManager(CONFIG)
    result = manager.evaluate_trade(
        _order(age_minutes=0), 4017.65, NOW,
        candle_high=4036.0, candle_low=4008.0, market_data_source="twelvedata",
    )

    assert result["new_status"] == "PENDING"
    assert "ORDER_FILLED" not in (result.get("events") or [])


# --- what must still fill ------------------------------------------------

def test_a_live_touch_fills_immediately_even_on_a_new_order() -> None:
    """Price actually at the trigger needs no waiting period."""
    assert _filled(_order(age_minutes=0), price=4030.0, high=4031.0, low=4029.0) is True


def test_a_matured_order_may_use_the_candle_extreme() -> None:
    """Past the bar interval, the bar's high is genuinely after the order."""
    assert _filled(_order(age_minutes=20), price=4017.65, high=4036.0, low=4008.0) is True


def test_a_sell_stop_is_guarded_the_same_way() -> None:
    """The mirror case: a low printed before a SELL STOP existed."""
    sell = _order(age_minutes=0, side="SELL", order_type="SELL_STOP", entry=4020.0)

    assert _filled(sell, price=4030.0, high=4036.0, low=4008.0) is False
    assert _filled(sell, price=4019.0, high=4021.0, low=4018.0) is True


def test_a_matured_sell_stop_uses_the_candle_low() -> None:
    sell = _order(age_minutes=20, side="SELL", order_type="SELL_STOP", entry=4020.0)

    assert _filled(sell, price=4030.0, high=4036.0, low=4008.0) is True


# --- helper behaviour ----------------------------------------------------

def test_missing_creation_time_falls_back_to_permissive() -> None:
    """Absent metadata must not block a legitimate fill."""
    manager = OpenTradesManager(CONFIG)
    trade = _order(age_minutes=0)
    trade.pop("created_at")
    trade.pop("entry_time")

    assert manager._touch_is_after_creation(trade, NOW) is True


def test_the_age_threshold_is_configurable() -> None:
    manager = OpenTradesManager(CONFIG)
    assert manager.pending_touch_min_age_minutes > 0

    relaxed = OpenTradesManager({
        "pending_freshness": {"touch_revalidation": {"min_age_minutes_for_candle_fill": 0}}
    })
    assert relaxed.pending_touch_min_age_minutes == 0
    # With the guard disabled the old behaviour returns.
    assert _filled(_order(age_minutes=0), 4017.65, 4036.0, 4008.0, manager=relaxed) is True


@pytest.mark.parametrize("age,expected", [(0, False), (5, False), (14, False), (15, True), (30, True)])
def test_the_maturity_boundary(age: float, expected: bool) -> None:
    manager = OpenTradesManager(CONFIG)
    assert manager._touch_is_after_creation(_order(age_minutes=age), NOW) is expected
