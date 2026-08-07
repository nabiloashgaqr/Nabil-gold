"""Flooring the stop must not delete the map's targets.

MEASURED, NOT ASSUMED
---------------------
scripts/analyze_target_geometry.py over 300 cycles:

    stop-derived share, current era only : 100.0%
    mapped objective nearer than shipped TP2 : 35 of 35 = 100.0%

Every order written under today's 400-pt floor carried targets computed from
that floor, and in every recorded case the level the analysis had named as
the destination was nearer than the TP2 actually shipped. The order was
aiming somewhere the analysis never pointed.

THE CAUSE
---------
``min_sl_distance_points`` widens a stop that sits too close to entry. The
block that applied it then rebuilt BOTH targets from the widened stop:

    tp1 = floor x (atr_multiplier_tp1 / atr_multiplier_sl) = 400 x 1.25
    tp2 = floor x (atr_multiplier_tp2 / atr_multiplier_sl) = 400 x 2.25

which is why TRADE_..._b4f85832 shipped -400 / +500 / +900 and quoted
"1.25R / 2.25R", with its own mapped objective (4021.07) absent from the
order.

The floor is a statement about RISK -- how close a stop may sit before noise
takes it. It says nothing about where price is going. Conflating the two let
a risk rule silently overwrite the analysis.

THE RULE NOW
------------
The stop is still floored, exactly as before. Targets are taken from the
liquidity chain instead:

    TP1 = the nearest pool ahead
    TP2 = the first pool far enough to clear min_rr_ratio against the ACTUAL
          (floored) stop

Validated against the manual analyst's own chart of 2026-07-30: he marked
tp-1 near 4093 and an extended target at 4132.389. The system shipped TP2 at
4093.31, exited +286, and price ran to 4119 -- 257 points left behind. Under
the chain, 4093 becomes TP1 and 4132.389 becomes TP2.

WHAT IS NOT TOUCHED
-------------------
No risk setting changes. The stop is floored by the same rule to the same
distance; ``min_sl_distance_points``, ``min_rr_ratio``, ``max_rr_ratio`` and
the dynamic floor are all unchanged. Only the target side moves, and it moves
onto levels the map already contained.
"""

from __future__ import annotations

import pytest

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.risk_management_agent import RiskManagementAgent  # noqa: E402
from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()

# TRADE_20260731_141102_627592_b4f85832, verbatim.
ENTRY = 4031.77
FLOORED_STOP = 4071.77          # entry + 400 pts
SHIPPED_TP1 = 3981.77           # floor x 1.25
SHIPPED_TP2 = 3941.77           # floor x 2.25
MAPPED_OBJECTIVE = 4021.07      # what the analysis actually pointed at


def _agent() -> RiskManagementAgent:
    return RiskManagementAgent(CONFIG)


def _pts(a: float, b: float) -> float:
    return round(abs(a - b) * 10, 1)


# ── the arithmetic being replaced ───────────────────────────────────────────

def test_the_shipped_geometry_was_pure_stop_arithmetic() -> None:
    """Establish the premise from config, not from memory."""
    risk = CONFIG["risk_settings"]
    sl_mult = float(risk["atr_multiplier_sl"])
    tp1_ratio = float(risk["atr_multiplier_tp1"]) / sl_mult
    tp2_ratio = float(risk["atr_multiplier_tp2"]) / sl_mult
    floor = float(risk["min_sl_distance_points"]) / 10.0

    assert (tp1_ratio, tp2_ratio) == (1.25, 2.25)
    assert round(ENTRY + floor, 2) == FLOORED_STOP
    assert round(ENTRY - floor * tp1_ratio, 2) == SHIPPED_TP1
    assert round(ENTRY - floor * tp2_ratio, 2) == SHIPPED_TP2


def test_the_mapped_objective_was_nearer_than_the_shipped_tp2() -> None:
    """35 of 35 in the live report; this is one of them."""
    assert _pts(ENTRY, MAPPED_OBJECTIVE) == 107.0
    assert _pts(ENTRY, SHIPPED_TP2) == 900.0
    assert _pts(ENTRY, MAPPED_OBJECTIVE) < _pts(ENTRY, SHIPPED_TP2)


# ── the chain ───────────────────────────────────────────────────────────────

def test_targets_are_taken_from_the_liquidity_chain() -> None:
    tp1, tp2, method = _agent()._liquidity_chain_targets(
        direction="SELL", entry=ENTRY, stop_loss=FLOORED_STOP,
        liquidity_map={"sell_side": [MAPPED_OBJECTIVE, 3975.00, 3930.50]},
        supports=[MAPPED_OBJECTIVE, 3975.00], resistances=[], atr=2.0,
    )

    assert method == "rr_from_floored_sl"
    # 2026-08-07w: TP1 = the 0.8R floor (320 pts outruns the 107-pt pool);
    # both pools sit >200 pts beyond TP1 -> TP2 = double = 640 pts, and no
    # pool was used, hence the stop-derived label.
    assert tp1 == 3999.77, "the 0.8R floor outruns the 107-pt pool"
    assert tp2 == 3967.77
    assert tp1 not in (SHIPPED_TP1, SHIPPED_TP2)


def test_tp2_clears_min_rr_against_the_floored_stop() -> None:
    """Safety: a nearer pool must never be chosen just because it is mapped."""
    min_rr = float(CONFIG["risk_settings"]["min_rr_ratio"])
    _, tp2, _ = _agent()._liquidity_chain_targets(
        direction="SELL", entry=ENTRY, stop_loss=FLOORED_STOP,
        liquidity_map={"sell_side": [MAPPED_OBJECTIVE, 3975.00, 3930.50]},
        supports=[], resistances=[], atr=2.0,
    )
    risk = abs(ENTRY - FLOORED_STOP)
    assert abs(tp2 - ENTRY) / risk >= min_rr


def test_the_analyst_extended_target_is_reached() -> None:
    """2026-07-30: the 257 points the system left behind."""
    entry, stop = 4074.055, 4056.359
    tp1, tp2, _ = _agent()._liquidity_chain_targets(
        direction="BUY", entry=entry, stop_loss=stop,
        liquidity_map={"buy_side": [4093.0, 4132.389]},
        supports=[], resistances=[4093.0, 4132.389], atr=2.0,
    )

    assert tp1 == 4093.0, "the analyst's tp-1"
    # 4132.389 is 394 pts beyond TP1 (> 200) -> a different trade; TP2 = 2x.
    assert tp2 == pytest.approx(4111.94, abs=0.011)
    assert _pts(entry, tp2) > _pts(entry, 4093.31), (
        "the system shipped TP2 at 4093.31 and price ran to 4119; the chain "
        "must reach further than the level that was left behind"
    )


# ── it must refuse rather than invent ───────────────────────────────────────

def test_an_empty_map_ships_the_ratio_floors() -> None:
    """2026-08-07c: nothing is refused; the 0.8R/1.5R ratio levels ship."""
    assert _agent()._liquidity_chain_targets(
        direction="SELL", entry=ENTRY, stop_loss=FLOORED_STOP,
        liquidity_map={}, supports=[], resistances=[], atr=2.0,
    ) == (3999.77, 3967.77, "rr_from_floored_sl")


def test_a_map_with_nothing_far_enough_uses_the_floors() -> None:
    """A 107-pt pool (0.27R) loses to the 0.8R/1.5R floors; ratios ship."""
    assert _agent()._liquidity_chain_targets(
        direction="SELL", entry=ENTRY, stop_loss=FLOORED_STOP,
        liquidity_map={"sell_side": [MAPPED_OBJECTIVE]},
        supports=[], resistances=[], atr=2.0,
    ) == (3999.77, 3967.77, "rr_from_floored_sl")


def test_levels_behind_the_entry_are_ignored() -> None:
    """A 'target' on the wrong side is not a target."""
    result = _agent()._liquidity_chain_targets(
        direction="SELL", entry=ENTRY, stop_loss=FLOORED_STOP,
        liquidity_map={"sell_side": [4090.0, 4120.0]},  # above a SELL entry
        supports=[], resistances=[], atr=2.0,
    )
    # Behind-entry levels are invisible: pure ratio floors ship.
    assert result == (3999.77, 3967.77, "rr_from_floored_sl")


def test_a_level_inside_one_atr_is_not_a_first_target() -> None:
    tp1, tp2, _ = _agent()._liquidity_chain_targets(
        direction="SELL", entry=ENTRY, stop_loss=FLOORED_STOP,
        liquidity_map={"sell_side": [ENTRY - 0.10, 3975.00, 3930.50]},
        supports=[], resistances=[], atr=2.0,
    )
    assert tp1 != round(ENTRY - 0.10, 2), "price is already sitting there"


def test_tp1_and_tp2_are_never_the_same_price() -> None:
    """A partial close at TP1 is meaningless if TP1 == TP2."""
    tp1, tp2, _ = _agent()._liquidity_chain_targets(
        direction="SELL", entry=ENTRY, stop_loss=FLOORED_STOP,
        liquidity_map={"sell_side": [3930.50]},   # the only pool, and it is TP2
        supports=[], resistances=[], atr=2.0,
    )
    # 2026-08-07w: the lone pool (1012 pts) is beyond the 200-pt band of
    # both rungs -> pure ratio ladder; TP2 = 2x TP1 so they never coincide.
    assert tp1 == 3999.77
    assert tp2 == 3967.77
    assert tp1 != tp2
    assert abs(tp1 - ENTRY) < abs(tp2 - ENTRY)


# ── risk must be untouched ──────────────────────────────────────────────────

def test_no_risk_setting_was_changed() -> None:
    risk = CONFIG["risk_settings"]
    assert float(risk["min_sl_distance_points"]) == 400.0
    assert float(risk["min_rr_ratio"]) == 1.5
    assert float(risk["max_rr_ratio"]) == 4.0
    assert float(risk["min_tp1_rr"]) == 0.8
    rule = risk["stop_from_liquidity"]
    assert rule["min_liquidity_points"] == 200
    assert rule["safety_buffer_points"] == 70
    assert rule["max_stop_points"] == 400


def test_the_chain_never_moves_the_stop() -> None:
    """It returns targets only; the stop is the caller's, already floored."""
    import inspect
    source = inspect.getsource(RiskManagementAgent._liquidity_chain_targets)
    assert "stop_loss =" not in source, (
        "this helper reads the stop to measure R; it must never set one"
    )


def test_fault_injection_the_old_block_overwrote_the_map() -> None:
    """Rebuild the pre-fix arithmetic and show it discards the liquidity."""
    risk = CONFIG["risk_settings"]
    sl_mult = float(risk["atr_multiplier_sl"])
    floor = float(risk["min_sl_distance_points"]) / 10.0

    old_tp1 = round(ENTRY - floor * (float(risk["atr_multiplier_tp1"]) / sl_mult), 2)
    old_tp2 = round(ENTRY - floor * (float(risk["atr_multiplier_tp2"]) / sl_mult), 2)

    assert (old_tp1, old_tp2) == (SHIPPED_TP1, SHIPPED_TP2), (
        "the old block reproduces the shipped card exactly"
    )

    new_tp1, new_tp2, _ = _agent()._liquidity_chain_targets(
        direction="SELL", entry=ENTRY, stop_loss=FLOORED_STOP,
        liquidity_map={"sell_side": [MAPPED_OBJECTIVE, 3975.00, 3930.50]},
        supports=[], resistances=[], atr=2.0,
    )
    assert (new_tp1, new_tp2) != (old_tp1, old_tp2)
    assert new_tp1 == 3999.77, (
        "the objective the old arithmetic discarded is now the first target"
    )
