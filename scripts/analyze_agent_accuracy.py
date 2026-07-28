#!/usr/bin/env python3
"""Measure each agent's standalone accuracy from closed trades.

Before letting any single agent open trades on its own, its solo record has to
earn that trust. This reads the agent_details stored alongside every trade and
asks, per agent: when it held a qualified opinion, how often did the trade
resolve its way, and what would following it alone have earned?

Read-only. Nothing is written back to the database and no signals are sent.

Usage:
    python scripts/analyze_agent_accuracy.py [--limit 500] [--min-confidence 68]
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService  # noqa: E402
from utils.helpers import load_config  # noqa: E402

# A record this thin cannot justify changing how orders are admitted.
MIN_SAMPLE_FOR_SOLO = 20
SOLO_ACCURACY_BAR = 70.0


def _snapshot(trade: Dict[str, Any]) -> Dict[str, Any]:
    snap = trade.get("signal_snapshot") or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except (ValueError, TypeError):
            return {}
    return snap if isinstance(snap, dict) else {}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def collect(trades: List[Dict[str, Any]], min_confidence: float) -> Dict[str, Any]:
    stats: Dict[str, Dict[str, float]] = {}
    closed = 0
    without_details = 0
    for trade in trades:
        status = str(trade.get("status") or "").upper()
        if status in {"PENDING", "OPEN", "PARTIAL", "TP1_HIT", "CANCELLED", "EXPIRED", ""}:
            continue
        side = str(trade.get("type") or trade.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            continue
        closed += 1
        pnl = _f(trade.get("final_pnl") or trade.get("final_pnl_points"))
        details = _snapshot(trade).get("agent_details") or {}
        if not details:
            without_details += 1
            continue
        for name, detail in details.items():
            if not isinstance(detail, dict):
                continue
            direction = str(detail.get("direction") or detail.get("signal") or "").upper()
            confidence = _f(detail.get("confidence"))
            if direction not in {"BUY", "SELL"} or confidence < min_confidence:
                continue
            entry = stats.setdefault(name, {"n": 0, "right": 0, "pnl": 0.0})
            entry["n"] += 1
            agreed = direction == side
            if agreed == (pnl > 0):
                entry["right"] += 1
            entry["pnl"] += pnl if agreed else -pnl
    return {"stats": stats, "closed": closed, "without_details": without_details}


def report(result: Dict[str, Any], min_confidence: float) -> None:
    stats = result["stats"]
    print(f"Closed trades examined        : {result['closed']}")
    if result["without_details"]:
        print(f"  without agent_details       : {result['without_details']}")
    print(f"Counting opinions at or above : {min_confidence:.0f}%")
    print()

    if not stats:
        print("No agent opinions were recorded on these trades, so solo accuracy")
        print("cannot be measured. agent_details is stored with trades created by")
        print("the current pipeline; older rows may predate it.")
        return

    print(f"{'agent':<18}{'calls':>7}{'accuracy':>10}{'net pts':>11}")
    for name, entry in sorted(stats.items(), key=lambda kv: -kv[1]["pnl"]):
        accuracy = entry["right"] / entry["n"] * 100 if entry["n"] else 0.0
        print(f"  {name:<16}{int(entry['n']):>7}{accuracy:>9.0f}%{entry['pnl']:>11.0f}")

    print()
    print("accuracy = how often the trade resolved the way the agent argued.")
    print("net pts  = what following that agent alone would have returned.")
    print()

    best_name, best = max(stats.items(), key=lambda kv: kv[1]["pnl"])
    accuracy = best["right"] / best["n"] * 100 if best["n"] else 0.0
    print("VERDICT")
    if best["n"] < MIN_SAMPLE_FOR_SOLO:
        print(f"  Not enough evidence. The strongest agent ({best_name}) has only")
        print(f"  {int(best['n'])} qualified calls; {MIN_SAMPLE_FOR_SOLO} are needed before letting any")
        print("  agent admit trades on its own. Percentages move by tens of points")
        print("  per trade at this size.")
    elif accuracy >= SOLO_ACCURACY_BAR and best["pnl"] > 0:
        print(f"  {best_name}: {accuracy:.0f}% accuracy across {int(best['n'])} calls, "
              f"{best['pnl']:+.0f} pts net — the strongest record here.")
        print()
        print("  Read this as a ranking, not as a licence to trade alone. Every")
        print("  trade in this sample was chosen by group consensus, so each agent")
        print("  is being scored on setups it was never asked to pick. An agent")
        print("  trading solo would take a different, larger and unfiltered set,")
        print("  and its accuracy there is unmeasured.")
        print()
        print("  What the sample does support: weighting this agent more heavily")
        print("  inside the existing consensus, and questioning any agent that")
        print("  ranks far below it while holding structural authority.")
    else:
        print(f"  {best_name} leads but at {accuracy:.0f}% accuracy and {best['pnl']:+.0f} pts.")
        print(f"  No agent clears {SOLO_ACCURACY_BAR:.0f}% with a positive return on a")
        print("  sufficient sample, so solo entry is not justified by this data.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=500, help="how many recent trades to pull")
    parser.add_argument("--min-confidence", type=float, default=68.0, help="ignore opinions below this")
    parser.add_argument("--out", default="", help="also write the report to this file")
    args = parser.parse_args()

    database = DatabaseService(load_config())
    if not getattr(database, "use_supabase", False):
        print("⚠️  Supabase is not configured; reading the local fallback file.")
        print("    Set SUPABASE_URL and SUPABASE_KEY to analyse the real history.\n")

    trades = database.get_recent_trades(limit=args.limit)
    print(f"Pulled {len(trades)} trades from "
          f"{'Supabase' if getattr(database, 'use_supabase', False) else 'local storage'}\n")
    if not trades:
        print("No trades returned. Nothing to analyse.")
        return 1

    result = collect(trades, args.min_confidence)
    report(result, args.min_confidence)

    if args.out:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            print(f"Pulled {len(trades)} trades")
            report(result, args.min_confidence)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(buffer.getvalue(), encoding="utf-8")
        print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
