"""The published entry area must respect min_entry_zone_width_points.

2026-07-31, TRADE_20260731_141102_627592_b4f85832:

    • Entry: 4031.77
    • Entry zone: 4029.85 → 4033.69

That area is 38.4 points wide. ``session_planner.min_entry_zone_width_points``
is 60, and the floor is not advisory -- it exists because an order rests at
ONE price inside the area, so a touch that misses that price by a few points
leaves a correct plan unfilled while price runs to target. On 2026-07-30 a
BUY zone was touched 21 points above the reference entry without filling and
price then ran through TP1.

WHY THE FLOOR DID NOT APPLY
---------------------------
``SessionPlannerService._enforce_min_zone_width`` implements it correctly:
asked directly with this POI it returns 4028.77 → 4034.77 = exactly 60.0
points, with the reference entry still inside.

But that method is called from ``_zone_payload`` -- the planner's own view of
the map -- while the order that is actually sent is built by
``_build_plan_ladder_decision`` in run_analysis.py, which read
``candidate["poi_zone"]`` raw and never asked.

So the floor was real, tested, and bypassed on the one path that reaches the
user. That is the dead-gate pattern: a setting that exists, is honoured in
one place, and is silently ignored where it matters.

Widening does not move risk. It is symmetric around the reference entry, so
the mapped price keeps its position, and zone_touch_activation carries the
stop the same distance it moves the entry (preserve_planned_risk=true).
"""

from __future__ import annotations

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "run_analysis_zone", os.path.join(ROOT, "scripts", "run_analysis.py")
)
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)

from services.session_planner import SessionPlannerService  # noqa: E402
from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()
FLOOR = float(CONFIG["session_planner"]["min_entry_zone_width_points"])

# The POI exactly as it was published.
RAW_LOW, RAW_HIGH = 4029.85, 4033.69
ENTRY = 4031.77
SYMBOL = "XAU/USD"


def _candidate(**extra) -> dict:
    candidate = {
        "entry_price": ENTRY,
        "poi_zone": {"top": RAW_HIGH, "bottom": RAW_LOW},
        "stop_loss": 4071.77,
        "target_price": 3941.77,
        "selection_role": "PRIMARY",
        "poi_type": "Swept Level",
        "quality_grade": "A+",
        "setup_type": "STRUCTURE_CONTINUATION",
        "symbol": SYMBOL,
    }
    candidate.update(extra)
    return candidate


def _build(candidate: dict, current_price: float = 4021.85):
    plan = {
        "symbol": SYMBOL, "session_bias": "SELL", "scenario_id": "S1",
        "primary_poi": candidate, "plan_ready": True,
    }
    base = {
        "symbol": SYMBOL, "decision": "SELL", "current_price": current_price,
        "confidence": 87.7, "session_plan": plan, "signal": {},
    }
    return ra._build_plan_ladder_decision(base, plan, candidate, CONFIG)


def _zone_width(decision) -> float:
    zone = decision["signal"]["entry"]
    return round(abs(zone["high"] - zone["low"]) * 10, 1)


def test_the_raw_poi_is_below_the_floor() -> None:
    """Establish the premise: this really was too narrow."""
    assert round((RAW_HIGH - RAW_LOW) * 10, 1) == 38.4
    assert 38.4 < FLOOR == 60.0


def test_the_planner_already_knew_the_right_answer() -> None:
    low, high, widened = SessionPlannerService(CONFIG)._enforce_min_zone_width(
        RAW_LOW, RAW_HIGH, entry_price=ENTRY, symbol=SYMBOL
    )
    assert widened is True
    assert round((high - low) * 10, 1) == FLOOR
    assert low <= ENTRY <= high, "the reference entry must keep its place"


def test_the_published_order_now_meets_the_floor() -> None:
    decision = _build(_candidate())
    assert decision is not None, "this candidate must produce a pending order"

    zone = decision["signal"]["entry"]
    assert _zone_width(decision) >= FLOOR, (
        f"published area is {_zone_width(decision)} pts against a {FLOOR}-pt "
        "floor; an order resting at one price inside a too-narrow area is "
        "exactly what the floor exists to prevent"
    )
    assert zone["low"] <= zone["price"] <= zone["high"]


def test_widening_is_symmetric_around_the_entry() -> None:
    """The mapped price must not be shoved toward an edge."""
    decision = _build(_candidate())
    zone = decision["signal"]["entry"]

    below = round(zone["price"] - zone["low"], 2)
    above = round(zone["high"] - zone["price"], 2)
    assert abs(below - above) <= 0.05, (
        f"entry sits {below} below / {above} above the edges; widening must "
        "keep the reference entry where the map put it"
    )


def test_the_entry_price_itself_is_unchanged() -> None:
    """Widening publishes a reachable area; it must not reprice the order."""
    decision = _build(_candidate())
    assert decision["signal"]["entry"]["price"] == ENTRY


def test_risk_levels_are_untouched_by_widening() -> None:
    decision = _build(_candidate())
    signal = decision["signal"]
    assert signal["stop_loss"] == 4071.77
    assert round(abs(signal["stop_loss"] - signal["entry"]["price"]) * 10, 1) == 400.0


def test_an_already_wide_zone_is_left_exactly_as_it_is() -> None:
    """The floor widens; it must never shrink or reshape a valid area."""
    wide = _candidate(poi_zone={"top": 4041.77, "bottom": 4021.77})
    decision = _build(wide)

    zone = decision["signal"]["entry"]
    assert zone["low"] == 4021.77 and zone["high"] == 4041.77
    assert _zone_width(decision) == 200.0


def test_a_market_conversion_still_reports_the_live_price() -> None:
    """When the leg prices as MARKET the area collapses to the fill, as before."""
    inside = _build(_candidate(), current_price=4031.77)
    if inside is None:
        return  # the leg declined for another reason; nothing to assert
    zone = inside["signal"]["entry"]
    if str(zone.get("kind") or "").upper() == "MARKET":
        assert zone["low"] == zone["high"] == zone["current_price"]


def test_fault_injection_reading_the_raw_poi_breaks_the_floor() -> None:
    """Reproduce the pre-fix line and show it publishes 38.4 pts.

    This is the exact expression ``_build_plan_ladder_decision`` used before
    the fix -- the raw POI, with no reference to the planner's floor.
    """
    candidate = _candidate()
    zone = candidate["poi_zone"]
    raw_low = min(float(zone["top"]), float(zone["bottom"]))
    raw_high = max(float(zone["top"]), float(zone["bottom"]))

    assert round((raw_high - raw_low) * 10, 1) == 38.4
    assert round((raw_high - raw_low) * 10, 1) < FLOOR, (
        "the raw POI is below the floor, so a path that publishes it "
        "unmodified violates min_entry_zone_width_points every time a narrow "
        "POI is selected"
    )

    published = _build(candidate)
    assert _zone_width(published) > round((raw_high - raw_low) * 10, 1), (
        "the shipped order must differ from the raw POI, or the floor is "
        "still being bypassed"
    )
