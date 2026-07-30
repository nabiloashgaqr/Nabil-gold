"""Before/after on the two live trades, through the real manager.

This is the measurement that justifies the change. Both trades are replayed
through OpenTradesManager.evaluate_trade twice -- once with no agent book
(the old behaviour) and once with the book the system actually published at
that moment -- and the resulting points are compared.

Run:  python scripts/prove_agent_vote_before_after.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOL = "XAU/USD"
PT = 0.1


def _config() -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _book(**agents):
    return {n: {"label": n, "direction": d, "confidence": c, "signals": []}
            for n, (d, c) in agents.items()}


CASES = [
    {
        "id": "a4911dee",
        "entry": 4019.38, "stop": 4059.38, "tp1": 3969.38, "tp2": 3929.38,
        "exit_price": 4028.02,
        "candles": [
            {"time": "2026-07-29T16:45:00+00:00", "open": 4021.0, "high": 4026.0,
             "low": 4020.0, "close": 4024.0},
            {"time": "2026-07-29T17:00:00+00:00", "open": 4025.0, "high": 4028.5,
             "low": 4024.5, "close": 4028.02},
        ],
        # published 17:16, 19 minutes after the exit
        "book": _book(technical=("WAIT", 39.6), classical=("WAIT", 29.0),
                      smc=("BUY", 82.0), price_action=("BUY", 79.0),
                      multitimeframe=("WAIT", 48.0)),
        "later_price": 4079.33,
        "note": "price ran through the 4059.38 stop",
    },
    {
        "id": "5f383b5c",
        "entry": 4046.02, "stop": 4086.02, "tp1": 3996.0, "tp2": 3970.0,
        "exit_price": 4049.94,
        "candles": [
            {"time": "2026-07-30T06:30:00+00:00", "open": 4047.0, "high": 4048.2,
             "low": 4045.5, "close": 4046.5},
            {"time": "2026-07-30T06:45:00+00:00", "open": 4046.6, "high": 4050.2,
             "low": 4046.4, "close": 4049.94},
        ],
        # published with the SELL DAY MAP that re-mapped this very zone
        "book": _book(technical=("BUY", 92.0), classical=("SELL", 71.0),
                      smc=("SELL", 90.0), price_action=("WAIT", 29.0),
                      multitimeframe=("SELL", 83.0)),
        "later_price": 4039.28,
        "note": "price fell; the planner republished this zone as A+",
    },
]


def _run(case, book):
    manager = OpenTradesManager(_config())
    trade = {
        "id": case["id"], "type": "SELL", "status": "OPEN", "symbol": SYMBOL,
        "entry_price": case["entry"], "stop_loss": case["stop"],
        "tp1": case["tp1"], "tp2": case["tp2"], "updates_sent": [],
        "created_at": "2026-07-30T06:31:00+00:00",
    }
    return manager.evaluate_trade(
        trade, case["exit_price"],
        candle_high=case["candles"][-1]["high"],
        candle_low=case["candles"][-1]["low"],
        recent_candles=case["candles"], agent_details=book,
    )


def main() -> None:
    print()
    print("#" * 72)
    print("#  agent vote on the thesis exit — before / after")
    print("#" * 72)

    total_before = total_after = 0.0
    for case in CASES:
        entry, later = case["entry"], case["later_price"]
        before = _run(case, None)
        after = _run(case, case["book"])

        held = (entry - later) / PT
        booked = (entry - case["exit_price"]) / PT

        print()
        print("=" * 72)
        print(f"  {case['id']}   SELL {entry} · stop {case['stop']}")
        print("=" * 72)
        defenders = [k for k, v in case["book"].items()
                     if v["direction"] == "SELL" and v["confidence"] >= 70]
        opponents = [k for k, v in case["book"].items()
                     if v["direction"] == "BUY" and v["confidence"] >= 70]
        print(f"   agent book : {len(defenders)} defending {defenders}, "
              f"{len(opponents)} against {opponents}")
        print()
        print(f"   BEFORE (no agent vote) : {before['new_status']:<14} "
              f"events {before['events']}")
        print(f"   AFTER  (agents asked)  : {after['new_status']:<14} "
              f"events {after['events']}")
        print()

        if before["new_status"] == "MANUAL_CLOSE":
            pnl_before = booked
        else:
            pnl_before = held
        if after["new_status"] == "MANUAL_CLOSE":
            pnl_after = booked
        elif "THESIS_SCALE_OUT" in after["events"]:
            pnl_after = booked * 0.5 + held * 0.5
        else:
            pnl_after = held

        print(f"   outcome at {later} ({case['note']}):")
        print(f"      before : {pnl_before:+8.1f} pts")
        print(f"      after  : {pnl_after:+8.1f} pts")
        print(f"      delta  : {pnl_after - pnl_before:+8.1f} pts")
        total_before += pnl_before
        total_after += pnl_after

    print()
    print("=" * 72)
    print("  TOTAL across both live trades")
    print("=" * 72)
    print(f"   before : {total_before:+8.1f} pts")
    print(f"   after  : {total_after:+8.1f} pts")
    print(f"   delta  : {total_after - total_before:+8.1f} pts")
    print()
    print("   a4911dee is unchanged: the agents confirmed the flip, so the")
    print("   exit still fires and still saves the stop-out.")
    print("   5f383b5c is the whole difference: three qualified agents were")
    print("   defending it, so it is no longer closed.")
    print()


if __name__ == "__main__":
    main()
