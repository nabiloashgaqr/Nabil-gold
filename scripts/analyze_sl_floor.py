#!/usr/bin/env python3
"""Decide the SL-floor question from trades already recorded.

The risk floor (`risk_settings.min_sl_distance_points`) widens every planner
stop to a fixed distance. On XAU that is 400 points ($40), while real POI
zones are a few dollars wide, so the floor engages on essentially every plan
and it -- not structure -- sets the risk on each trade.

Changing that number blind is a guess. But the answer is already in the
history: `signal_snapshot.session_plan.primary_poi.stop_loss` preserves the
structural stop the planner derived *before* the floor was applied, alongside
the stop that actually shipped and the outcome that followed. So for every
closed trade we can ask the only question that matters:

    would the structural stop have been hit before the trade resolved?

A structural stop that survives is pure waste: the extra distance was risked
and never used. A structural stop that would have been breached is the floor
earning its keep -- it kept a winner alive through noise.

Usage:
    python scripts/analyze_sl_floor.py [--limit 500] [--symbol XAU/USD]

Reads the same database the bot writes to; makes no changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService  # noqa: E402
from utils.helpers import load_config  # noqa: E402
from utils.instruments import price_to_points  # noqa: E402


def _f(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default  # reject NaN


def _snapshot(trade: Dict[str, Any]) -> Dict[str, Any]:
    snap = trade.get("signal_snapshot") or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except (ValueError, TypeError):
            return {}
    return snap if isinstance(snap, dict) else {}


def _structural_stop(trade: Dict[str, Any]) -> float:
    """The stop the planner derived from structure, before the floor."""
    snap = _snapshot(trade)
    plan = snap.get("session_plan") or {}
    for source in (
        (plan.get("primary_poi") or {}).get("stop_loss"),
        (snap.get("setup_context") or {}).get("stop_loss"),
    ):
        value = _f(source)
        if value > 0:
            return value
    return 0.0


def _adverse_points(trade: Dict[str, Any]) -> float | None:
    """How far price ran against the position, in points.

    OpenTradesManager records max_adverse_excursion as signed points from
    entry (negative when the trade went against us), refreshed every cycle
    from candle highs and lows, so it already captures the wick that a tighter
    stop would have had to survive.
    """
    raw = trade.get("max_adverse_excursion")
    if raw is None:
        return None
    value = _f(raw, 0.0)
    return abs(value) if value <= 0 else 0.0


def analyse(trades: List[Dict[str, Any]], symbol: str) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for trade in trades:
        if symbol and str(trade.get("symbol") or "") != symbol:
            continue
        status = str(trade.get("status") or trade.get("result") or "").upper()
        if status in {"PENDING", "OPEN", "CANCELLED", "EXPIRED", ""}:
            continue

        entry = _f(trade.get("entry_price"))
        shipped_stop = _f(trade.get("initial_stop_loss") or trade.get("stop_loss"))
        structural_stop = _structural_stop(trade)
        if entry <= 0 or shipped_stop <= 0 or structural_stop <= 0:
            continue

        side = str(trade.get("type") or trade.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            continue

        shipped_risk = abs(price_to_points(entry - shipped_stop, symbol=symbol))
        structural_risk = abs(price_to_points(entry - structural_stop, symbol=symbol))
        if structural_risk <= 0:
            continue

        pnl = _f(trade.get("final_pnl") or trade.get("final_pnl_points") or trade.get("current_pnl_points"))
        adverse = _adverse_points(trade)
        # Would price have traded through the tighter structural stop?
        structural_hit = None if adverse is None else adverse >= round(structural_risk, 1)

        rows.append({
            "id": trade.get("id"),
            "side": side,
            "status": status,
            "entry": entry,
            "shipped_stop": shipped_stop,
            "structural_stop": structural_stop,
            "shipped_risk": round(shipped_risk, 1),
            "structural_risk": round(structural_risk, 1),
            "inflation": shipped_risk / structural_risk if structural_risk else 0.0,
            "floored": shipped_risk > structural_risk + 1.0,
            "pnl": pnl,
            "won": pnl > 0,
            "structural_hit": structural_hit,
        })
    return {"rows": rows}


def report(result: Dict[str, Any]) -> None:
    rows = result["rows"]
    if not rows:
        print("No closed trades carried both a structural and a shipped stop.")
        print("The planner must have written session_plan into signal_snapshot")
        print("for a trade to be analysable here.")
        return

    floored = [r for r in rows if r["floored"]]
    print(f"Closed trades analysed        : {len(rows)}")
    print(f"Trades where the floor engaged: {len(floored)} ({len(floored)/len(rows)*100:.0f}%)")
    if not floored:
        print("\nThe floor never engaged, so it is not what sets your risk.")
        return

    inflations = sorted(r["inflation"] for r in floored)
    mid = inflations[len(inflations) // 2]
    print(f"Risk inflation  median {mid:.1f}x  |  min {inflations[0]:.1f}x  |  max {inflations[-1]:.1f}x")
    avg_shipped = sum(r["shipped_risk"] for r in floored) / len(floored)
    avg_struct = sum(r["structural_risk"] for r in floored) / len(floored)
    print(f"Average risk    shipped {avg_shipped:.0f} pts  vs structural {avg_struct:.0f} pts")

    judged = [r for r in floored if r["structural_hit"] is not None]
    print()
    if not judged:
        print("VERDICT: cannot be settled from this data yet.")
        print("  No row carries max_adverse_excursion, so whether the tighter stop")
        print("  would have survived is unknowable. It is recorded on trades managed")
        print("  by the current OpenTradesManager; older rows may predate it.")
        return

    saved = [r for r in judged if r["structural_hit"] and r["won"]]
    wasted = [r for r in judged if not r["structural_hit"]]
    print(f"Judged on recorded excursion  : {len(judged)}")
    print(f"  Floor rescued a winner      : {len(saved)} ({len(saved)/len(judged)*100:.0f}%)")
    print(f"  Structural stop would have held (extra risk unused): "
          f"{len(wasted)} ({len(wasted)/len(judged)*100:.0f}%)")

    print()
    print("VERDICT")
    waste_rate = len(wasted) / len(judged)
    if waste_rate >= 0.8:
        print(f"  The tighter stop would have survived {waste_rate*100:.0f}% of the time.")
        print("  The floor is mostly dead weight: lower it, or make it ATR-relative,")
        print("  so position size reflects structure instead of a constant.")
    elif len(saved) / len(judged) >= 0.25:
        print(f"  The floor rescued {len(saved)/len(judged)*100:.0f}% of judged winners.")
        print("  It is doing real work. Keep it and size positions off it.")
    else:
        print("  Mixed. Neither lowering nor keeping the floor is clearly right;")
        print("  widen the sample before changing a number that moves every trade.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=500, help="how many recent trades to pull")
    parser.add_argument("--symbol", default="XAU/USD", help="restrict to one instrument")
    parser.add_argument("--out", default="", help="also write the report to this file")
    args = parser.parse_args()

    config = load_config()
    database = DatabaseService(config)

    # An empty local fallback would produce a confident "no data" verdict that
    # says nothing about the real history, so be explicit about the source.
    if not getattr(database, "use_supabase", False):
        print("⚠️  Supabase is not configured; reading the local fallback file.")
        print("    Set SUPABASE_URL and SUPABASE_KEY to analyse the real history.\n")

    trades = database.get_recent_trades(limit=args.limit)
    print(f"Pulled {len(trades)} trades from "
          f"{'Supabase' if getattr(database, 'use_supabase', False) else 'local storage'}\n")
    if not trades:
        print("No trades returned. Nothing to analyse.")
        return 1
    result = analyse(trades, args.symbol)
    report(result)

    if args.out:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            print(f"Pulled {len(trades)} trades")
            report(result)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(buffer.getvalue(), encoding="utf-8")
        print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
