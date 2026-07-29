"""Coverage for the cross-path distance filter.

This filter decides whether a new order may be opened alongside an existing
one in the same direction, and it is the reason a system cannot stack five
BUYs on top of each other during a trend. It shipped with no test of any kind:
a refactor could have inverted its comparison and 730 green tests would have
said nothing.

Two rules are enforced together:
  - a minimum gap between the new entry and the existing one;
  - direction sense -- add to a BUY only lower, to a SELL only higher.
"""

from __future__ import annotations

import json
from pathlib import Path

import scripts.run_analysis as ra

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


class _Database:
    def __init__(self, open_trades=None):
        self._trades = open_trades or []

    def get_open_trades(self):
        return self._trades


def _trade(side="BUY", entry=4000.0, symbol="XAU/USD"):
    return {"type": side, "entry_price": entry, "symbol": symbol, "status": "OPEN"}


def _decision(side="BUY", entry=4000.0, symbol="XAU/USD"):
    return {
        "decision": side,
        "symbol": symbol,
        "current_price": entry,
        "signal": {"entry": {"price": entry}},
    }


def _check(decision, trades, distance=200):
    return ra._cross_path_distance_check(
        decision, _Database(trades), CONFIG, cross_distance_points=distance
    )


# --- the gap rule -------------------------------------------------------

def test_entry_too_close_to_an_existing_buy_is_blocked() -> None:
    """10 points apart against a 200 point requirement."""
    reason = _check(_decision("BUY", 3999.0), [_trade("BUY", 4000.0)])

    assert reason is not None
    assert "only 10 pts" in reason


def test_entry_far_enough_below_an_existing_buy_is_allowed() -> None:
    """Buying the dip: lower and beyond the gap."""
    assert _check(_decision("BUY", 3970.0), [_trade("BUY", 4000.0)]) is None


def test_entry_far_enough_above_an_existing_sell_is_allowed() -> None:
    """Selling the rally: higher and beyond the gap."""
    assert _check(_decision("SELL", 4030.0), [_trade("SELL", 4000.0)]) is None


# --- the direction rule -------------------------------------------------

def test_buying_above_an_existing_buy_is_blocked() -> None:
    """Distance alone is not enough; adds must improve the average."""
    reason = _check(_decision("BUY", 4040.0), [_trade("BUY", 4000.0)])

    assert reason is not None
    assert "not lower" in reason


def test_selling_below_an_existing_sell_is_blocked() -> None:
    reason = _check(_decision("SELL", 3960.0), [_trade("SELL", 4000.0)])

    assert reason is not None
    assert "not higher" in reason


# --- scope --------------------------------------------------------------

def test_opposite_direction_trades_are_ignored() -> None:
    """A SELL nearby says nothing about whether a BUY may open."""
    assert _check(_decision("BUY", 4001.0), [_trade("SELL", 4000.0)]) is None


def test_other_symbols_are_ignored() -> None:
    trades = [_trade("BUY", 4000.0, symbol="XAG/USD")]
    assert _check(_decision("BUY", 4001.0), trades) is None


def test_no_open_trades_means_no_objection() -> None:
    assert _check(_decision("BUY", 4000.0), []) is None


def test_wait_decisions_are_not_evaluated() -> None:
    assert _check(_decision("WAIT", 4000.0), [_trade("BUY", 4000.0)]) is None


def test_missing_entry_price_is_not_treated_as_zero() -> None:
    """A malformed decision must not be silently approved against 0.00."""
    decision = {"decision": "BUY", "symbol": "XAU/USD", "signal": {}}
    assert _check(decision, [_trade("BUY", 4000.0)]) is None


def test_the_configured_distance_is_respected() -> None:
    """A 300 point requirement must refuse what 200 would allow."""
    decision = _decision("BUY", 3975.0)
    trades = [_trade("BUY", 4000.0)]

    assert _check(decision, trades, distance=200) is None
    assert _check(decision, trades, distance=300) is not None


def test_every_open_trade_must_be_cleared_not_just_the_nearest() -> None:
    """The filter refuses on the first conflict it finds, and should.

    A new BUY at 3995 is comfortably below the 4000 position, but it is far
    *above* the 3500 one -- so it worsens that position's average. Passing the
    nearest trade is not sufficient; the entry has to be defensible against
    every open position in the same direction.
    """
    trades = [_trade("BUY", 3500.0), _trade("BUY", 4000.0)]
    reason = _check(_decision("BUY", 3995.0), trades)

    assert reason is not None
    assert "3500.00" in reason
    assert "not lower" in reason


def test_an_entry_clearing_all_open_trades_is_allowed() -> None:
    trades = [_trade("BUY", 3500.0), _trade("BUY", 4000.0)]
    assert _check(_decision("BUY", 3470.0), trades) is None
