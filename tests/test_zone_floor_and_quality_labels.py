"""The entry-zone floor applies on every path, and each grade names itself.

2026-08-03, TRADE_20260803_000202_045834_bdde9a5f (3-AGENT CONSENSUS):

    🏅 Quality: A 89.7
    ...
    • Setup: Failed Reclaim Continuation · ... · quality D
    • Entry zone: 4063.55 → 4067.02

Two separate faults on one card.

FAULT 1 -- THE ZONE IS BELOW ITS OWN FLOOR
------------------------------------------
4063.55 → 4067.02 is 34.7 points against
``session_planner.min_entry_zone_width_points`` = 60.

The floor exists because the order rests at ONE price inside the area: on
2026-07-30 a BUY zone was touched 21 points from the reference entry without
filling and price then ran through TP1.

``run_analysis._build_plan_ladder_decision`` already honours it -- that was
fixed earlier. But the consensus and dual-agent paths build their signal in
``RiskManagementAgent``, which read the raw POI. One setting, one intent, and
only one of the two paths obeying it.

Widening is symmetric around the reference entry, so the mapped price keeps
its place and no risk moves: zone_touch_activation carries the stop the same
distance it moves the entry.

FAULT 2 -- TWO GRADES, ONE WORD
-------------------------------
"Quality: A 89.7" is the decision agent's grade for the whole signal
(confidence, reward, session, news). "quality D" is SMCAgent's grade for the
POI alone. They measure different things and may legitimately disagree --
but printed as two bare letters on one card they read as a contradiction,
and neither number was wrong.

This is a labelling fault, not an arithmetic one, so the fix is to name the
subject rather than to force the numbers together. Deleting either would
destroy real information.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.risk_management_agent import RiskManagementAgent  # noqa: E402
from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()
FLOOR = float(CONFIG["session_planner"]["min_entry_zone_width_points"])

# The zone exactly as it was published.
RAW_LOW, RAW_HIGH = 4063.55, 4067.02
ENTRY = 4065.28


def _agent(config=None) -> RiskManagementAgent:
    return RiskManagementAgent(config or CONFIG)


def _zone(low=RAW_LOW, high=RAW_HIGH, entry=ENTRY, agent=None, **extra):
    payload = {
        "low": low, "high": high,
        "proximal": high, "distal": low,
        "fill_at": "mid", "source": "order_block",
    }
    payload.update(extra)
    return (agent or _agent())._entry_zone_with_floor(
        payload, entry_price=entry, atr=2.0
    )


def _width(zone) -> float:
    return round(abs(zone["high"] - zone["low"]) * 10, 1)


# ── fault 1: the zone floor ─────────────────────────────────────────────────

def test_the_published_zone_was_below_the_floor() -> None:
    """Establish the premise from the card and the config."""
    assert round((RAW_HIGH - RAW_LOW) * 10, 1) == 34.7
    assert 34.7 < FLOOR == 60.0


def test_the_zone_is_widened_to_the_floor() -> None:
    zone = _zone()
    assert _width(zone) == FLOOR
    assert zone["widened_to_min_width"] is True


def test_the_reference_entry_stays_inside_and_centred() -> None:
    zone = _zone()
    assert zone["low"] <= ENTRY <= zone["high"]
    below = round(ENTRY - zone["low"], 2)
    above = round(zone["high"] - ENTRY, 2)
    assert abs(below - above) <= 0.05, (
        f"entry sits {below} below / {above} above; widening must keep the "
        "mapped price where the analysis put it"
    )


def test_proximal_and_distal_follow_their_own_edges() -> None:
    """The stop sits behind the distal edge; it must move with the zone."""
    zone = _zone()
    assert zone["distal"] == zone["low"], "distal was the lower edge"
    assert zone["proximal"] == zone["high"], "proximal was the upper edge"


def test_a_sell_zone_keeps_its_orientation() -> None:
    """For a SELL the proximal edge is the lower one; it must not flip."""
    zone = _zone(proximal=RAW_LOW, distal=RAW_HIGH)
    assert _width(zone) == FLOOR
    assert zone["proximal"] == zone["low"]
    assert zone["distal"] == zone["high"]


def test_an_already_wide_zone_is_untouched() -> None:
    zone = _zone(low=4050.0, high=4070.0, entry=4060.0)
    assert zone["low"] == 4050.0 and zone["high"] == 4070.0
    assert "widened_to_min_width" not in zone


def test_the_floor_reads_the_same_setting_the_planner_uses() -> None:
    """One setting, one behaviour -- not a second number to keep in sync."""
    config = load_config()
    config["session_planner"]["min_entry_zone_width_points"] = 100
    assert _width(_zone(agent=_agent(config))) == 100.0


def test_disabling_the_floor_publishes_the_raw_poi() -> None:
    config = load_config()
    config["session_planner"]["min_entry_zone_width_points"] = 0
    zone = _zone(agent=_agent(config))
    assert zone["low"] == RAW_LOW and zone["high"] == RAW_HIGH


def test_inverted_edges_are_normalised() -> None:
    zone = _zone(low=RAW_HIGH, high=RAW_LOW)
    assert zone["low"] < zone["high"]
    assert _width(zone) == FLOOR


def test_the_zone_never_moves_the_entry_price() -> None:
    """Widening publishes a reachable area; it must not reprice the order."""
    zone = _zone()
    assert "price" not in zone


# ── fault 2: the two grades ─────────────────────────────────────────────────

def _bot_source() -> str:
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "services", "telegram_bot.py",
    )
    return open(path, encoding="utf-8").read()


def test_the_poi_grade_names_its_subject() -> None:
    source = _bot_source()
    assert 'f"POI quality {quality}"' in source, (
        "a bare 'quality D' beside a header 'Quality: A' reads as a "
        "contradiction; the POI grade must say what it grades"
    )
    assert 'compact.append(f"quality {quality}")' not in source


def test_the_signal_grade_names_its_subject() -> None:
    source = _bot_source()
    assert '"Setup quality" if planner_led else "Signal quality"' in source
    assert '"Setup quality" if planner_led else "Quality"' not in source


def test_both_grades_are_still_shown() -> None:
    """Naming them must not delete either -- both carry real information."""
    source = _bot_source()
    assert "POI quality" in source
    assert "Signal quality" in source


def test_fault_injection_the_raw_poi_breaks_the_floor() -> None:
    """Reproduce the pre-fix expression and show it publishes 34.7 pts."""
    raw_low = min(RAW_LOW, RAW_HIGH)
    raw_high = max(RAW_LOW, RAW_HIGH)
    old_width = round((raw_high - raw_low) * 10, 1)

    assert old_width == 34.7 and old_width < FLOOR, (
        "reading the POI unmodified violates min_entry_zone_width_points "
        "every time a narrow POI is selected"
    )
    assert _width(_zone()) > old_width, (
        "the shipped zone must differ from the raw POI, or the floor is "
        "still being bypassed on this path"
    )
