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


def test_resolver_looks_only_at_nearest_and_next_pools() -> None:
    # Directive 2026-08-06: TP2 must be a real second objective. Risk 15.0
    # (entry 4060, stop 4045): liq1=4080 (1.33R); 4085 (1.67R) is glued to TP1
    # (0.34R gap < 0.5R) so the resolver scans to 4105 (1.67R beyond TP1).
    tp1, tp2, reject = ra._resolve_reward_target(
        "BUY", 4060.0, 4045.0, 4080.0, BUY_CANDIDATE,
        min_rr=1.5, min_tp1_rr=0.8, prefer_far=True, max_rr=4.0,
    )
    assert reject is None
    assert tp2 == 4105.0, "TP2 must not be glued to TP1; scan to a real second pool"
    assert tp1 == 4080.0, "TP1 stays the nearest usable level"


def test_a_third_pool_is_ignored_even_if_it_clears_min_rr() -> None:
    # The 3rd-nearest pool (4300, 16R) clears min_rr, but only the nearest two
    # are considered -- so the leg is REJECTED honestly, not stretched to it.
    candidate = {"details": {"liquidity": {"buy_side": [4066.0, 4070.0, 4300.0]}}}
    tp1, tp2, reject = ra._resolve_reward_target(
        "BUY", 4060.0, 4045.0, 4066.0, candidate,
        min_rr=1.5, min_tp1_rr=0.8, prefer_far=True, max_rr=4.0,
    )
    assert reject is not None, "nearest two fail min_rr; the far 3rd pool must not rescue it"


def test_resolver_nearest_first_is_restorable() -> None:
    tp1, tp2, reject = ra._resolve_reward_target(
        "BUY", 4060.0, 4045.0, 4080.0, BUY_CANDIDATE,
        min_rr=1.5, min_tp1_rr=0.8, prefer_far=False, max_rr=4.0,
    )
    assert reject is None
    assert tp2 == 4105.0  # min-separation rule applies regardless of prefer_far


def test_resolver_falls_back_to_nearest_when_only_overcap_pools_qualify() -> None:
    candidate = {"details": {"liquidity": {"buy_side": [4150.0]}}}  # 6.0R, beyond cap 4.0
    tp1, tp2, reject = ra._resolve_reward_target(
        "BUY", 4060.0, 4045.0, 4150.0, candidate,
        min_rr=1.5, min_tp1_rr=0.8, prefer_far=True, max_rr=4.0,
    )
    # A single pool beyond max_rr is unreasonable; the leg is rejected, not stretched.
    assert reject is not None
    assert tp2 == 0.0


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
    assert tp2 == 4105.0, "classic path must aim TP2 at the farthest pool within the cap"
    assert tp1 == 4085.0, "TP1 books the nearest pool short of the far objective"


def test_risk_agent_chain_nearest_first_is_restorable() -> None:
    agent = _agent(prefer_far=False)
    tp1, tp2, method = agent._liquidity_chain_targets(
        direction="BUY", entry=4060.0, stop_loss=4045.0,
        liquidity_map={"buy_side": [4085.0, 4105.0, 4130.0]},
        supports=[], resistances=[], atr=2.0,
    )
    assert tp2 == 4085.0


def test_config_pins_the_directive_on() -> None:
    from utils.helpers import load_config
    cfg = load_config()
    assert bool(cfg["risk_settings"].get("prefer_far_liquidity", True)) is True
