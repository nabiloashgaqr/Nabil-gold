"""The scaled stop floor must apply on EVERY path that prices a trade.

WHAT THIS PINS
--------------
`risk_settings.dynamic_sl_floor` replaced the flat 400-point floor on gold
because the flat number, not structure, was setting the risk on every plan.
`scripts.run_analysis._planner_trade_levels` honours it.

`RiskManagementAgent` did not. It read `min_sl_distance_points` raw, so the
CONSENSUS / two-agent route -- the route that builds the shipped order at
run_analysis.py:3894 -- floored every stop to the full 400 while the planner
priced the identical leg at 150. One setting, one intent, two answers.

THE MEASURED CASE (2026-08-03, signal 2f72579f)
-----------------------------------------------
SELL, entry 4037.48, published zone 4034.48 -> 4040.48, ATR 1.5.
Running `RiskManagementAgent.evaluate()` on that setup:

    live main : SL 4077.48 (400.0 pts)  TP1 3987.48  TP2 3947.48
                target_method = rr_from_floored_sl
    fixed     : SL 4052.48 (150.0 pts)  TP1 4028.20  TP2 4000.00
                target_method = liquidity_chain

The live column reproduces the card the operator received, digit for digit,
including the -400/+500/+900 signature. The structural stop was 30 points.

Against a 400-point stop every level on the analyst's map is unreachable::

    4028.20 (card objective)      92.8 pts   0.23R
    4022.31 (previous TP2)       151.7 pts   0.38R
    4020.00 (sellside liquidity) 174.8 pts   0.44R
    4000.00 (sellside stop hunt) 374.8 pts   0.94R

`min_rr_ratio` is 1.5, so the mapped target is refused and the ratio fallback
invents targets. Against the scaled floor, 4000.00 is 2.50R and the liquidity
chain has a real objective -- which is exactly what the fixed column ships.

WHAT THIS DOES NOT DO
---------------------
No risk setting is changed. `min_sl_distance_points` stays 400 and remains the
ceiling via `dynamic_sl_floor.max_points`; `min_rr_ratio` stays 1.5. The floor
still cannot go below `min_points`, and it can never tighten a stop.

FAULT INJECTION
---------------
Restore the old line in agents/risk_management_agent.py::

    min_sl_distance = points_to_price(
        self._f(self.settings.get("min_sl_distance_points"), 0.0), self.symbol)

Verified against live main: 8 tests fail, including
`test_the_real_2026_08_03_signal_ships_a_scaled_stop` (400.0 != 150.0) and
`test_scaled_floor_lets_the_liquidity_chain_ship_mapped_targets`
(rr_from_floored_sl != liquidity_chain).

Every assertion below reads what `evaluate()` actually returned. None of them
re-implement the floor arithmetic -- an earlier draft did, and it passed
against the unfixed code because it was only testing its own copy of the
formula.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.risk_management_agent import RiskManagementAgent  # noqa: E402
from utils.instruments import price_to_points  # noqa: E402


SYMBOL = "XAU/USD"

# The real signal: SELL 4037.48, zone 4034.48 -> 4040.48.
ENTRY = 4037.48
ZONE_PROXIMAL = 4034.48
ZONE_DISTAL = 4040.48
MAPPED_LIQUIDITY = [4028.20, 4022.31, 4020.00, 4000.00]


def _config(*, enabled: bool = True,
            min_points: float = 70.0, max_points: float = 400.0,
            flat: float = 400.0) -> dict:
    return {
        "symbol": SYMBOL,
        "risk_settings": {
            "min_sl_distance_points": flat,
            "min_rr_ratio": 1.5,
            "max_rr_ratio": 4.0,
            "atr_multiplier_sl": 2.0,
            "atr_multiplier_tp1": 2.5,
            "atr_multiplier_tp2": 4.5,
            "dynamic_sl_floor": {
                "enabled": enabled,
                "min_points": min_points,
                "max_points": max_points,
            },
        },
        "agent_weights": {
            "technical": 1, "classical": 1, "smc": 1,
            "price_action": 1, "multitimeframe": 1,
        },
    }


def _results(*, atr: float = 1.5) -> dict:
    """A SELL consensus on the 2026-08-03 map. ATR drives the structural stop."""
    return {
        "current_price": ENTRY,
        "atr": atr,
        "technical": {"direction": "SELL", "confidence": 80},
        "classical": {"direction": "SELL", "confidence": 80},
        "smc": {
            "direction": "SELL", "confidence": 80,
            "entry_suggestion": {
                "entry": ENTRY,
                "zone": {"proximal": ZONE_PROXIMAL, "distal": ZONE_DISTAL},
                "stop_loss": ZONE_DISTAL + 0.30,
            },
            "liquidity": {"sell_side": list(MAPPED_LIQUIDITY)},
        },
        "price_action": {"direction": "SELL", "confidence": 80},
        "multitimeframe": {"direction": "SELL", "confidence": 80},
        "support_levels": list(MAPPED_LIQUIDITY),
        "resistance_levels": [4040.48, 4045.09, 4064.00],
        "portfolio": {"open_trades": 0},
    }


def _evaluate(config: dict, *, atr: float = 1.5) -> dict:
    """Drive the real agent. Every assertion reads this output."""
    return RiskManagementAgent(copy.deepcopy(config)).evaluate(_results(atr=atr))


def _shipped_sl_points(out: dict) -> float:
    return float((out.get("stop_loss") or {}).get("distance_points") or 0.0)


def _shipped_sl_price(out: dict) -> float:
    return float((out.get("stop_loss") or {}).get("price") or 0.0)


def _target_method(out: dict) -> str:
    return str((out.get("risk_metrics") or {}).get("target_method") or "")


def _tp(out: dict, key: str) -> float:
    return float(((out.get("take_profit") or {}).get(key) or {}).get("price") or 0.0)


# --------------------------------------------------------------------------
# The measured regression.
# --------------------------------------------------------------------------

def test_the_real_2026_08_03_signal_ships_a_scaled_stop():
    """2f72579f shipped a 400-pt stop on a 30-pt structural stop."""
    out = _evaluate(_config())
    shipped = _shipped_sl_points(out)

    assert shipped != pytest.approx(400.0, abs=0.5), (
        "The flat 400-point floor is still being applied on the consensus "
        "path. This is the stop the operator received on 2026-08-03."
    )
    assert shipped == pytest.approx(70.0, abs=0.5), (
        f"Operator directive 2026-08-07: minimum 70 pts. Got {shipped}."
    )
    assert _shipped_sl_price(out) == pytest.approx(4044.48, abs=0.05)


def test_scaled_floor_lets_the_liquidity_chain_ship_mapped_targets():
    """The -400/+500/+900 signature must be gone, replaced by real levels."""
    out = _evaluate(_config())

    assert _target_method(out) == "liquidity_chain", (
        f"Targets came from {_target_method(out)!r}, not the map. Under the "
        f"flat floor this is 'rr_from_floored_sl' -- invented geometry."
    )
    assert _tp(out, "tp1") == pytest.approx(4028.20, abs=0.05), (
        "TP1 must be the mapped objective from the operator's card."
    )
    assert _tp(out, "tp2") == pytest.approx(4020.00, abs=0.05), (
        "Against a 70-pt stop the 4000.00 level is 5.35R (> max_rr 4.0); "
        "the chain must settle on the next real level, 4020.00 (2.50R)."
    )
    for invented in (3987.48, 3947.48):
        assert _tp(out, "tp1") != pytest.approx(invented, abs=0.05)
        assert _tp(out, "tp2") != pytest.approx(invented, abs=0.05)


def test_shipped_targets_clear_min_rr_against_the_shipped_stop():
    """The reward label must be true against the stop that actually shipped."""
    config = _config()
    out = _evaluate(config)
    risk = _shipped_sl_points(out)
    reward = abs(price_to_points(ENTRY - _tp(out, "tp2"), SYMBOL))

    assert reward / risk >= config["risk_settings"]["min_rr_ratio"], (
        f"TP2 is {reward / risk:.2f}R against the shipped {risk}-pt stop."
    )


def test_the_agent_reads_the_dynamic_floor_at_all():
    """Source guard: the raw setting must not be the sole input any more."""
    source = (ROOT / "agents" / "risk_management_agent.py").read_text()
    assert "dynamic_sl_floor" in source, (
        "RiskManagementAgent ignores risk_settings.dynamic_sl_floor, so the "
        "consensus path floors stops to the flat min_sl_distance_points "
        "while the planner path scales them."
    )


# --------------------------------------------------------------------------
# The two doors must agree.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("atr", [0.5, 1.0, 1.5, 3.0, 5.0])
def test_consensus_stop_matches_planner_stop(atr):
    """Same leg, same config: both doors must ship the same stop distance."""
    from scripts.run_analysis import _planner_trade_levels

    config = _config()
    out = _evaluate(config, atr=atr)
    structural = float((out.get("risk_metrics") or {}).get("structural_sl_points") or 0.0)

    planner = _planner_trade_levels(
        config, direction="SELL", entry_price=ENTRY,
        stop_loss=ENTRY + structural * 0.1, target_price=4000.00, symbol=SYMBOL,
    )
    planner_points = abs(price_to_points(ENTRY - float(planner["stop_loss"]), SYMBOL))

    assert _shipped_sl_points(out) == pytest.approx(planner_points, abs=0.5), (
        f"atr {atr}: the agent ships {_shipped_sl_points(out)} pts but the "
        f"planner prices the same leg at {planner_points} pts."
    )


# --------------------------------------------------------------------------
# The floor must stay a floor. These guard the fix from becoming a loophole.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "atr, expected_structural, expected_shipped",
    [
        (0.5, 10.0, 70.0),    # below the operator minimum: raised to 70
        (1.5, 30.0, 70.0),    # the real signal: raised to 70
        (3.0, 60.0, 70.0),    # still below 70: raised
        (5.0, 100.0, 100.0),  # inside the band: UNTOUCHED (no multiplier!)
        (7.0, 140.0, 140.0),  # inside the band: UNTOUCHED
        (15.0, 300.0, 300.0),  # inside the band: UNTOUCHED
        (30.0, 600.0, 600.0),  # wider than the ceiling: never tightened
    ],
)
def test_the_floor_clamps_structural_to_operator_band(atr, expected_structural,
                                                      expected_shipped):
    """Pin the operator formula (2026-08-07): clamp(structural, min, lift-cap).

    THIS TEST WAS REWRITTEN AGAIN, NOT DELETED -- the spec changed by
    explicit operator directive: "تحت السيولة، حد أدنى 70 نقطة".

    The structural stop already sits beyond the nearest opposing liquidity
    with an ATR buffer, so the floor must not multiply it. It only raises
    stops below the absolute 70-pt noise minimum; `max_points` caps that
    raise, never the structural stop itself (a floor may widen, never
    tighten -- the last row proves it).

    The dead bug this table now guards: any reintroduced multiplier. Under
    the old x3 formula the 100/140/300-pt rows would ship 300/400/400 and
    this test would fail.
    """
    out = _evaluate(_config(), atr=atr)
    structural = float((out.get("risk_metrics") or {}).get("structural_sl_points") or 0.0)

    assert structural == pytest.approx(expected_structural, abs=1.0)
    assert _shipped_sl_points(out) == pytest.approx(expected_shipped, abs=1.0)


@pytest.mark.parametrize("atr", [0.5, 1.5, 3.0, 7.0, 15.0, 30.0])
def test_the_floor_never_tightens_a_structural_stop(atr):
    """The real invariant: the shipped stop is never closer than structure.

    A floor may widen a stop. It must never move one closer to entry, which
    would place the stop inside the zone the order fills from.
    """
    out = _evaluate(_config(), atr=atr)
    structural = float((out.get("risk_metrics") or {}).get("structural_sl_points") or 0.0)
    assert _shipped_sl_points(out) >= structural - 0.5, (
        f"atr {atr}: structural {structural} pts was tightened to "
        f"{_shipped_sl_points(out)} pts."
    )


@pytest.mark.parametrize("atr", [0.5, 1.5, 3.0, 5.0, 7.0, 15.0])
def test_the_shipped_stop_never_exceeds_the_configured_ceiling(atr):
    """max_points caps the floor; min_sl_distance_points is never raised."""
    out = _evaluate(_config(), atr=atr)
    structural = float((out.get("risk_metrics") or {}).get("structural_sl_points") or 0.0)
    if structural <= 400.0:
        assert _shipped_sl_points(out) <= 400.0 + 0.5, (
            "The scaled floor must not push risk beyond max_points."
        )


def test_the_stop_still_sits_behind_the_zone():
    """A SELL stop must remain above the zone's distal edge."""
    out = _evaluate(_config())
    assert _shipped_sl_price(out) > ZONE_DISTAL, (
        "The stop fell inside the entry zone; the filling wick would clip it."
    )


def test_disabled_floor_restores_the_flat_behaviour():
    """enabled=false must return the old, fixed 400-point distance."""
    out = _evaluate(_config(enabled=False))
    assert _shipped_sl_points(out) == pytest.approx(400.0, abs=0.5), (
        "dynamic_sl_floor.enabled=false must fall back to the flat floor."
    )


def test_a_lower_min_points_is_honoured():
    """The floor follows configuration, not a value hard-coded in the fix."""
    out = _evaluate(_config(min_points=200.0))
    assert _shipped_sl_points(out) == pytest.approx(200.0, abs=0.5)


def test_risk_settings_are_not_mutated():
    """The fix must not rewrite the operator's configured numbers."""
    config = _config()
    _evaluate(config)
    assert config["risk_settings"]["min_sl_distance_points"] == 400
    assert config["risk_settings"]["min_rr_ratio"] == 1.5
    assert config["risk_settings"]["dynamic_sl_floor"]["min_points"] == 70
