"""Operator directive 2026-08-07b — the liquidity rule for stops.

    سيولة تحت 200 نقطة تُتجاهل ويُبحث عن الأبعد؛ الستوب = أول سيولة مؤهلة
    (200-400) + 70 نقطة أمان؛ أول مؤهلة أبعد من 400 أو لا شيء مؤهل = 400
    مباشرة. النطاق المسموح [270, 400]. الأهداف هجين: السيولة إن حققت
    min_rr_ratio ضد الستوب، وإلا مضاعفات الستوب.

Replaces the three dead eras (flat 400, x3 multiplier, 70-clamp). Reintroducing
any of them -- or a stop outside [270, 400] -- fails here. The rule function is
tested PURE (no _smart_entry interference); the doors' agreement and the hybrid
targets are tested through _planner_trade_levels.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.risk_management_agent import RiskManagementAgent  # noqa: E402
from scripts.run_analysis import _planner_trade_levels  # noqa: E402
from utils.instruments import price_to_points  # noqa: E402

SYMBOL = "XAU/USD"
ENTRY = 4037.48
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
RULE = CFG["risk_settings"]["stop_from_liquidity"]


def lv(points: float) -> float:
    """A SELL-side pool `points` above entry (0.10 USD per point)."""
    return ENTRY + points * 0.10


def rule_pts(levels) -> float:
    agent = RiskManagementAgent(copy_cfg())
    return agent._stop_from_liquidity_points(
        "SELL", ENTRY, {"buy_side": list(levels)}, RULE)


def copy_cfg():
    return json.loads(json.dumps(CFG))


# ---------------------------------------------------------------------------
# The band, pure.
# ---------------------------------------------------------------------------

def test_pool_at_250_gives_320():
    assert rule_pts([lv(250)]) == pytest.approx(320.0, abs=0.5)


def test_pool_at_350_caps_at_400():
    """350 + 70 = 420 -> the 400 cap (the operator's own example)."""
    assert rule_pts([lv(350)]) == pytest.approx(400.0, abs=0.5)


def test_pool_beyond_400_ships_400_directly():
    assert rule_pts([lv(500)]) == pytest.approx(400.0, abs=0.5)


def test_only_noise_pools_ship_400():
    assert rule_pts([lv(50), lv(120), lv(199)]) == pytest.approx(400.0, abs=0.5)


def test_no_pools_ship_400():
    assert rule_pts([]) == pytest.approx(400.0, abs=0.5)


def test_nearest_eligible_wins_not_nearest_pool():
    assert rule_pts([lv(120), lv(250)]) == pytest.approx(320.0, abs=0.5)


def test_minimum_possible_stop_is_270():
    assert rule_pts([lv(200)]) == pytest.approx(270.0, abs=0.5)


def test_wrong_side_liquidity_is_invisible():
    """sell_side pools must not steer a SELL stop."""
    agent = RiskManagementAgent(copy_cfg())
    pts = agent._stop_from_liquidity_points(
        "SELL", ENTRY, {"sell_side": [ENTRY - 25]}, RULE)
    assert pts == pytest.approx(400.0, abs=0.5)


def test_buy_side_mirror():
    agent = RiskManagementAgent(copy_cfg())
    pts = agent._stop_from_liquidity_points(
        "BUY", ENTRY, {"sell_side": [ENTRY - 25]}, RULE)
    assert pts == pytest.approx(320.0, abs=0.5)


# ---------------------------------------------------------------------------
# The planner door: rule stop + hybrid targets.
# ---------------------------------------------------------------------------

def _planner(stop_levels, target_levels, target):
    """SELL: the stop anchors on buy_side pools ABOVE entry; the targets come
    from sell_side pools BELOW entry."""
    candidate = {"direction": "SELL", "entry_price": ENTRY,
                 "details": {"liquidity": {"buy_side": list(stop_levels),
                                           "sell_side": list(target_levels)}}}
    return _planner_trade_levels(
        CFG, direction="SELL", entry_price=ENTRY, stop_loss=ENTRY + 5.0,
        target_price=target, symbol=SYMBOL, candidate=candidate)


def below(points: float) -> float:
    return ENTRY - points * 0.10


def test_planner_door_applies_the_rule_stop():
    out = _planner([lv(250)], [below(620)], below(620))
    pts = abs(price_to_points(ENTRY - float(out["stop_loss"]), SYMBOL))
    assert pts == pytest.approx(320.0, abs=1.0)


def test_hybrid_far_liquidity_keeps_the_map():
    """620 pts pays 1.59R against the 390-pt rule stop -> stays TP2."""
    out = _planner([lv(320)], [below(320), below(620)], below(620))
    assert float(out["tp2"]) == pytest.approx(below(620), abs=0.01)
    assert not out.get("reject_reason")


def test_hybrid_near_only_is_refused_by_the_planner_door():
    """The planner door never invents: pools under 0.8R -> explicit refusal.
    (The agent door provides the ratio fallback; hybrid by operator choice.)"""
    out = _planner([lv(100)], [below(100)], below(100))
    assert out.get("reject_reason")


# ---------------------------------------------------------------------------
# Config guard.
# ---------------------------------------------------------------------------

def test_config_carries_the_operator_numbers():
    assert RULE["min_liquidity_points"] == 200
    assert RULE["safety_buffer_points"] == 70
    assert RULE["max_stop_points"] == 400
    assert "dynamic_sl_floor" not in CFG["risk_settings"], (
        "dead eras must not linger in config"
    )
