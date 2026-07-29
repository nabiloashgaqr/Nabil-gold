from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.day_map_sanity import DayMapSanityService


def _config() -> dict:
    return {
        "symbol": "XAU/USD",
        "day_map_sanity": {
            "enabled": True,
            "block_when_plan_not_ready": True,
            "entry_zone_tolerance_points": 40,
            "require_planner_execution_for_extreme_poi": True,
            "allowed_execution_modes_for_extreme_poi": [
                "session_plan_ladder",
                "session_plan_ladder_market",
                "adaptive_market_promotion",
            ],
        },
    }


def _plan(extreme: bool = False) -> dict:
    return {
        "plan_ready": True,
        "authority_state": "CONFIRMED",
        "authority_direction": "SELL",
        "primary_entry_zone": {"low": 4020.0, "high": 4045.0},
        "standby_entry_zone": {"low": 4030.0, "high": 4034.0},
        "poi_classification": "EXTREME_POI" if extreme else "HIGH_PROBABILITY_POI",
    }


def _decision(price: float, *, order_type: str, entry_mode: str = "three_agent_consensus", side: str = "SELL") -> dict:
    return {
        "decision": side,
        "symbol": "XAU/USD",
        "current_price": price,
        "entry_mode": entry_mode,
        "signal": {
            "type": side,
            "order_type": order_type,
            "entry": {
                "price": price,
                "current_price": price,
                "order_type": order_type,
            },
        },
    }


def test_day_map_sanity_allows_when_no_plan_ready_unless_strict() -> None:
    """A missing map is an absence of opinion, not a veto.

    UPDATED 2026-07-29 -- this test previously asserted BLOCK_NO_DAY_MAP
    unconditionally, pinning behaviour that refused every directional signal
    whenever the planner had produced no map.

    Across the operator's own 300-cycle sample, 294 cycles (98%) had no ready
    map, overwhelmingly because the planner refused its own map on archetype
    conviction rather than because the market was unreadable. The practical
    effect was that a SELL consensus of 87.3% with zero qualified opposition
    was blocked for lacking a second opinion, on a day the market fell 310
    points.

    Blocking is still available, but it is now an explicit opt-in via
    ``require_day_map_for_all_entries`` rather than a silent default.
    """
    service = DayMapSanityService(_config())
    review = service.review(_decision(4021.0, order_type="SELL_MARKET"), {"plan_ready": False})
    assert review["action"] == "ALLOW"

    strict_config = _config()
    strict_config["day_map_sanity"]["require_day_map_for_all_entries"] = True
    strict_review = DayMapSanityService(strict_config).review(
        _decision(4021.0, order_type="SELL_MARKET"), {"plan_ready": False}
    )
    assert strict_review["action"] == "BLOCK_NO_DAY_MAP"


def test_day_map_sanity_allows_market_inside_confirmed_day_map_zone() -> None:
    service = DayMapSanityService(_config())
    review = service.review(_decision(4021.0, order_type="SELL_MARKET"), _plan(extreme=False))
    assert review["action"] == "ALLOW"


def test_day_map_sanity_blocks_micro_entry_outside_day_map_zone() -> None:
    service = DayMapSanityService(_config())
    review = service.review(_decision(4010.6, order_type="SELL_LIMIT"), _plan(extreme=False))
    assert review["action"] == "BLOCK_ENTRY_OUTSIDE_DAY_MAP"


def test_day_map_sanity_blocks_extreme_poi_legacy_bypass() -> None:
    service = DayMapSanityService(_config())
    review = service.review(_decision(4022.0, order_type="SELL_LIMIT", entry_mode="three_agent_consensus"), _plan(extreme=True))
    assert review["action"] == "BLOCK_EXTREME_POI_BYPASS"


def test_day_map_sanity_allows_extreme_poi_planner_led_execution() -> None:
    service = DayMapSanityService(_config())
    review = service.review(_decision(4022.0, order_type="SELL_MARKET", entry_mode="session_plan_ladder_market"), _plan(extreme=True))
    assert review["action"] == "ALLOW"
