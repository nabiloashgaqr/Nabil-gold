"""A winning trade the agents have turned against must defend more of its gain.

The exit design is deliberate: the candle must break before the agent vote is
even read, so a thesis is never killed by an opinion the price action has not
confirmed. That rule saved 5f383b5c, where three qualified agents were still
defending a SELL the candle rule wanted to close.

But holding used to mean holding at the full trailing gap. Measured on the
live BUY runner:

    +191 pts open, stop at breakeven, five qualified agents reading SELL,
    candle calm  ->  trailing stop 4068.00, only 59 pts defended,
                     132 pts left exposed

Tightening the gap instead of closing the trade keeps the thesis alive and
defends the gain: 4077.00, 149 pts defended, 42 exposed.

This can never add risk. The trailing stop only ever moves in the profitable
direction, and the rule requires the stop to already sit at or beyond
breakeven before it engages.

Fault injection: set tighten_trail_on_reversal False, or raise
reversal_trail_distance_points to the normal gap, and the headline test fails.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOL = "XAU/USD"
ENTRY = 4062.05


def _config() -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _book(direction: str, *names_conf) -> dict:
    return {n: {"direction": direction, "confidence": c} for n, c in names_conf}


SELL_BOOK = _book("SELL", ("technical", 90), ("classical", 85), ("smc", 92),
                  ("price_action", 88), ("multitimeframe", 91))


def _runner(**over) -> dict:
    """The live BUY: +191 pts, stop already carried to breakeven."""
    trade = {
        "id": "T", "type": "BUY", "status": "OPEN", "symbol": SYMBOL,
        "entry_price": ENTRY, "stop_loss": ENTRY, "initial_stop_loss": 4043.57,
        "tp1": 4075.79, "tp2": 4093.31,
        "partial_close": True, "sl_moved_to_entry": True,
        "management_phase": "POST_TP1_TRAILING",
        "updates_sent": ["ORDER_FILLED", "TP1_HIT", "MOVE_SL_TO_BE"],
        "entry_time": "2026-07-30T09:20:00+00:00",
        "created_at": "2026-07-30T09:05:48+00:00",
        "max_favorable_excursion": 191.0, "current_pnl_points": 191.0,
    }
    trade.update(over)
    return trade


def _calm_candles() -> list[dict]:
    """Drifting, but no bearish reclaim -- the exit trigger must NOT fire."""
    return [
        {"time": "a", "open": 4082.0, "high": 4083.0, "low": 4079.0, "close": 4081.00},
        {"time": "b", "open": 4081.0, "high": 4081.5, "low": 4080.0, "close": 4081.14},
    ]


def _evaluate(book, config=None, trade=None):
    manager = OpenTradesManager(config or _config())
    return manager.evaluate_trade(
        trade or _runner(), 4081.14,
        now=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        candle_high=4081.5, candle_low=4080.0,
        recent_candles=_calm_candles(), agent_details=book,
    )


def _defended(result) -> float:
    return round((result["updates"]["stop_loss"] - ENTRY) / 0.1, 1)


# ── the headline ───────────────────────────────────────────────────────────

def test_a_reversed_book_defends_more_of_the_gain() -> None:
    baseline = _defended(_evaluate(None))
    tightened = _defended(_evaluate(SELL_BOOK))
    assert tightened > baseline, (
        f"the agent reversal defended {tightened} pts, no better than the "
        f"{baseline} pts a silent book already defended"
    )
    # 60-pt gap from 4081.14 -> 4075.14, capped by the step logic to 4077.00
    assert tightened >= 140


def test_the_trade_is_not_closed_by_the_tightening() -> None:
    """Holding the thesis is the point: only the defence changes."""
    result = _evaluate(SELL_BOOK)
    assert result["new_status"] == "OPEN"
    assert "MANUAL_CLOSE" not in result["events"]
    assert "THESIS_EXIT" not in result["events"]


def test_it_is_reported_on_the_update() -> None:
    updates = _evaluate(SELL_BOOK)["updates"]
    assert updates.get("reversal_trail_active") is True
    assert float(updates.get("reversal_trail_points")) == 60.0


# ── it must not fire where it would be wrong ───────────────────────────────

def test_a_supportive_book_leaves_the_trail_alone() -> None:
    buy_book = _book("BUY", ("technical", 90), ("smc", 92), ("price_action", 88))
    assert _defended(_evaluate(buy_book)) == _defended(_evaluate(None))


def test_a_split_book_leaves_the_trail_alone() -> None:
    """Only a clean CONFIRM tightens: a contested read is not evidence."""
    split = {**_book("SELL", ("technical", 90), ("smc", 92)),
             **_book("BUY", ("classical", 85), ("multitimeframe", 88))}
    assert _defended(_evaluate(split)) == _defended(_evaluate(None))


def test_a_losing_trade_is_untouched() -> None:
    """No gain to defend, and the stop is still real risk. Leave it alone."""
    losing = _runner(stop_loss=4043.57, sl_moved_to_entry=False,
                     partial_close=False, current_pnl_points=-50.0,
                     updates_sent=["ORDER_FILLED"], management_phase="DEFENSIVE")
    result = _evaluate(SELL_BOOK, trade=losing)
    assert result["updates"].get("reversal_trail_active") is None


def test_a_trade_not_yet_at_breakeven_is_untouched() -> None:
    exposed = _runner(stop_loss=4055.00, sl_moved_to_entry=False)
    result = _evaluate(SELL_BOOK, trade=exposed)
    assert result["updates"].get("reversal_trail_active") is None


def test_no_agent_book_behaves_exactly_as_before() -> None:
    for absent in (None, {}):
        assert _evaluate(absent)["updates"].get("reversal_trail_active") is None


# ── the stop can only ever improve ─────────────────────────────────────────

def test_the_stop_never_moves_against_the_trade() -> None:
    """The whole safety argument in one assertion."""
    for book in (None, SELL_BOOK):
        result = _evaluate(book)
        assert result["updates"]["stop_loss"] >= ENTRY, (
            "a tightened trail must never place the stop below breakeven"
        )


def test_a_sell_runner_mirrors_the_behaviour() -> None:
    config = _config()
    sell = {
        "id": "S", "type": "SELL", "status": "OPEN", "symbol": SYMBOL,
        "entry_price": 4100.00, "stop_loss": 4100.00, "initial_stop_loss": 4140.00,
        "tp1": 4080.00, "tp2": 4060.00, "partial_close": True,
        "sl_moved_to_entry": True, "management_phase": "POST_TP1_TRAILING",
        "updates_sent": ["ORDER_FILLED", "TP1_HIT", "MOVE_SL_TO_BE"],
        "entry_time": "2026-07-30T09:20:00+00:00",
        "created_at": "2026-07-30T09:05:48+00:00",
        "max_favorable_excursion": 191.0, "current_pnl_points": 191.0,
    }
    buy_book = _book("BUY", ("technical", 90), ("classical", 85), ("smc", 92),
                     ("price_action", 88), ("multitimeframe", 91))
    manager = OpenTradesManager(config)
    calm = [
        {"time": "a", "open": 4079.0, "high": 4082.0, "low": 4078.0, "close": 4081.00},
        {"time": "b", "open": 4081.0, "high": 4082.0, "low": 4080.5, "close": 4080.86},
    ]
    plain = manager.evaluate_trade(
        dict(sell), 4080.86, now=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        candle_high=4082.0, candle_low=4080.5, recent_candles=calm, agent_details=None,
    )
    turned = manager.evaluate_trade(
        dict(sell), 4080.86, now=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        candle_high=4082.0, candle_low=4080.5, recent_candles=calm, agent_details=buy_book,
    )
    plain_stop = plain["updates"]["stop_loss"]
    turned_stop = turned["updates"]["stop_loss"]
    assert turned_stop < plain_stop, "a SELL defends by moving its stop DOWN"
    assert turned_stop <= 4100.00


# ── configurable ───────────────────────────────────────────────────────────

def test_the_distance_is_configurable_and_can_be_disabled() -> None:
    config = _config()
    vote = config["trade_management"]["thesis_exit"]["agent_vote"]
    assert vote["tighten_trail_on_reversal"] is True
    assert float(vote["reversal_trail_distance_points"]) == 60.0

    config["trade_management"]["thesis_exit"]["agent_vote"]["tighten_trail_on_reversal"] = False
    assert _evaluate(SELL_BOOK, config=config)["updates"].get("reversal_trail_active") is None
