"""Guards for the three faults that turned a correct call into a flat trade.

A live BUY on 2026-07-29 was analytically right -- price ran 134 points -- and
still closed at breakeven. Three independent defects combined:

1. TP1 was taken as the literal nearest pool, 5 points from entry against a
   150-point stop (0.035R).
2. Touching TP1 armed the breakeven stop unconditionally, so the trade was
   stopped out flat by ordinary noise minutes later.
3. A leg that priced as MARKET -- price having reached the mapped area -- was
   abandoned instead of executed, so the map published an order only while
   price was still far away.

Each test below fails if one of those behaviours returns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.open_trades_manager import OpenTradesManager
from scripts.run_analysis import _planned_order_type, _resolve_reward_target

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


# --- 1. TP1 must be worth acting on -------------------------------------

def test_trivial_first_target_is_skipped_not_taken() -> None:
    """The exact levels from the losing trade."""
    entry, stop = 4028.32, 4013.32
    candidate = {"details": {"liquidity": {"buy_side": [4028.85, 4082.34]}}}

    tp1, tp2, reject = _resolve_reward_target(
        "BUY", entry, stop, 4028.85, candidate, 1.5, min_tp1_rr=0.8
    )

    assert reject is None, "a sound plan must not be rejected outright"
    assert tp1 != 4028.85, "the 0.035R pool must not be used as TP1"
    risk = entry - stop
    assert (tp1 - entry) / risk >= 0.8
    assert tp2 == 4082.34


def test_first_target_still_prefers_a_real_pool_when_one_qualifies() -> None:
    """Skipping a trivial level must not mean inventing every target."""
    entry, stop = 4000.0, 3985.0  # 150 pt risk
    candidate = {"details": {"liquidity": {"buy_side": [4015.0, 4060.0]}}}

    tp1, tp2, reject = _resolve_reward_target(
        "BUY", entry, stop, 4015.0, candidate, 1.5, min_tp1_rr=0.8
    )

    assert reject is None
    assert tp1 == 4015.0, "a 1.0R pool is a legitimate first target"
    assert tp2 == 4060.0


def test_default_config_enforces_a_tp1_floor() -> None:
    assert float(CONFIG["risk_settings"]["min_tp1_rr"]) >= 0.5


# --- 2. Breakeven must not arm before the trade travels -----------------

def _trade(**overrides):
    trade = {
        "id": "TRADE_BE_GUARD",
        "type": "BUY",
        "entry_price": 4026.67,
        "stop_loss": 4013.32,
        "initial_stop_loss": 4013.32,
        "tp1": 4028.85,
        "tp2": 4082.34,
        "status": "OPEN",
        "sl_moved_to_entry": False,
        "partial_close": False,
        "updates_sent": [],
    }
    trade.update(overrides)
    return trade


def test_breakeven_is_deferred_when_tp1_is_reached_too_early() -> None:
    """The 0.16R touch that killed the live trade must not move the stop."""
    manager = OpenTradesManager(
        {"trade_management": {"auto_move_sl_to_entry_after_tp1": True, "min_breakeven_rr": 0.5}}
    )

    result = manager.evaluate_trade(_trade(), 4028.85)

    assert "TP1_HIT" in result["events"]
    assert "MOVE_SL_TO_BE" not in result["events"], (
        "arming breakeven at 0.16R is what closed a correct trade flat"
    )
    assert result["updates"].get("stop_loss") in (None, 4013.32)


def test_breakeven_still_arms_once_the_trade_has_travelled() -> None:
    """The guard must not disable breakeven on a genuine target."""
    manager = OpenTradesManager(
        {"trade_management": {"auto_move_sl_to_entry_after_tp1": True, "min_breakeven_rr": 0.5}}
    )
    trade = _trade(entry_price=4000.0, stop_loss=3985.0,
                   initial_stop_loss=3985.0, tp1=4015.0, tp2=4060.0)

    result = manager.evaluate_trade(trade, 4015.0)

    assert "TP1_HIT" in result["events"]
    assert "MOVE_SL_TO_BE" in result["events"], "a 1.0R target should protect the trade"
    assert result["updates"]["stop_loss"] == 4000.0


def test_default_config_sets_a_breakeven_floor() -> None:
    assert float(CONFIG["trade_management"]["min_breakeven_rr"]) > 0


# --- 3. Price reaching the area must execute, not cancel ----------------

@pytest.mark.parametrize("current_price", [4029.64, 4028.50, 4031.00])
def test_price_inside_the_area_prices_as_market(current_price: float) -> None:
    """These are the prices at which the live plan produced nothing."""
    order_type = _planned_order_type(CONFIG, "BUY", 4029.64, current_price, "XAU/USD")
    assert order_type.endswith("MARKET")


def test_market_conversion_is_enabled_by_default() -> None:
    """The leg must convert rather than return None on a MARKET price."""
    assert CONFIG["split_execution"].get("convert_touched_zone_to_market") is True


def _plan_and_candidate():
    plan = {
        "session_bias": "BUY",
        "symbol": "XAU/USD",
        "scenario_type": "CONTINUATION",
        "scenario_id": "SC_TEST",
        "plan_id": "PLAN_TEST",
        "planner_grade": "A+",
        "planner_confidence": 97.3,
        "plan_ready": True,
    }
    candidate = {
        "direction": "BUY",
        "entry_price": 4029.64,
        "stop_loss": 4019.64,
        "target_price": 4051.00,
        "quality_grade": "A+",
        "quality_score": 97.0,
        "selection_role": "PRIMARY",
        "details": {"liquidity": {"buy_side": [4048.00, 4051.00]}},
    }
    return plan, candidate


def test_leg_executes_at_market_when_price_reaches_the_area() -> None:
    """The live failure: price arrived and the leg produced nothing."""
    from scripts.run_analysis import _build_plan_ladder_decision

    plan, candidate = _plan_and_candidate()
    base = {"symbol": "XAU/USD", "current_price": 4029.64, "decision": "BUY"}

    leg = _build_plan_ladder_decision(base, plan, candidate, CONFIG)

    assert leg is not None, "price inside the mapped area must not cancel the leg"
    signal = leg["signal"]
    assert signal["order_type"].endswith("MARKET")
    assert signal["entry"]["kind"] == "MARKET"
    assert signal["entry"]["price"] == pytest.approx(4029.64, abs=0.01)
    assert leg["entry_mode"] == "session_plan_ladder_market"


def test_leg_above_market_with_an_unprotective_stop_is_refused() -> None:
    """Conversion must not swallow ordinary pending placement.

    UPDATED after stop entries were removed. This previously asserted
    BUY_STOP: a BUY mapped at 4029.64 while price sat at 4024.49 used to rest
    above the market as a stop order.

    Stop entries are gone -- they buy strength and sell weakness, which is the
    shape that lost 198 points on 2026-07-29. The replacement is a market fill
    at the better price: 4024.49 instead of 4029.64, five points cheaper.

    The mapped stop of 4019.64 (100 pts, inside the honest [70, 400] band so
    the 2026-08-07 clamp leaves it untouched) sits 4.85 below the 4024.49
    fill, so the executed trade is properly protected. The refusal path exists for plans the floor
    cannot rescue; it is exercised in
    test_unprotective_stop_blocks_a_market_conversion below.
    """
    from scripts.run_analysis import _build_plan_ladder_decision

    plan, candidate = _plan_and_candidate()
    base = {"symbol": "XAU/USD", "current_price": 4024.49, "decision": "BUY"}

    leg = _build_plan_ladder_decision(base, plan, candidate, CONFIG)

    assert leg is not None
    assert leg["signal"]["order_type"] == "BUY_MARKET"
    assert leg["signal"]["order_type"] != "BUY_STOP"
    # Filled better than the mapped level, and genuinely protected.
    assert leg["signal"]["entry"]["price"] == 4024.49
    assert leg["signal"]["stop_loss"] < leg["signal"]["entry"]["price"]


def test_unprotective_stop_blocks_a_market_conversion() -> None:
    """A plan whose stop cannot protect a market fill is refused outright.

    With stop entries removed there is no way to wait above the market, so a
    leg that would open beyond its own invalidation must not be created.
    """
    from scripts.run_analysis import _planned_order_type

    # BUY mapped at 4042.43 with a 4039.64 stop while price is far below.
    order = _planned_order_type(
        CONFIG, "BUY", 4042.43, 3992.76, "XAU/USD", planned_stop=4039.64,
    )
    assert order == "NO_ENTRY"

    # The mirror case on the sell side.
    order = _planned_order_type(
        CONFIG, "SELL", 4000.0, 4050.0, "XAU/USD", planned_stop=4002.0,
    )
    assert order == "NO_ENTRY"


def test_converted_leg_still_respects_the_reward_gate() -> None:
    """Conversion is not a bypass: a leg without reward is still refused."""
    from scripts.run_analysis import _build_plan_ladder_decision

    plan, candidate = _plan_and_candidate()
    candidate["target_price"] = 4029.90          # ~0.02R away
    candidate["details"] = {"liquidity": {"buy_side": [4029.90]}}
    base = {"symbol": "XAU/USD", "current_price": 4029.64, "decision": "BUY"}

    assert _build_plan_ladder_decision(base, plan, candidate, CONFIG) is None


def test_distant_price_still_produces_a_pending_order() -> None:
    """Conversion must not replace ordinary pending placement."""
    order_type = _planned_order_type(CONFIG, "BUY", 4029.64, 4024.49, "XAU/USD")
    assert order_type in {"BUY_MARKET", "NO_ENTRY"}
    assert order_type != "BUY_STOP", "stop entries were removed"
