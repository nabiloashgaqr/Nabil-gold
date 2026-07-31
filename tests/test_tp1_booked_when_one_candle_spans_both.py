"""One candle reaching TP1 and TP2 together must still book the TP1 half.

2026-07-31, trade TRADE_20260731_060253_794033_d917b1d5 (SELL 4074.78,
TP1 4051.98, TP2 4029.17):

    Take Profit 2 Hit
    Status: OPEN → TP2_HIT
    Actual PnL: +456.1 pts

Two things are wrong in that card and they share one cause.

  * "OPEN → TP2_HIT" -- TP1 was taken on the way, so the trade should have
    passed through TP1_HIT. No TP1 event was ever recorded.
  * "+456.1" -- the full position settled at the TP2 price, when half had
    already left at 4051.98. The account made +342.1.

WHY
---
The outcome branches are an if/elif chain and ``tp2_touched`` is tested
before ``tp1_touched``. A single 5-minute bar fell 4055 → 4023, crossing
both levels, so the TP2 branch fired and the TP1 branch was never reached.
The partial booking added for the ordinary two-candle path lives inside that
skipped branch, so it never ran either.

This is NOT a Telegram formatting problem and not a database lag. The status
was not late -- it was never written, because TP1 never executed. Patching
the card would have hidden a 114-point error in `final_pnl`, which is the
number the scoreboard, the weekly report and the learning service consume.

THE RULE
--------
Price cannot reach TP2 without passing through TP1, so the TP1 fill is not
an assumption -- it is the only order in which those two levels can be
touched. When one bar spans both, the TP1 half is booked at the TP1 price
and the remainder settles at TP2, producing exactly the same arithmetic as
the two-candle path.

Scope is narrow: it applies only when TP1 lies between the entry and TP2,
only when nothing was booked yet, and only when partial closing is enabled.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager  # noqa: E402
from utils.helpers import calculate_pips, load_config  # noqa: E402

ENTRY = 4074.78
TP1 = 4051.98
TP2 = 4029.17
SYMBOL = "XAU/USD"
NOW = datetime(2026, 7, 31, 13, 50, tzinfo=timezone.utc)

# The bar that carried both targets at once.
SPANNING_BAR = {
    "time": "2026-07-31T13:49:00Z",
    "open": 4054.00, "high": 4055.00, "low": 4023.00, "close": 4023.21,
}

FULL_SIZE_AT_TP2 = round(calculate_pips(ENTRY, TP2, "SELL", SYMBOL), 1)
HONEST_SETTLEMENT = round(
    0.5 * calculate_pips(ENTRY, TP1, "SELL", SYMBOL)
    + 0.5 * calculate_pips(ENTRY, TP2, "SELL", SYMBOL),
    1,
)


def _manager(config=None) -> OpenTradesManager:
    return OpenTradesManager(config or load_config())


def _trade(**extra) -> dict:
    trade = {
        "id": "TRADE_20260731_060253_794033_d917b1d5",
        "symbol": SYMBOL, "type": "SELL", "status": "OPEN",
        "entry_price": ENTRY, "stop_loss": ENTRY, "initial_stop_loss": 4089.78,
        "tp1": TP1, "tp2": TP2,
        "entry_time": "2026-07-31T06:02:53+00:00",
        "created_at": "2026-07-31T06:02:53+00:00",
        "last_updated": "2026-07-31T13:40:00+00:00",
        "sl_moved_to_entry": True, "partial_close": False,
        "max_favorable_excursion": 250.0, "max_adverse_excursion": -69.3,
        "updates_sent": ["ORDER_FILLED"], "signal_snapshot": {},
    }
    trade.update(extra)
    return trade


def _settle(manager: OpenTradesManager, trade: dict) -> dict:
    return manager.evaluate_trade(
        trade, current_price=4023.21, now=NOW,
        candle_high=SPANNING_BAR["high"], candle_low=SPANNING_BAR["low"],
        recent_candles=[SPANNING_BAR],
    )


def test_the_numbers_this_file_defends() -> None:
    assert FULL_SIZE_AT_TP2 == 456.1, "the figure the card printed"
    assert HONEST_SETTLEMENT == 342.1, "the figure the account received"
    assert round(FULL_SIZE_AT_TP2 - HONEST_SETTLEMENT, 1) == 114.0


def test_one_candle_spanning_both_targets_books_the_tp1_half() -> None:
    result = _settle(_manager(), _trade())

    assert "TP2_HIT" in result["events"]
    assert "TP1_HIT" in result["events"], (
        "price cannot reach TP2 without crossing TP1; skipping the event "
        "loses the half that left at 4051.98"
    )
    assert result["updates"]["partial_close"] is True
    assert result["updates"]["final_pnl"] == HONEST_SETTLEMENT
    assert result["updates"]["final_pnl"] != FULL_SIZE_AT_TP2


def test_the_two_candle_path_gives_the_identical_number() -> None:
    """The fix must reconcile the two paths, not create a third answer."""
    manager = _manager()

    tp1_only = manager.evaluate_trade(
        _trade(), current_price=4051.00,
        now=datetime(2026, 7, 31, 13, 45, tzinfo=timezone.utc),
        candle_high=4058.00, candle_low=4050.00,
        recent_candles=[{"time": "2026-07-31T13:44:00Z", "open": 4057.0,
                         "high": 4058.00, "low": 4050.00, "close": 4051.0}],
    )["updates"]

    two_candle = manager.evaluate_trade(
        _trade(
            status="TP1_HIT", partial_close=True,
            closed_fraction=tp1_only["closed_fraction"],
            realized_pnl_points=tp1_only["realized_pnl_points"],
            max_favorable_excursion=400.0,
        ),
        current_price=4023.21, now=NOW,
        candle_high=4035.00, candle_low=4023.00,
        recent_candles=[{"time": "2026-07-31T13:49:00Z", "open": 4034.0,
                         "high": 4035.00, "low": 4023.00, "close": 4023.21}],
    )["updates"]

    one_candle = _settle(_manager(), _trade())["updates"]

    assert two_candle["final_pnl"] == one_candle["final_pnl"] == HONEST_SETTLEMENT, (
        "whether the two targets arrive on one bar or two is an artefact of "
        "the data feed; the account result must not depend on it"
    )


def test_an_already_booked_half_is_not_booked_twice() -> None:
    """The guard against the opposite error: double counting TP1."""
    result = _settle(
        _manager(),
        _trade(status="TP1_HIT", partial_close=True,
               closed_fraction=0.5, realized_pnl_points=114.0),
    )

    assert result["updates"]["final_pnl"] == HONEST_SETTLEMENT
    assert result["updates"]["closed_fraction"] == 1.0


def test_a_bar_that_reaches_only_tp2_is_untouched() -> None:
    """No TP1 in range means nothing to book -- full size at TP2."""
    result = _settle(_manager(), _trade(tp1=0.0))

    assert "TP1_HIT" not in result["events"]
    assert result["updates"]["final_pnl"] == FULL_SIZE_AT_TP2


def test_a_malformed_plan_with_tp1_beyond_tp2_is_left_alone() -> None:
    """TP1 further than TP2 is not a first target; do not invent a fill."""
    result = _settle(_manager(), _trade(tp1=4010.00))

    assert "TP1_HIT" not in result["events"]
    assert result["updates"]["final_pnl"] == FULL_SIZE_AT_TP2


def test_disabling_partial_close_restores_full_size() -> None:
    config = load_config()
    config.setdefault("trade_management", {})["partial_close_at_tp1"] = False

    result = _settle(_manager(config), _trade())

    assert result["updates"]["final_pnl"] == FULL_SIZE_AT_TP2
    assert result["updates"].get("closed_fraction") is None


def test_a_buy_trade_behaves_the_same_way() -> None:
    """The fault is directional-agnostic; so is the fix."""
    entry, tp1, tp2 = 4000.00, 4022.80, 4045.61
    bar = {"time": "2026-07-31T13:49:00Z", "open": 4001.0,
           "high": 4046.00, "low": 3999.00, "close": 4045.80}

    result = _manager().evaluate_trade(
        {
            "id": "buy-case", "symbol": SYMBOL, "type": "BUY", "status": "OPEN",
            "entry_price": entry, "stop_loss": entry, "initial_stop_loss": 3985.0,
            "tp1": tp1, "tp2": tp2,
            "entry_time": "2026-07-31T06:02:53+00:00",
            "created_at": "2026-07-31T06:02:53+00:00",
            "last_updated": "2026-07-31T13:40:00+00:00",
            "sl_moved_to_entry": True, "partial_close": False,
            "max_favorable_excursion": 200.0,
            "updates_sent": ["ORDER_FILLED"], "signal_snapshot": {},
        },
        current_price=4045.80, now=NOW,
        candle_high=bar["high"], candle_low=bar["low"], recent_candles=[bar],
    )

    expected = round(
        0.5 * calculate_pips(entry, tp1, "BUY", SYMBOL)
        + 0.5 * calculate_pips(entry, tp2, "BUY", SYMBOL), 1
    )
    assert "TP1_HIT" in result["events"]
    assert result["updates"]["final_pnl"] == expected


def test_fault_injection_the_elif_chain_skipped_tp1() -> None:
    """Reproduce the pre-fix control flow and show it loses the half.

    This mirrors the branch order in ``evaluate_trade``: TP2 is tested first,
    so a bar that touched both never evaluates the TP1 branch.
    """
    tp2_touched = SPANNING_BAR["low"] <= TP2
    tp1_touched = SPANNING_BAR["low"] <= TP1
    assert tp1_touched and tp2_touched, "the bar genuinely crossed both levels"

    booked = None
    if tp2_touched:                      # the pre-fix chain, verbatim
        settled = calculate_pips(ENTRY, TP2, "SELL", SYMBOL)
    elif tp1_touched:                    # unreachable on this bar
        booked = 0.5 * calculate_pips(ENTRY, TP1, "SELL", SYMBOL)
        settled = None

    assert booked is None, "the TP1 branch is dead whenever TP2 is touched too"
    assert round(settled, 1) == FULL_SIZE_AT_TP2 == 456.1, (
        "the old chain settles the full position at TP2 and reports the "
        "number the card printed"
    )
