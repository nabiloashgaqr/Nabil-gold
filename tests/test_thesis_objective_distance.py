"""A narrated objective must not contradict the targets printed above it.

The published plan showed:

    TARGETS · TP1 4047.76 · TP2 4054.56
    THESIS  · ... hold above 4026.92 and target 4029.33

The thesis objective sat 6 points from a 4028.77 entry while TP1 was 190
points away. Being ahead of the entry is necessary but not sufficient: a
level that close is inside noise, and quoting it makes the message argue
with itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.session_planner import SessionPlannerService

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

_LIQUIDITY = {"recent_sweep": {"reference_type": "recent_lows"}}


def _path(direction, primary, midpoint, current):
    return SessionPlannerService(CONFIG)._expected_path(
        direction, primary, _LIQUIDITY, {"midpoint": midpoint}, current
    )


def test_the_six_point_objective_is_not_quoted() -> None:
    """The exact numbers from the live plan."""
    primary = {"stop_loss": 4026.92, "entry_price": 4028.77, "target_liquidity": 4029.33}

    text = _path("BUY", primary, 4029.33, 4028.77)

    assert "4029.33" not in text
    assert "next mapped liquidity ahead" in text


def test_a_real_objective_is_quoted() -> None:
    primary = {"stop_loss": 4026.92, "entry_price": 4028.77, "target_liquidity": 4047.76}

    assert "target 4047.76" in _path("BUY", primary, 4029.33, 4028.77)


def test_it_skips_to_the_first_meaningful_level() -> None:
    """A trivial primary target must not block a usable fallback."""
    primary = {
        "stop_loss": 4026.92,
        "entry_price": 4028.77,
        "target_liquidity": 4029.33,   # 6 pts - skipped
        "target_price": 4060.00,       # 312 pts - used
    }

    assert "target 4060.0" in _path("BUY", primary, 4029.33, 4028.77)


def test_sell_side_distance_is_enforced_too() -> None:
    near = {"stop_loss": 4066.18, "entry_price": 4051.18, "target_liquidity": 4050.60}
    far = {"stop_loss": 4066.18, "entry_price": 4051.18, "target_liquidity": 4021.18}

    assert "4050.6" not in _path("SELL", near, 4050.60, 4051.18)
    assert "target 4021.18" in _path("SELL", far, 4050.60, 4051.18)


def test_direction_is_still_enforced() -> None:
    """The earlier fix must survive: no objective behind the entry."""
    primary = {"stop_loss": 4026.71, "entry_price": 4029.64}

    text = _path("BUY", primary, 4029.33, 4029.64)

    assert "4029.33" not in text
    assert "next mapped liquidity ahead" in text


def test_config_ships_the_distance_floor() -> None:
    assert float(CONFIG["session_planner"]["min_thesis_objective_points"]) > 0
