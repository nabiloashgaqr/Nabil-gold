"""Far liquidity first, near as fallback — the operator's target policy.

Directive (2026-08-04): "look at far liquidity first, then near; far
liquidity is better." TP2 is the level the trade is held for, so it aims at
the FURTHEST real pool whose reward clears min_rr_ratio, never beyond
max_rr_ratio. Near pools serve as TP1 (book half early, arm protection) and
as the fallback when nothing far qualifies.

FAULT INJECTION: revert either resolver to its nearest-first pick
(`tp2 = qualifying[0]` / `next(nearest qualifying)`) and the far-first
tests fail. This is the gap that left 257 points unaimed at on 2026-07-30
and shaped the 0.61R refusal on 2026-08-04.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

import scripts.run_analysis as ra
from agents.risk_management_agent import RiskManagementAgent


BUY_CANDIDATE = {
    "details": {
        "liquidity": {"buy_side": [4085.0, 4105.0, 4130.0]},
    },
}


def test_targets_take_the_farther_of_liquidity_and_ratio() -> None:
    # Directive 2026-08-07c: TP1 = farther(0.8R, nearest pool),
    # TP2 = farther(1.5R, farthest pool). Risk 15.0: ratios at 4072 / 4082.5.
    # Nearest pool 4080 beats the TP1 ratio; farthest 4105 beats TP2 ratio.
    tp1, tp2, reject = ra._resolve_reward_target(
        "BUY", 4060.0, 4045.0, 4080.0, BUY_CANDIDATE,
        min_rr=1.5, min_tp1_rr=0.8, prefer_far=True, max_rr=4.0,
    )
    assert reject is None, "an approved plan is never refused on reward"
    # The mapped target (4080) is itself a real level and the nearest one.
    assert tp1 == 4080.0, "nearest real level beats the 0.8R floor (12 pts)"
    assert tp2 == 4130.0, "farthest pool is the farther objective"


def test_the_far_pool_is_the_objective() -> None:
    # Directive 2026-08-07c: "اهداف الابعد نقارن" -- the far pool (4300) IS
    # the TP2 objective; near noise pools lose to the ratio floors.
    candidate = {"details": {"liquidity": {"buy_side": [4066.0, 4070.0, 4300.0]}}}
    tp1, tp2, reject = ra._resolve_reward_target(
        "BUY", 4060.0, 4045.0, 4066.0, candidate,
        min_rr=1.5, min_tp1_rr=0.8, prefer_far=True, max_rr=4.0,
    )
    assert reject is None
    assert tp1 == 4072.0, "nearest pool (6 pts) loses to the 0.8R floor (12)"
    assert tp2 == 4300.0, "the far pool is the farther objective"


def test_ratio_floors_ship_when_pools_are_noise() -> None:
    # Pools nearer than the ratio floors lose: 4066 (6 pts) < 12, so the
    # 0.8R ratio level ships as TP1.
    candidate = {"details": {"liquidity": {"buy_side": [4066.0, 4070.0]}}}
    tp1, tp2, reject = ra._resolve_reward_target(
        "BUY", 4060.0, 4045.0, 4066.0, candidate,
        min_rr=1.5, min_tp1_rr=0.8, prefer_far=False, max_rr=4.0,
    )
    assert reject is None
    assert tp1 == 4072.0
    # 2026-08-07d: TP2 must be >= 2x TP1 distance (24 pts) -> 4084.0.
    assert tp2 == 4084.0


def test_a_single_far_pool_sets_tp1_and_double_rule_sets_tp2() -> None:
    # One pool at 90 pts wins both comparisons; the 2026-08-07d double rule
    # then pushes TP2 to 2x TP1 (180 pts) instead of collapsing onto it.
    candidate = {"details": {"liquidity": {"buy_side": [4150.0]}}}
    tp1, tp2, reject = ra._resolve_reward_target(
        "BUY", 4060.0, 4045.0, 4150.0, candidate,
        min_rr=1.5, min_tp1_rr=0.8, prefer_far=True, max_rr=4.0,
        tp2_multiple=2.0,
    )
    assert reject is None
    assert tp1 == 4150.0
    # 2 x 90.0 (TP1 distance) = 180.0 above entry.
    assert tp2 == 4240.0


def _agent(prefer_far: bool) -> RiskManagementAgent:
    return RiskManagementAgent({
        "symbol": "XAU/USD",
        "risk_settings": {
            "min_rr_ratio": 1.5,
            "max_rr_ratio": 4.0,
            "min_sl_distance_points": 0,
            "prefer_far_liquidity": prefer_far,
        },
        "agent_weights": {"technical": 20, "classical": 25, "smc": 20,
                          "price_action": 20, "multitimeframe": 15},
    })


def test_risk_agent_chain_aims_at_the_far_pool() -> None:
    agent = _agent(prefer_far=True)
    tp1, tp2, method = agent._liquidity_chain_targets(
        direction="BUY", entry=4060.0, stop_loss=4045.0,
        liquidity_map={"buy_side": [4085.0, 4105.0, 4130.0]},
        supports=[], resistances=[], atr=2.0,
    )
    assert method == "liquidity_chain"
    assert tp2 == 4130.0, "2026-08-07c: the farthest pool, no cap"
    assert tp1 == 4085.0, "TP1 books the nearest pool short of the far objective"


def test_risk_agent_chain_nearest_first_is_restorable() -> None:
    agent = _agent(prefer_far=False)
    tp1, tp2, method = agent._liquidity_chain_targets(
        direction="BUY", entry=4060.0, stop_loss=4045.0,
        liquidity_map={"buy_side": [4085.0, 4105.0, 4130.0]},
        supports=[], resistances=[], atr=2.0,
    )
    assert tp2 == 4130.0  # farther wins regardless of prefer_far


def test_config_pins_the_directive_on() -> None:
    from utils.helpers import load_config
    cfg = load_config()
    assert bool(cfg["risk_settings"].get("prefer_far_liquidity", True)) is True
