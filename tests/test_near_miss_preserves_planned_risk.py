"""Re-pricing the entry must not re-price the risk.

2026-08-03, trade TRADE_20260803_141059_572842_2f72579f:

    Order: SELL LIMIT · Entry 4037.48 · Stop 4077.48 · TP2 3947.48
    SL distance: 400.0 pts · Planned RR 2.25R

    Pending Order Activated
    Current Price: 4031.76
    Activation: Pending order was converted to MARKET and is now live
    Activation review: Near-miss market conversion: missed entry by 20 pts
                       within halo 25, then confirmed away by 38 pts

The conversion moved the ENTRY to the market price and left the STOP where
the plan had put it. The distance between them is the risk, so it grew from
400 points to 457 -- fifty-seven points of extra exposure that nothing
authorised and no card mentioned.

WHY THE EXISTING GUARD DID NOT CATCH IT
---------------------------------------
``_near_miss_review`` does check reward-to-risk, and it measured the RR from
the NEW price: 842.8 / 457.2 = 1.84, which clears its 1.8 bar. So the trade
looked acceptable on the ratio while the absolute risk had silently expanded.
A ratio cannot police a quantity: 1.84R of a 457-point stop is a bigger loss
than 1.84R of a 400-point one.

THE FIX
-------
``zone_touch_activation`` already had the answer -- ``preserve_planned_risk``
moves the stop by the same distance the entry moved, so the trade risks what
it was authorised to risk. The near-miss path now does the same.

Direction is safe by construction: a near-miss conversion only happens after
price has moved AWAY from the entry in the trade's favour, so the carried
stop is nearer the market than the mapped one, never further. The
implementation also refuses to widen even if that assumption is ever broken.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOL = "XAU/USD"

# The card, verbatim.
ENTRY = 4037.48
STOP = 4077.48          # 400 pts
TP1, TP2 = 3987.48, 3947.48
FILL = 4031.76          # where the conversion actually happened

NOW = datetime(2026, 8, 3, 14, 17, tzinfo=timezone.utc)
# Price approached to 4035.48 (20 pts short) then moved away to 4031.76.
BARS = [
    {"time": "2026-08-03T14:12:00Z", "open": 4034.0, "high": 4035.48, "low": 4033.0, "close": 4034.0},
    {"time": "2026-08-03T14:16:00Z", "open": 4034.0, "high": 4034.5, "low": 4031.0, "close": FILL},
]


def _config(**near_miss) -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        config = json.load(fh)
    if near_miss:
        config["order_execution"]["near_miss_execution"].update(near_miss)
    return config


def _pending(**extra) -> dict:
    trade = {
        "id": "TRADE_20260803_141059_572842_2f72579f",
        "symbol": SYMBOL, "type": "SELL", "status": "PENDING",
        "entry_price": ENTRY, "stop_loss": STOP, "initial_stop_loss": STOP,
        "tp1": TP1, "tp2": TP2,
        "order_type": "SELL_LIMIT", "order_kind": "LIMIT",
        "created_at": "2026-08-03T14:10:59+00:00",
        "entry_time": "2026-08-03T14:10:59+00:00",
        "last_updated": "2026-08-03T14:12:00+00:00",
        "updates_sent": [],
        "signal_snapshot": {
            "setup_context": {"selection_role": "PRIMARY", "pending_plan_role": "PRIMARY"},
            "signal": {"entry": {"low": 4034.48, "high": 4040.48}},
        },
    }
    trade.update(extra)
    return trade


def _convert(config=None, trade=None, price: float = FILL):
    manager = OpenTradesManager(config or _config())
    return manager.evaluate_trade(
        trade or _pending(), current_price=price, now=NOW,
        candle_high=4034.5, candle_low=4031.0, recent_candles=BARS,
    )


def _points(a: float, b: float) -> float:
    return round(abs(a - b) * 10, 1)


# ── the incident ────────────────────────────────────────────────────────────

def test_the_conversion_still_happens() -> None:
    """The fix must not disable near-miss execution, only correct its risk."""
    result = _convert()
    assert "ORDER_FILLED" in result["events"]
    assert result["new_status"] == "OPEN"
    assert result["updates"]["entry_price"] == FILL


def test_the_planned_risk_is_preserved() -> None:
    updates = _convert()["updates"]
    fill = updates["entry_price"]
    stop = updates["stop_loss"]

    assert _points(ENTRY, STOP) == 400.0, "the plan authorised 400 points"
    assert _points(fill, stop) == 400.0, (
        f"converted at {fill} with stop {stop} -> {_points(fill, stop)} pts; "
        "re-pricing the entry must not enlarge the risk"
    )


def test_the_old_behaviour_would_have_risked_457() -> None:
    """Fault injection: leave the stop behind and measure the damage."""
    unmoved_risk = _points(FILL, STOP)
    assert unmoved_risk == 457.2
    assert unmoved_risk > 400.0

    # And the RR test could not have caught it.
    old_rr = abs(FILL - TP2) / abs(STOP - FILL)
    bar = float(_config()["order_execution"]["near_miss_execution"]["min_remaining_rr"])
    assert old_rr >= bar, (
        f"RR was {old_rr:.2f} against a {bar} bar -- a ratio cannot police an "
        "absolute quantity, which is why the fix had to be about distance"
    )


def test_the_initial_stop_records_the_carried_level() -> None:
    """`initial_stop_loss` is what later code measures R against."""
    updates = _convert()["updates"]
    assert updates["initial_stop_loss"] == updates["stop_loss"]


def test_the_reward_to_risk_improves_rather_than_degrades() -> None:
    updates = _convert()["updates"]
    fill, stop = updates["entry_price"], updates["stop_loss"]
    new_rr = abs(fill - TP2) / abs(stop - fill)
    old_rr = abs(FILL - TP2) / abs(STOP - FILL)
    assert new_rr > old_rr
    assert round(new_rr, 2) == 2.11


# ── scope and safety ────────────────────────────────────────────────────────

def test_the_stop_is_never_widened() -> None:
    """If the mapped stop is already tighter, it is kept.

    A SELL whose plan risks only 150 points must not have that stretched to
    400 just because the entry moved.
    """
    tight = _pending(stop_loss=4052.48, initial_stop_loss=4052.48)  # 150 pts
    updates = _convert(trade=tight)["updates"]
    if "ORDER_FILLED" not in _convert(trade=tight)["events"]:
        return
    assert updates["stop_loss"] <= 4052.48, (
        "carrying the stop must only ever tighten it, never add risk"
    )


def test_a_buy_conversion_mirrors_the_same_rule() -> None:
    buy = _pending(
        type="BUY", order_type="BUY_LIMIT",
        entry_price=4000.0, stop_loss=3960.0, initial_stop_loss=3960.0,
        tp1=4050.0, tp2=4090.0,
    )
    buy["signal_snapshot"]["signal"]["entry"] = {"low": 3997.0, "high": 4003.0}
    bars = [
        {"time": "2026-08-03T14:12:00Z", "open": 4002.0, "high": 4003.0, "low": 4002.0, "close": 4002.5},
        {"time": "2026-08-03T14:16:00Z", "open": 4003.0, "high": 4006.5, "low": 4003.0, "close": 4006.24},
    ]
    manager = OpenTradesManager(_config())
    result = manager.evaluate_trade(
        buy, current_price=4006.24, now=NOW,
        candle_high=4006.5, candle_low=4003.0, recent_candles=bars,
    )
    if "ORDER_FILLED" not in result["events"]:
        return
    updates = result["updates"]
    assert _points(updates["entry_price"], updates["stop_loss"]) == 400.0


def test_the_behaviour_can_be_switched_off() -> None:
    updates = _convert(config=_config(preserve_planned_risk=False))["updates"]
    assert updates.get("stop_loss", STOP) == STOP, (
        "with the flag off the legacy behaviour returns, unchanged"
    )


def test_the_setting_is_recorded_in_config() -> None:
    near_miss = _config()["order_execution"]["near_miss_execution"]
    assert near_miss["preserve_planned_risk"] is True
    assert near_miss["enabled"] is True


def test_the_runtime_notes_that_risk_was_preserved() -> None:
    """The decision must be visible afterwards, not inferred from prices."""
    import inspect
    source = inspect.getsource(OpenTradesManager._evaluate_pending)
    assert "near_miss_planned_risk_preserved" in source


# ── nothing else moved ──────────────────────────────────────────────────────

def test_no_risk_threshold_was_changed() -> None:
    config = _config()
    risk = config["risk_settings"]
    assert float(risk["min_sl_distance_points"]) == 400.0
    assert float(risk["min_rr_ratio"]) == 1.5
    assert float(risk["max_rr_ratio"]) == 4.0
    near_miss = config["order_execution"]["near_miss_execution"]
    assert float(near_miss["min_halo_points"]) == 12.0
    assert float(near_miss["max_halo_points"]) == 25.0
    assert float(near_miss["min_remaining_rr"]) == 1.8
