"""Regression: the stop floor CLAMPS the structural stop, never multiplies it.

Operator directive (2026-08-07): "تحت السيولة، حد أدنى 70 نقطة".
The structural stop (already beyond the nearest opposing liquidity with an
ATR buffer) must pass through untouched when it lies inside [min_points,
max_points]; the floor may only raise stops below the absolute noise minimum.
max_points caps that raise -- it never tightens a wider structural stop.

The dead bug: ``max(min_points, structural * structural_multiplier)`` -- the
x3 era flattened tight 30-70 pt structural stops into a constant, inflated
the R ruler and made the liquidity map look illogical
(STOP_LIQUIDITY_DIAGNOSIS_AR.md). Reintroducing any multiplier must fail here.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.risk_management_agent import RiskManagementAgent

SYMBOL = "XAU/USD"
ENTRY = 4037.48


def _config(*, min_points: float = 70.0, max_points: float = 400.0) -> dict:
    return {
        "symbol": SYMBOL,
        "risk_settings": {
            "min_sl_distance_points": 400.0,
            "min_rr_ratio": 1.5,
            "max_rr_ratio": 4.0,
            "atr_multiplier_sl": 2.0,
            "atr_multiplier_tp1": 2.5,
            "atr_multiplier_tp2": 4.5,
            "dynamic_sl_floor": {
                "enabled": True,
                "min_points": min_points,
                "max_points": max_points,
            },
        },
        "agent_weights": {
            "technical": 1, "classical": 1, "smc": 1,
            "price_action": 1, "multitimeframe": 1,
        },
    }


def _results(*, atr: float, smc_stop: float = 4060.0) -> dict:
    """SELL consensus; the atr candidate (entry + 2*ATR) sets the structural
    distance because the smc/resistance candidates sit farther away."""
    return {
        "current_price": ENTRY,
        "atr": atr,
        "technical": {"direction": "SELL", "confidence": 80},
        "classical": {"direction": "SELL", "confidence": 80},
        "smc": {
            "direction": "SELL", "confidence": 80,
            "entry_suggestion": {
                "entry": ENTRY,
                "zone": {"proximal": 4034.48, "distal": 4040.48},
                "stop_loss": smc_stop,
            },
            "liquidity": {"sell_side": [4028.20, 4020.00]},
        },
        "price_action": {"direction": "SELL", "confidence": 80},
        "multitimeframe": {"direction": "SELL", "confidence": 80},
        "support_levels": [4028.20, 4020.00],
        "resistance_levels": [4080.00],
        "portfolio": {"open_trades": 0},
    }


def _shipped_points(atr: float) -> float:
    out = RiskManagementAgent(copy.deepcopy(_config())).evaluate(_results(atr=atr))
    return float((out.get("stop_loss") or {}).get("distance_points") or 0.0)


def test_structural_inside_band_passes_untouched():
    # atr 5.0 -> atr candidate = 10 USD = 100 pts, inside [70, 400].
    shipped = _shipped_points(5.0)
    assert shipped == pytest.approx(100.0, abs=1.0), (
        f"A 100-pt structural stop must ship at 100 pts. Got {shipped}: "
        "the floor multiplied or rewrote structure (the dead x3 bug)."
    )


def test_structural_below_minimum_raises_to_minimum():
    # atr 2.5 -> structural 50 pts < 70 -> raised to the operator's 70.
    shipped = _shipped_points(2.5)
    assert shipped == pytest.approx(70.0, abs=1.0), (
        f"A 50-pt structural stop must be raised to the 70-pt absolute "
        f"minimum, got {shipped}."
    )


def test_structural_above_ceiling_is_never_tightened():
    # atr 25 -> structural 500 pts. max_points caps the floor's RAISE only;
    # a floor may widen a stop, never move one closer to entry.
    shipped = _shipped_points(25.0)
    assert shipped == pytest.approx(500.0, abs=1.0), (
        f"A 500-pt structural stop must stay 500 pts, got {shipped}: "
        "the floor tightened structure."
    )


def test_production_config_carries_the_operator_numbers():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    floor = (cfg.get("risk_settings") or {}).get("dynamic_sl_floor") or {}
    assert floor.get("min_points") == 70, "operator minimum is 70 points"
    assert floor.get("max_points") == 400
    assert "structural_multiplier" not in floor, (
        "structural_multiplier reintroduced -- it flattens structural stops"
    )
