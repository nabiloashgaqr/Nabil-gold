"""A mapped order must fill when price trades inside its own entry zone.

2026-07-30, the trade that went missing:

    BUY zone      4054.49 - 4062.05
    ref entry     4058.27      stop 4039.79      TP1 4075.79  TP2 4093.31
    price touched 4060.40  -- inside the zone, 21 pts above the entry
    order never filled; price then ran to 4079.88, straight through TP1

The zone is the thesis; the reference entry is one price inside it. The setup
happened -- the order simply sat a few points too deep.

Chasing it at market was measured and rejected: the stop stays where the map
put it, so entering at 4066.26 inflates risk 185 -> 265 pts and collapses RR
1.90 -> 1.02, under the configured 1.5 floor. Filling at the zone EDGE and
carrying the stop the same distance keeps risk at exactly 185 and RR at 1.69.

    approach                              risk     RR    outcome
    do nothing (old behaviour)               -      -      0 pts
    market chase @4066.26                 265p   1.02   +136 pts
    zone edge + stop moved                185p   1.69   +178 pts

Fault injection: set preserve_planned_risk False and the risk-preservation
test fails; drop require_exit_in_favour and the rejection test fails.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager

SYMBOL = "XAU/USD"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# the live trade
ENTRY, STOP, TP1, TP2 = 4058.27, 4039.79, 4075.79, 4093.31
ZONE_LOW, ZONE_HIGH = 4054.49, 4062.05
TOUCH_LOW = 4060.40          # inside the zone, above the reference entry
PRICE_AFTER = 4066.26        # left the zone upward
PLANNED_RISK_PTS = 185.0


def _config() -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _trade(**over) -> dict:
    trade = {
        "id": "TRADE_20260730_090548_435830_9bc87f75", "type": "BUY",
        "status": "PENDING", "symbol": SYMBOL, "entry_price": ENTRY,
        "stop_loss": STOP, "tp1": TP1, "tp2": TP2, "order_type": "BUY_LIMIT",
        "created_at": "2026-07-30T09:05:48+00:00", "updates_sent": [],
        "signal_snapshot": {
            "setup_context": {"pending_plan_role": "PRIMARY"},
            "session_plan": {"session_bias": "BUY", "plan_ready": True},
            "signal": {"entry": {"price": ENTRY, "low": ZONE_LOW, "high": ZONE_HIGH}},
        },
    }
    trade.update(over)
    return trade


def _evaluate(manager, trade, price, high, low):
    """Replay the wait the mapped entry is entitled to, then the departure.

    The reference entry gets first refusal for `grace_minutes` (two analysis
    cycles). These tests therefore drive three cycles: inside the zone, still
    inside, then leaving it -- which is the sequence the live order saw.
    """
    from datetime import timedelta

    t0 = datetime(2026, 7, 30, 9, 10, tzinfo=timezone.utc)
    state = trade
    for minutes, p, h, lo in (
        (0, 4061.00, 4061.50, low),
        (5, 4061.20, 4061.80, low),
    ):
        res = manager.evaluate_trade(
            state, p, now=t0 + timedelta(minutes=minutes),
            candle_high=h, candle_low=lo,
            recent_candles=[{"time": "x", "open": p, "high": h, "low": lo, "close": p}],
        )
        carried = {k: v for k, v in res["updates"].items()
                   if k in ("signal_snapshot", "pending_cycles")}
        state = {**state, **carried}
    return manager.evaluate_trade(
        state, price, now=t0 + timedelta(minutes=10),
        candle_high=high, candle_low=low,
        recent_candles=[
            {"time": "2026-07-30T09:45:00+00:00", "open": 4062.0, "high": 4066.0,
             "low": low, "close": 4065.0},
            {"time": "2026-07-30T09:50:00+00:00", "open": 4065.0, "high": high,
             "low": 4064.0, "close": price},
        ],
    )


# ── the trade that was lost ────────────────────────────────────────────────

def test_the_lost_trade_now_activates() -> None:
    result = _evaluate(OpenTradesManager(_config()), _trade(),
                       PRICE_AFTER, 4068.17, TOUCH_LOW)
    assert result["new_status"] == "OPEN"
    assert "ORDER_FILLED" in result["events"]


def test_it_fills_at_the_zone_edge_not_at_market() -> None:
    result = _evaluate(OpenTradesManager(_config()), _trade(),
                       PRICE_AFTER, 4068.17, TOUCH_LOW)
    assert result["updates"]["entry_price"] == ZONE_HIGH, (
        "filling at market would be chasing; the zone edge is the worst price "
        "inside the area the map itself drew"
    )


def test_the_planned_risk_is_preserved_exactly() -> None:
    """This is what makes it not a chase."""
    result = _evaluate(OpenTradesManager(_config()), _trade(),
                       PRICE_AFTER, 4068.17, TOUCH_LOW)
    updates = result["updates"]
    risk_points = (updates["entry_price"] - updates["stop_loss"]) / 0.1
    assert abs(risk_points - PLANNED_RISK_PTS) < 1.0, (
        f"risk became {risk_points:.0f} pts; the map planned {PLANNED_RISK_PTS:.0f}"
    )
    assert updates["stop_loss"] > STOP, "the stop must travel with the entry"


def test_the_resulting_rr_clears_the_configured_floor() -> None:
    cfg = _config()
    floor = float(cfg["risk_settings"]["min_rr_ratio"])
    result = _evaluate(OpenTradesManager(cfg), _trade(), PRICE_AFTER, 4068.17, TOUCH_LOW)
    updates = result["updates"]
    rr = (TP2 - updates["entry_price"]) / (updates["entry_price"] - updates["stop_loss"])
    assert rr >= floor, f"RR {rr:.2f} is below the {floor} floor"


def test_market_chasing_would_have_broken_the_rr_floor() -> None:
    """Why option (b) was chosen over a market conversion -- as arithmetic."""
    floor = float(_config()["risk_settings"]["min_rr_ratio"])
    chase_rr = (TP2 - PRICE_AFTER) / (PRICE_AFTER - STOP)
    assert chase_rr < floor, "the premise of this design is that chasing breaks the floor"


# ── the guard that keeps it honest ─────────────────────────────────────────

def test_a_wick_into_the_zone_that_collapses_is_refused() -> None:
    """Touch alone is not entry: price must leave the zone in favour.

    A wick that dips into a BUY zone and then sells off looks identical to a
    real entry at the moment of the touch. Requiring the close to clear the
    far edge is what separates them.
    """
    manager = OpenTradesManager(_config())
    review = manager._zone_touch_review(
        _trade(), trade_type="BUY", order_type="BUY_LIMIT", entry=ENTRY,
        stop_loss=STOP, tp2=TP2,
        # The wick stays ABOVE the reference entry, so the ordinary LIMIT
        # touch cannot fill it -- this isolates the exit-in-favour guard.
        current_price=4059.50,          # still inside the zone
        candle_high=4061.90, candle_low=4058.80,
        recent_window_high=4061.90, recent_window_low=4058.80, symbol=SYMBOL,
    )
    assert review["activate"] is False
    assert "not yet left the zone" in str(review.get("reason", ""))


def test_price_never_reaching_the_zone_is_refused() -> None:
    manager = OpenTradesManager(_config())
    review = manager._zone_touch_review(
        _trade(), trade_type="BUY", order_type="BUY_LIMIT", entry=ENTRY,
        stop_loss=STOP, tp2=TP2, current_price=4075.00,
        candle_high=4076.00, candle_low=4070.00,
        recent_window_high=4076.00, recent_window_low=4070.00, symbol=SYMBOL,
    )
    assert review["activate"] is False


def test_a_normal_touch_of_the_reference_entry_is_left_alone() -> None:
    """When the order can fill properly, do not fill it at a worse price."""
    manager = OpenTradesManager(_config())
    review = manager._zone_touch_review(
        _trade(), trade_type="BUY", order_type="BUY_LIMIT", entry=ENTRY,
        stop_loss=STOP, tp2=TP2, current_price=4060.00,
        candle_high=4062.00, candle_low=4057.00,   # low <= entry
        recent_window_high=4062.00, recent_window_low=4057.00, symbol=SYMBOL,
    )
    assert review["activate"] is False


def test_a_sell_zone_fills_at_the_lower_edge() -> None:
    manager = OpenTradesManager(_config())
    sell = _trade(
        type="SELL", order_type="SELL_LIMIT", entry_price=4047.76,
        stop_loss=4062.76, tp1=4029.17, tp2=4020.91,
        signal_snapshot={
            "setup_context": {"pending_plan_role": "PRIMARY"},
            "session_plan": {"session_bias": "SELL", "plan_ready": True},
            "signal": {"entry": {"price": 4047.76, "low": 4045.64, "high": 4049.88}},
        },
    )
    review = manager._zone_touch_review(
        sell, trade_type="SELL", order_type="SELL_LIMIT", entry=4047.76,
        stop_loss=4062.76, tp2=4020.91,
        current_price=4040.00,           # left the zone downward
        candle_high=4046.50, candle_low=4039.00,
        recent_window_high=4046.50, recent_window_low=4039.00, symbol=SYMBOL,
        runtime={"zone_first_touch_at": "2026-07-30T09:10:00+00:00"},
        now=datetime(2026, 7, 30, 9, 25, tzinfo=timezone.utc),
    )
    assert review["activate"] is True
    assert review["fill_price"] == 4045.64, "a SELL fills at the LOWER edge"
    planned_risk = abs(4047.76 - 4062.76)
    actual_risk = abs(review["fill_price"] - review["stop_loss"])
    assert abs(actual_risk - planned_risk) < 0.05


def test_a_non_planner_order_is_untouched() -> None:
    manager = OpenTradesManager(_config())
    orphan = _trade(signal_snapshot={
        "signal": {"entry": {"price": ENTRY, "low": ZONE_LOW, "high": ZONE_HIGH}}
    })
    review = manager._zone_touch_review(
        orphan, trade_type="BUY", order_type="BUY_LIMIT", entry=ENTRY,
        stop_loss=STOP, tp2=TP2, current_price=PRICE_AFTER,
        candle_high=4068.17, candle_low=TOUCH_LOW,
        recent_window_high=4068.17, recent_window_low=TOUCH_LOW, symbol=SYMBOL,
    )
    assert review["activate"] is False


def test_an_order_with_no_published_zone_is_untouched() -> None:
    manager = OpenTradesManager(_config())
    flat = _trade(signal_snapshot={
        "setup_context": {"pending_plan_role": "PRIMARY"},
        "signal": {"entry": {"price": ENTRY}},
    })
    review = manager._zone_touch_review(
        flat, trade_type="BUY", order_type="BUY_LIMIT", entry=ENTRY,
        stop_loss=STOP, tp2=TP2, current_price=PRICE_AFTER,
        candle_high=4068.17, candle_low=TOUCH_LOW,
        recent_window_high=4068.17, recent_window_low=TOUCH_LOW, symbol=SYMBOL,
    )
    assert review["activate"] is False


def test_it_is_configurable_from_config_json() -> None:
    zta = _config()["order_execution"]["zone_touch_activation"]
    assert zta["enabled"] is True
    assert zta["require_exit_in_favour"] is True
    assert zta["preserve_planned_risk"] is True
    assert float(zta["min_remaining_rr"]) == 1.5


def test_disabling_it_restores_the_old_behaviour() -> None:
    cfg = _config()
    cfg["order_execution"]["zone_touch_activation"]["enabled"] = False
    result = _evaluate(OpenTradesManager(cfg), _trade(), PRICE_AFTER, 4068.17, TOUCH_LOW)
    assert result["new_status"] == "PENDING"


# ── the mapped entry gets first refusal ────────────────────────────────────

def _cycle(manager, trade, price, high, low, minutes, t0=None):
    from datetime import timedelta
    t0 = t0 or datetime(2026, 7, 30, 9, 10, tzinfo=timezone.utc)
    result = manager.evaluate_trade(
        trade, price, now=t0 + timedelta(minutes=minutes),
        candle_high=high, candle_low=low,
        recent_candles=[{"time": "x", "open": price, "high": high, "low": low, "close": price}],
    )
    updates = result["updates"]
    carried = {k: v for k, v in updates.items() if k in ("signal_snapshot", "pending_cycles")}
    return result, {**trade, **carried}


def test_the_mapped_entry_is_given_two_cycles_before_the_edge_is_used() -> None:
    """Sitting inside the zone is not enough: the real entry gets its chance."""
    manager = OpenTradesManager(_config())
    trade = _trade()

    # cycle 1 -- inside the zone, reference entry untouched
    r1, trade = _cycle(manager, trade, 4061.00, 4061.50, 4060.40, 0)
    assert r1["new_status"] == "PENDING"

    # cycle 2 -- five minutes later, still inside
    r2, trade = _cycle(manager, trade, 4061.20, 4061.80, 4060.60, 5)
    assert r2["new_status"] == "PENDING"

    # cycle 3 -- ten minutes in, price leaves the zone upward
    r3, trade = _cycle(manager, trade, PRICE_AFTER, 4068.17, 4060.40, 10)
    assert r3["new_status"] == "OPEN"
    assert r3["updates"]["entry_price"] == ZONE_HIGH


def test_leaving_the_zone_inside_the_grace_window_still_waits() -> None:
    manager = OpenTradesManager(_config())
    trade = _trade()
    r1, trade = _cycle(manager, trade, 4061.00, 4061.50, 4060.40, 0)
    assert r1["new_status"] == "PENDING"
    # Leaves the zone after only 5 minutes -- inside the 10-minute grace.
    r2, trade = _cycle(manager, trade, PRICE_AFTER, 4068.17, 4061.00, 5)
    assert r2["new_status"] == "PENDING", "the mapped entry still had time"


# ── the departure is a fact, not a live reading ────────────────────────────

def _departed_trade() -> dict:
    trade = _trade()
    trade["signal_snapshot"]["pending_runtime"] = {
        "zone_first_touch_at": "2026-07-30T09:10:00+00:00",
        "zone_left_in_favour": True,
        "zone_left_at": "2026-07-30T09:20:00+00:00",
        "creation_price": 4066.00,
    }
    return trade


def test_fill_is_at_the_edge_wherever_price_sits_when_noticed() -> None:
    """Your rule: once it left the zone in favour, the edge is the fill.

    The five-minute cycle may only notice the departure after price has
    pulled back. The event already happened; where price sits on the cycle
    that observes it is not part of the test.
    """
    from datetime import timedelta
    for price, high, low in (
        (4066.26, 4067.00, 4065.00),   # still above the edge
        (4060.50, 4061.00, 4060.00),   # pulled back below the edge
        (4058.90, 4059.50, 4058.50),   # all the way back inside the zone
    ):
        manager = OpenTradesManager(_config())
        result = manager.evaluate_trade(
            _departed_trade(), price,
            now=datetime(2026, 7, 30, 9, 25, tzinfo=timezone.utc),
            candle_high=high, candle_low=low,
            recent_candles=[{"time": "x", "open": price, "high": high, "low": low, "close": price}],
        )
        assert result["new_status"] == "OPEN", f"price {price} should still activate"
        assert result["updates"]["entry_price"] == ZONE_HIGH


# ── the 60-point published zone floor ──────────────────────────────────────

def test_published_zones_are_widened_to_the_configured_floor() -> None:
    """A 42-point area is too tight to be reachable; widen, never refuse."""
    from services.session_planner import SessionPlannerService

    planner = SessionPlannerService(_config())
    floor = planner.min_entry_zone_width_points
    assert floor >= 60

    # the live SELL map from 2026-07-30
    low, high, widened = planner._enforce_min_zone_width(
        4045.64, 4049.88, entry_price=4047.76, symbol=SYMBOL,
    )
    assert widened is True
    assert round((high - low) / 0.1) >= floor
    assert low <= 4047.76 <= high, "the reference entry must stay inside its own zone"


def test_a_zone_already_wide_enough_is_left_alone() -> None:
    from services.session_planner import SessionPlannerService

    planner = SessionPlannerService(_config())
    low, high, widened = planner._enforce_min_zone_width(
        ZONE_LOW, ZONE_HIGH, entry_price=ENTRY, symbol=SYMBOL,
    )
    assert widened is False
    assert (low, high) == (ZONE_LOW, ZONE_HIGH)


def test_widening_is_recorded_on_the_payload() -> None:
    from services.session_planner import SessionPlannerService

    planner = SessionPlannerService(_config())
    payload = planner._zone_payload({
        "poi_zone": {"bottom": 4045.64, "top": 4049.88},
        "entry_price": 4047.76, "poi_type": "ob", "symbol": SYMBOL,
    })
    assert payload["widened_to_min_width"] is True
    assert payload["original_low"] == 4045.64
    assert round((payload["high"] - payload["low"]) / 0.1) >= 60
