"""A resting order must be reachable, and must not bet against a live winner.

2026-07-31, 13:16:34 UTC. Trade TRADE_20260731_131634_538414_6e31ddf6 was
published as a SELL LIMIT at 4076.93 while the market stood at 4023.21 --
537 points BELOW it and collapsing. At that same moment the system's own
SELL d917b1d5 (entry 4074.78) was live, +399.6 points open, minutes from
taking TP2 at 4029.17.

So the order could only ever fill on a 537-point rally: precisely the move
that would have destroyed the winning trade the system was already holding.
It was a hedge against its own correct thesis.

Thirty minutes later the system cancelled it itself:

    "Planner pending cancelled as stale: market covered 61% of target path
     without fill"

That cancellation was right. The placement was the fault, and cancelling
30 minutes later does not undo it: while the order rested it occupied the
map and the scenario family.

TWO INDEPENDENT GUARDS
----------------------
1. Reachability. The threshold is not invented -- it is
   ``session_planner.max_primary_zone_width_points`` (450), the widest zone
   the planner may publish. An entry further away than the widest legitimate
   zone cannot be a mapped level, because no zone reaches it.

2. Not behind a live winner. A resting order in the same direction as a
   profitable live trade, at a worse price than that trade's entry, can only
   fill on the move that ends the winner.

Both are deliberately narrow: market orders are never blocked, a pending
order at a *better* price than the live entry is a legitimate pyramid and
passes, and a losing live trade does not trigger guard 2 at all.
"""

from __future__ import annotations

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "run_analysis_guards", os.path.join(ROOT, "scripts", "run_analysis.py")
)
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)

from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()

# The order exactly as it was published.
THE_ORDER = {
    "decision": "SELL",
    "symbol": "XAU/USD",
    "current_price": 4023.21,
    "signal": {
        "order_type": "SELL_LIMIT",
        "entry": {"price": 4076.93},
        "stop_loss": 4091.93,
        "tp1": 4054.13,
        "tp2": 4031.32,
    },
}

# The live winner it was placed behind.
THE_LIVE_WINNER = [{
    "id": "TRADE_20260731_060253_794033_d917b1d5",
    "symbol": "XAU/USD", "type": "SELL", "status": "TP1_HIT",
    "entry_price": 4074.78, "current_pnl_points": 399.6,
}]


def _resting_violations(decision, open_trades) -> list[str]:
    return [
        v for v in ra.validate_signal_before_send(decision, CONFIG, open_trades)
        if "resting" in v
    ]


def test_the_published_order_is_refused() -> None:
    violations = _resting_violations(THE_ORDER, THE_LIVE_WINNER)
    assert len(violations) == 2, (
        "both faults were present at once and each must be reported in its "
        f"own words, got: {violations}"
    )
    assert any("537 pts" in v for v in violations)
    assert any("d917b1d5" in v for v in violations)


def test_unreachable_alone_is_enough() -> None:
    """No live trade at all -- distance still refuses it."""
    violations = _resting_violations(THE_ORDER, [])
    assert len(violations) == 1
    assert "537 pts" in violations[0]
    assert "450-pt" in violations[0], "the limit must be quoted from config"


def test_behind_a_live_winner_alone_is_enough() -> None:
    """Comfortably reachable, but still a bet against the winner."""
    near = {
        "decision": "SELL", "symbol": "XAU/USD", "current_price": 4060.00,
        "signal": {"order_type": "SELL_LIMIT", "entry": {"price": 4090.00},
                   "stop_loss": 4130.00, "tp1": 4050.00, "tp2": 4010.00},
    }
    violations = _resting_violations(near, THE_LIVE_WINNER)
    assert len(violations) == 1
    assert "worse than live" in violations[0]


def test_the_threshold_comes_from_the_map_not_a_new_number() -> None:
    planner = CONFIG.get("session_planner") or {}
    assert ra._max_pending_distance_points(CONFIG) == float(
        planner.get("max_primary_zone_width_points")
    ) == 450.0


def test_a_buy_limit_far_below_the_market_is_refused_too() -> None:
    far_buy = {
        "decision": "BUY", "symbol": "XAU/USD", "current_price": 4023.21,
        "signal": {"order_type": "BUY_LIMIT", "entry": {"price": 3960.00},
                   "stop_loss": 3920.00, "tp1": 4000.00, "tp2": 4040.00},
    }
    violations = _resting_violations(far_buy, [])
    assert len(violations) == 1 and "632 pts" in violations[0]


# ── the guards must not become a blanket ban ────────────────────────────────

def test_a_normal_nearby_pending_order_passes() -> None:
    ok = {
        "decision": "SELL", "symbol": "XAU/USD", "current_price": 4050.00,
        "signal": {"order_type": "SELL_LIMIT", "entry": {"price": 4062.00},
                   "stop_loss": 4082.00, "tp1": 4030.00, "tp2": 4000.00},
    }
    assert ra.validate_signal_before_send(ok, CONFIG, []) == []


def test_a_pending_order_at_a_better_price_than_the_winner_passes() -> None:
    """Adding at a better price is a pyramid, not a hedge."""
    pyramid = {
        "decision": "SELL", "symbol": "XAU/USD", "current_price": 4023.21,
        "signal": {"order_type": "SELL_LIMIT", "entry": {"price": 4060.00},
                   "stop_loss": 4100.00, "tp1": 4020.00, "tp2": 3980.00},
    }
    assert _resting_violations(pyramid, THE_LIVE_WINNER) == []


def test_a_market_order_is_never_blocked() -> None:
    market = {
        "decision": "SELL", "symbol": "XAU/USD", "current_price": 4023.21,
        "signal": {"order_type": "SELL_MARKET", "entry": {"price": 4023.21},
                   "stop_loss": 4063.21, "tp1": 3983.21, "tp2": 3943.21},
    }
    assert ra.validate_signal_before_send(market, CONFIG, THE_LIVE_WINNER) == []


def test_a_losing_live_trade_does_not_trigger_the_winner_guard() -> None:
    losing = [{
        "id": "loser", "symbol": "XAU/USD", "type": "SELL", "status": "OPEN",
        "entry_price": 4074.78, "current_pnl_points": -40.0,
    }]
    near = {
        "decision": "SELL", "symbol": "XAU/USD", "current_price": 4060.00,
        "signal": {"order_type": "SELL_LIMIT", "entry": {"price": 4090.00},
                   "stop_loss": 4130.00, "tp1": 4050.00, "tp2": 4010.00},
    }
    assert _resting_violations(near, losing) == []


def test_the_opposite_direction_is_not_a_hedge_against_the_winner() -> None:
    buy_while_short = {
        "decision": "BUY", "symbol": "XAU/USD", "current_price": 4023.21,
        "signal": {"order_type": "BUY_LIMIT", "entry": {"price": 4000.00},
                   "stop_loss": 3960.00, "tp1": 4040.00, "tp2": 4080.00},
    }
    assert _resting_violations(buy_while_short, THE_LIVE_WINNER) == []


def test_fault_injection_without_the_guards_the_order_is_accepted() -> None:
    """Prove the guards are what stops it, not the pre-existing checks."""
    other = [
        v for v in ra.validate_signal_before_send(THE_ORDER, CONFIG, THE_LIVE_WINNER)
        if "resting" not in v
    ]
    assert other == [], (
        "every pre-existing arithmetic check passes this order: its geometry "
        "is internally valid. Only reachability and the live-winner test "
        f"catch it, so removing them lets it through again. Got: {other}"
    )
