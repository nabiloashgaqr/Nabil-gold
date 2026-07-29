"""Prove a stale day map can now be retired by the live agent book.

Phase A stopped maps inventing authority. This shows the remaining case: a
map that genuinely earned authority hours ago, then went stale while the
agents turned against it.

Run:  python scripts/prove_map_retirement.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.day_map_sanity import DayMapSanityService
from services.directional_authority import DirectionalAuthorityService
from services.session_planner import SessionPlannerService


def _config() -> dict:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


BOOK = {
    "technical": {"direction": "SELL", "confidence": 92},
    "price_action": {"direction": "SELL", "confidence": 79},
    "multitimeframe": {"direction": "SELL", "confidence": 92},
    "classical": {"direction": "WAIT", "confidence": 30},
    "smc": {"direction": "WAIT", "confidence": 31},
}


def _decision() -> dict:
    return {
        "decision": "SELL",
        "confidence": 91.0,
        "symbol": "XAU/USD",
        "current_price": 4021.96,
        "agent_details": BOOK,
        "signal": {"order_type": "SELL_MARKET", "entry": {"price": 4021.96}},
        "setup_context": {"setup_type": "STRUCTURE_CONTINUATION",
                          "trigger_state": "DETECTED", "trigger_score": 0.0,
                          "sweep_side": "buy_side"},
    }


def main() -> None:
    config = _config()

    earned = SessionPlannerService(config)._resolve_authority(
        daily_bias={"bias": "BULLISH", "confidence": 80},
        macro={"bias": "NEUTRAL", "confidence": 50},
        market_structure={"trend": "BULLISH", "structure_quality": "STRONG"},
        recent_sweep={"occurred": True, "type": "sell_side",
                      "confirmation": "STRONG"},
        zone_context="DISCOUNT", reversal_watch={},
    )

    print()
    print("=" * 62)
    print("  A map that EARNED authority, then went stale")
    print("=" * 62)
    print()
    print(f"  02:00  map built : {earned['state']} {earned['direction']} "
          f"from {earned['sources']}")
    print("  14:00  the book  : 3 qualified agents read SELL, none read BUY")
    print()

    for label, retirement in (("BEFORE  (map cannot be outvoted)", False),
                              ("AFTER   (live book can retire it)", True)):
        cfg = json.loads(json.dumps(config))
        cfg["directional_authority"]["allow_live_book_retirement"] = retirement

        plan = {"plan_ready": True,
                "authority_state": earned["state"],
                "authority_direction": earned["direction"]}

        service = DirectionalAuthorityService(cfg)
        review = service.review(_decision(), plan, [])
        print(f"  {label}")
        print("  " + "-" * 58)
        print(f"   authority gate : {review['action']}")

        if review["action"] == "BLOCK_OPPOSITE_LOCAL":
            print("                    => SELL cancelled, rewritten to WAIT")
            print("\n   OUTCOME: the map wins. No trade.\n")
            continue

        service.apply_retirement(plan, review)
        print(f"   plan stamp now : {plan['authority_state']}")

        sanity = DayMapSanityService(cfg).review(_decision(), plan)
        print(f"   day-map sanity : {sanity['action']}")
        if sanity["action"] != "ALLOW":
            print(f"                    {sanity['reason']}")
            print("\n   OUTCOME: blocked one gate later.\n")
            continue
        print("\n   OUTCOME: SELL survives. The map was retired, not obeyed.\n")

    print("=" * 62)
    print("  The bar for retirement")
    print("=" * 62)
    print("   - 3 qualified agents (same count a new plan needs)")
    print("   - zero qualified agents still backing the map")
    print("   - no live trades riding the map")
    print("   Anything less and the map holds.")
    print()


if __name__ == "__main__":
    main()
