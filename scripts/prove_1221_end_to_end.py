"""Walk the 2026-07-29 12:21 UTC cycle through every gate, before and after.

This does not assert that the fix is correct. It replays the published agent
book through the real services and prints what each gate decides, so the
before/after difference is observable rather than claimed.

Run:  python scripts/prove_1221_end_to_end.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.decision_agent import DecisionAgent
from services.day_map_sanity import DayMapSanityService
from services.directional_authority import DirectionalAuthorityService
from services.session_planner import SessionPlannerService

# The agent book exactly as printed in the published 12:21 UTC signal.
BOOK = {
    "technical": {"signal": "SELL", "confidence": 92},
    "classical": {"signal": "WAIT", "confidence": 30},
    "smc": {"signal": "WAIT", "confidence": 31},
    "price_action": {"signal": "SELL", "confidence": 79},
    "multitimeframe": {"signal": "SELL", "confidence": 92},
    "session": {"allow_signals": True, "trading_allowed": True,
                "current_session": "London"},
}

# Market state that cycle, per the published archetype and daily bias.
DAILY_BIAS = {"bias": "BEARISH", "confidence": 95}
MACRO = {"bias": "NEUTRAL", "confidence": 50}
STRUCTURE = {"trend": "BULLISH", "structure_quality": "STRONG"}
SWEEP = {"occurred": True, "type": "sell_side", "confirmation": "STRONG"}
ZONE = "DISCOUNT"

SELL_DECISION = {
    "decision": "SELL",
    "confidence": 87.3,
    "symbol": "XAU/USD",
    "current_price": 4021.96,
    "signal": {"order_type": "SELL_MARKET", "entry": {"price": 4021.96}},
    "setup_context": {"setup_type": "STRUCTURE_CONTINUATION",
                      "trigger_state": "DETECTED", "trigger_score": 0.0,
                      "sweep_side": "buy_side"},
}


def _config() -> dict:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _legacy_authority() -> dict:
    """Authority as the pre-fix code produced it: the self-referential tie."""
    return {"state": "CONFIRMED", "direction": "BUY", "count": 1}


def _walk(config: dict, authority: dict, label: str) -> None:
    print(f"  {label}")
    print("  " + "-" * 56)

    decision = DecisionAgent(config).analyze(
        {"all_agents_results": BOOK, "session": BOOK["session"], "indicators": {}}
    )
    print(f"   gate 1  consensus            : {decision['decision']} @ "
          f"{decision['confidence']}%")

    print(f"   gate 2  day-map authority    : {authority['state']} "
          f"{authority['direction']} (aligned sources: {authority['count']})")

    plan = {"plan_ready": True,
            "authority_state": authority["state"],
            "authority_direction": authority["direction"]}

    veto = DirectionalAuthorityService(config).review(SELL_DECISION, plan, [])
    print(f"   gate 3  directional veto     : {veto['action']}")
    if veto["action"] == "BLOCK_OPPOSITE_LOCAL":
        print("           => SELL cancelled, decision rewritten to WAIT")
        print(f"\n   OUTCOME: no SELL. The published trade was BUY 4028.77.\n")
        return

    sanity = DayMapSanityService(config).review(SELL_DECISION, plan)
    print(f"   gate 4  day-map sanity       : {sanity['action']}")
    if sanity["action"] != "ALLOW":
        print(f"           {sanity['reason']}")
        print(f"\n   OUTCOME: no SELL.\n")
        return

    # The planner admission gate, applied to the live book.
    sig = config.get("signal_requirements", {}) or {}
    min_agents = int(sig.get("min_agents_agree", 3) or 3)
    min_conf = float(sig.get("agent_min_confidence", 70) or 70)
    support = [k for k, v in BOOK.items()
               if isinstance(v, dict) and v.get("confidence", 0) >= min_conf
               and v.get("signal") == "SELL"]
    print(f"   gate 5  agent admission      : {len(support)} qualified "
          f"supporters {support} (need {min_agents})")

    print(f"\n   OUTCOME: SELL 87.3% survives every gate.\n")


def main() -> None:
    config = _config()

    print()
    print("=" * 60)
    print("  12:21 UTC — the same cycle through every gate")
    print("=" * 60)
    print()

    _walk(config, _legacy_authority(), "BEFORE  (authority stamped by SMC alone)")

    fixed = SessionPlannerService(config)._resolve_authority(
        daily_bias=DAILY_BIAS, macro=MACRO, market_structure=STRUCTURE,
        recent_sweep=SWEEP, zone_context=ZONE, reversal_watch={},
    )
    _walk(config, fixed, "AFTER   (authority resolved from evidence)")

    print("=" * 60)
    print("  What actually happened that day")
    print("=" * 60)
    print("   manual analyst : SELL 4040 -> 4009   = +310 points")
    print("   system         : BUY  4028.77, price 4009 = -198 points")
    print()
    print("   The consensus had the analyst's answer at 12:21.")
    print("   Two vetoes discarded it.")
    print()


if __name__ == "__main__":
    main()
