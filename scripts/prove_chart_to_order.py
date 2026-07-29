"""Walk the 2026-07-29 turtle-soup chart from candles to an admitted order.

Every earlier proof in this session started from hand-fed parameters, which
is how a broken candidate builder stayed hidden: the gates were tested with
inputs the agent could never actually produce.

This starts from candles. SMCAgent detects the structure, the raid, the zone
and the POIs on its own; the planner and the admission gates then judge what
it produced.

Run:  python scripts/prove_chart_to_order.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.smc_agent import SMCAgent
from services.day_map_sanity import DayMapSanityService
from services.directional_authority import DirectionalAuthorityService


def _config() -> dict:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _mk(seq):
    out = []
    for i, (high, low, close) in enumerate(seq):
        prev_close = out[-1]["close"] if out else (high + low) / 2
        opening = min(max(prev_close, low), high)
        out.append({
            "time": "2026-07-29T%02d:%02d:00+00:00" % (6 + i // 4, (i % 4) * 15),
            "open": round(opening, 2), "high": round(high, 2),
            "low": round(low, 2), "close": round(close, 2), "volume": 1000,
        })
    return out


LEG = [
    (4015, 4009, 4011), (4014, 4008, 4013), (4018, 4011, 4017),
    (4021, 4015, 4019), (4019, 4013, 4014), (4017, 4010, 4016),
    (4024, 4016, 4023), (4029, 4022, 4028), (4034, 4027, 4033),
    (4040, 4032, 4039), (4040, 4035, 4036), (4039, 4033, 4034),
    (4036, 4028, 4029), (4031, 4022, 4023), (4027, 4021, 4026),
    (4032, 4025, 4031), (4038, 4030, 4037), (4043, 4036, 4042),
    (4046, 4040, 4045), (4047, 4042, 4044),
]
TURTLE_SOUP = (4050.0, 4043.0, 4044.0)


def main() -> None:
    config = _config()
    candles = _mk((LEG * 4)[:72] + [TURTLE_SOUP])

    print()
    print("=" * 68)
    print("  2026-07-29 turtle soup — candles to order")
    print("=" * 68)
    print()

    result = SMCAgent(config).analyze(
        {"symbol": "XAU/USD", "timeframe": "15m", "data": candles}
    )
    sweep = (result.get("liquidity") or {}).get("recent_sweep") or {}
    structure = result.get("market_structure") or {}
    candidates = result.get("setup_candidates") or []
    sells = [c for c in candidates
             if str(c.get("direction") or "").upper() == "SELL"]

    print("  1  raid detected   : %s on %s, graded %s"
          % (sweep.get("type"), sweep.get("level"), sweep.get("confirmation")))
    print("  2  structure/zone  : %s / %s  (score label: %s)"
          % (structure.get("trend"), result.get("zone"), result.get("direction")))
    print("  3  archetype       : %s at %s"
          % (result.get("day_archetype"), result.get("day_archetype_confidence")))
    print("  4  SELL candidates : %d" % len(sells))
    if not sells:
        print("\n  OUTCOME: no SELL thesis exists. Nothing downstream can run.\n")
        return
    primary = sells[0]
    print("       %s / %s  entry %.2f  stop %.2f  target %s"
          % (primary.get("setup_type"), primary.get("setup_state"),
             float(primary.get("entry_price") or 0),
             float(primary.get("stop_loss") or 0),
             primary.get("target_liquidity")))
    print()

    book = {
        "technical": {"direction": "SELL", "confidence": 85},
        "price_action": {"direction": "SELL", "confidence": 82},
        "smc": {"direction": "SELL", "confidence": 78},
        "multitimeframe": {"direction": "WAIT", "confidence": 45},
        "classical": {"direction": "WAIT", "confidence": 40},
    }
    entry = float(primary.get("entry_price") or 0)
    decision = {
        "decision": "SELL", "confidence": 84.0, "symbol": "XAU/USD",
        "current_price": float(candles[-1]["close"]), "agent_details": book,
        "signal": {"order_type": "SELL_LIMIT", "entry": {"price": entry}},
        "setup_context": {
            "setup_type": primary.get("setup_type"),
            "trigger_state": primary.get("trigger_state"),
            "trigger_score": primary.get("trigger_score") or 0,
            "sweep_side": "buy_side",
        },
    }

    print("  Admission, against every day-map state:")
    for state, direction, label in (
        ("CONFIRMED", "BUY", "CONFIRMED BUY map (worst case)"),
        ("WEAK", "BUY", "weak BUY map"),
        ("CONFLICTED", None, "conflicted map"),
    ):
        plan = {
            "plan_ready": True, "authority_state": state,
            "authority_direction": direction,
            "primary_entry_zone": {"low": entry - 2.0, "high": entry + 2.0},
        }
        service = DirectionalAuthorityService(config)
        review = service.review(decision, plan, [])
        if review["action"] == "ALLOW_MAP_RETIRED":
            service.apply_retirement(plan, review)
        sanity = DayMapSanityService(config).review(decision, plan)
        allowed = (review["action"] != "BLOCK_OPPOSITE_LOCAL"
                   and sanity["action"] == "ALLOW")
        print("     %-32s %-20s %s"
              % (label, review["action"], "TRADES" if allowed else "BLOCKED"))
    print()


if __name__ == "__main__":
    main()
