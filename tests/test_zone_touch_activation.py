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
    return manager.evaluate_trade(
        trade, price, now=datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc),
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
