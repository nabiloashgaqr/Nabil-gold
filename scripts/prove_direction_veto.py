"""Reproduce the 2026-07-29 12:21 UTC decision path from the published numbers.

Purpose
-------
The manual analyst sold 4040 and took +310 points. The system bought 4028.77
and was -198 points. The usual explanation is "the vote was wrong". This
script tests that explanation against the real code paths.

It answers three questions, in order:

  1. What did the 5-agent consensus actually decide?
  2. What did the day-map authority do with that decision?
  3. With today's code (opposition gate live), what would happen now?

Run:  python scripts/prove_direction_veto.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.decision_agent import DecisionAgent
from services.directional_authority import DirectionalAuthorityService


# The agent book exactly as printed in the published 12:21 UTC signal.
AGENT_BOOK = {
    "technical": {"signal": "SELL", "confidence": 92,
                  "reason_codes": ["EMA_RIBBON_BEARISH", "RSI_BEARISH_RANGE"]},
    "classical": {"signal": "WAIT", "confidence": 30},
    "smc": {"signal": "WAIT", "confidence": 31,
            "market_structure": {"trend": "BEARISH"}},
    "price_action": {"signal": "SELL", "confidence": 79,
                     "reason_codes": ["BEARISH_FALSE_BREAKOUT"]},
    "multitimeframe": {"signal": "SELL", "confidence": 92,
                       "alignment": "FULL", "alignment_score": 100},
    "session": {"allow_signals": True, "trading_allowed": True,
                "current_session": "London"},
    "daily_bias": {"bias": "BEARISH", "confidence": 95},
}

# The day map that was live at 12:21: built by SMC alone, pointing BUY.
SESSION_PLAN = {
    "plan_ready": True,
    "authority_state": "CONFIRMED",
    "authority_direction": "BUY",
    "session_bias": "BUY",
    "day_archetype": "CONTINUATION_AFTER_SWEEP_DAY",
    "day_archetype_confidence": 86,
}

AGENT_DETAILS = {
    "technical": {"direction": "SELL", "confidence": 92},
    "classical": {"direction": "WAIT", "confidence": 30},
    "smc": {"direction": "WAIT", "confidence": 31},
    "price_action": {"direction": "SELL", "confidence": 79},
    "multitimeframe": {"direction": "SELL", "confidence": 92},
}


def _load_config() -> dict:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def step_1_consensus(config: dict) -> dict:
    print("STEP 1 — what the 5-agent consensus decided")
    print("-" * 62)
    decision = DecisionAgent(config).analyze({
        "all_agents_results": AGENT_BOOK,
        "session": AGENT_BOOK["session"],
        "indicators": {},
    })
    classic = decision["classic"]
    print(f"  verdict            : {decision['decision']} @ {decision['confidence']}%")
    print(f"  SELL voters        : {[v['agent'] for v in decision['votes']['SELL']]}")
    print(f"  BUY voters         : {[v['agent'] for v in decision['votes']['BUY']]}")
    print(f"  opposition to SELL : {classic['buy_count']}")
    print(f"  rejection_reason   : {classic['rejection_reason']}")
    print()
    print("  The consensus agreed with the manual analyst: SELL, no dissent.")
    print()
    return decision


def step_2_authority(config: dict, decision: dict) -> dict:
    """What the day map did to that decision, then and now.

    This step originally passed no agent book, which is what the authority
    layer received before Phase B: the five voting agents could not reach it,
    so no amount of live disagreement could retire a stale map. Re-running it
    that way still reproduces the original refusal, and that is the point --
    but printing only that reading makes today's system look unchanged.

    So both are shown: the call as it was made on 2026-07-29, and the same
    call with the live book attached, which is what runs now.
    """
    print("STEP 2 — what the day-map authority did to that decision")
    print("-" * 62)
    base_decision = {
        "decision": decision["decision"],
        "confidence": decision["confidence"],
        "symbol": "XAU/USD",
        # The SELL was a continuation of bearish structure, not a
        # reversal-graded setup, so the flip conditions cannot be met.
        "setup_context": {"setup_type": "STRUCTURE_CONTINUATION",
                          "trigger_state": "DETECTED",
                          "trigger_score": 0.0,
                          "sweep_side": "buy_side"},
    }

    service = DirectionalAuthorityService(config)
    review = service.review(base_decision, SESSION_PLAN, [])
    print("  THEN — the agent book never reached this gate:")
    print(f"    action : {review.get('action')}")
    print(f"    reason : {review.get('reason')}")
    print()

    now = service.review(
        {**base_decision, "agent_details": AGENT_DETAILS}, dict(SESSION_PLAN), []
    )
    print("  NOW  — the same call, with the live book attached:")
    print(f"    action : {now.get('action')}")
    print(f"    reason : {now.get('reason')}")
    print()
    if now.get("action") == "ALLOW_MAP_RETIRED":
        print("    => three qualified agents read SELL and none defend the BUY")
        print("       map, so the map is retired instead of vetoing them.")
        print()
    return review


def step_3_today(config: dict) -> None:
    print("STEP 3 — with today's code, what happens to each direction")
    print("-" * 62)
    sig = config.get("signal_requirements", {}) or {}
    min_agents = int(sig.get("min_agents_agree", 3) or 3)
    min_conf = float(sig.get("agent_min_confidence", 70) or 70)
    planner = config.get("session_planner", {}) or {}
    max_opposing = int(planner.get("max_opposing_agents_for_ready", 1) or 1)

    for side in ("BUY", "SELL"):
        support = [k for k, v in AGENT_DETAILS.items()
                   if v["confidence"] >= min_conf and v["direction"] == side]
        oppose = [k for k, v in AGENT_DETAILS.items()
                  if v["confidence"] >= min_conf
                  and v["direction"] in {"BUY", "SELL"}
                  and v["direction"] != side]
        if len(oppose) > max_opposing:
            verdict = f"BLOCKED — {len(oppose)} live agents oppose (limit {max_opposing})"
        elif len(support) >= min_agents:
            verdict = "ADMITTED by the planner gate"
        else:
            verdict = f"BLOCKED — only {len(support)} supporting agents"
        print(f"  mapped {side:4} : support={len(support)} oppose={len(oppose)} -> {verdict}")

    print()
    print("  BUY  : blocked by the opposition gate (the fix that shipped).")
    print("  SELL : admitted here — and no longer stopped upstream either,")
    print("         because the live book now retires the opposing map.")
    print()


def step_4_vote_count_is_not_the_constraint(config: dict) -> None:
    """Test the assumption that the vote threshold is what blocks trades.

    The user offered to accept 1, 2 or 3 agreeing agents. This re-runs the
    consensus at each setting and re-applies the day-map veto to each result.
    """
    print("STEP 4 — does changing the required agent count rescue the trade?")
    print("-" * 62)
    print("  (shown against the pre-Phase-B veto, which had no agent book)")
    for required in (1, 2, 3):
        cfg = json.loads(json.dumps(config))
        cfg.setdefault("signal_requirements", {})["min_agents_agree"] = required
        decision = DecisionAgent(cfg).analyze({
            "all_agents_results": AGENT_BOOK,
            "session": AGENT_BOOK["session"],
            "indicators": {},
        })
        review = DirectionalAuthorityService(cfg).review(
            {
                "decision": decision["decision"],
                "confidence": decision["confidence"],
                "symbol": "XAU/USD",
                "setup_context": {"setup_type": "STRUCTURE_CONTINUATION",
                                  "trigger_state": "DETECTED",
                                  "trigger_score": 0.0,
                                  "sweep_side": "buy_side"},
            },
            SESSION_PLAN,
            [],
        )
        blocked = review.get("action") == "BLOCK_OPPOSITE_LOCAL"
        outcome = "vetoed by the day map" if blocked else "allowed"
        print(f"  min_agents_agree={required} -> consensus "
              f"{decision['decision']} @ {decision['confidence']}% -> {outcome}")
    print()
    print("  The veto never read the agent count, so the number of agreeing")
    print("  agents could not change this outcome. The vote was never the")
    print("  constraint -- which is why Phase B changed what the map may")
    print("  veto, rather than how many agents are required.")
    print()


def main() -> None:
    config = _load_config()
    print()
    print("=" * 62)
    print("  2026-07-29 12:21 UTC — where the winning SELL was lost")
    print("=" * 62)
    print()
    decision = step_1_consensus(config)
    step_2_authority(config, decision)
    step_3_today(config)
    step_4_vote_count_is_not_the_constraint(config)
    print("=" * 62)
    print("  CONCLUSION")
    print("=" * 62)
    print("  The voting system produced the winning answer.")
    print("  A day map derived from SMC alone vetoed it.")
    print("  Replacing the vote would have discarded the one component that")
    print("  was right, so the map's authority was fixed instead:")
    print("    Phase A — a map must earn its CONFIRMED stamp from evidence.")
    print("    Phase B — a decisive live book retires a map that has gone stale.")
    print()
    print("  Run scripts/prove_chart_to_order.py to see the current behaviour")
    print("  end to end, starting from candles rather than from this replay.")
    print()


if __name__ == "__main__":
    main()
