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


# A verdict drawn from a handful of trades is a coin toss wearing a suit.
MIN_SAMPLE_FOR_VERDICT = 20


def analyse(trades: List[Dict[str, Any]], symbol: str) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    # Every exclusion is counted. Silently dropping rows is how an analysis of
    # 4 trades gets mistaken for an analysis of 86.
    skipped: Dict[str, int] = {
        "other_symbol": 0,
        "not_closed": 0,
        "no_structural_stop": 0,
        "missing_prices": 0,
        "no_side": 0,
    }
    statuses: Dict[str, int] = {}

    for trade in trades:
        if symbol and str(trade.get("symbol") or "") != symbol:
            skipped["other_symbol"] += 1
            continue
        status = str(trade.get("status") or trade.get("result") or "").upper()
        statuses[status or "(blank)"] = statuses.get(status or "(blank)", 0) + 1
        if status in {"PENDING", "OPEN", "CANCELLED", "EXPIRED", ""}:
            skipped["not_closed"] += 1
            continue

        entry = _f(trade.get("entry_price"))
        shipped_stop = _f(trade.get("initial_stop_loss") or trade.get("stop_loss"))
        structural_stop = _structural_stop(trade)
        if entry <= 0 or shipped_stop <= 0:
            skipped["missing_prices"] += 1
            continue
        if structural_stop <= 0:
            # The planner did not record a session_plan on this trade, so the
            # pre-floor stop is unknown. Common for non-planner entry paths.
            skipped["no_structural_stop"] += 1
            continue

        side = str(trade.get("type") or trade.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            skipped["no_side"] += 1
            continue

        shipped_risk = abs(price_to_points(entry - shipped_stop, symbol=symbol))
        structural_risk = abs(price_to_points(entry - structural_stop, symbol=symbol))
        if structural_risk <= 0:
            skipped["missing_prices"] += 1
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
    return {"rows": rows, "skipped": skipped, "statuses": statuses, "total": len(trades)}


def _path_stats(trades: List[Dict[str, Any]], symbol: str, planner: bool) -> Dict[str, Any]:
    wins = losses = be = 0
    gross_win = gross_loss = 0.0
    for trade in trades:
        if symbol and str(trade.get("symbol") or "") != symbol:
            continue
        status = str(trade.get("status") or "").upper()
        if status in {"PENDING", "OPEN", "PARTIAL", "TP1_HIT", "CANCELLED", "EXPIRED"}:
            continue
        if (_structural_stop(trade) > 0) != planner:
            continue
        pnl = _f(trade.get("final_pnl") or trade.get("final_pnl_points"))
        if pnl > 0:
            wins += 1
            gross_win += pnl
        elif pnl < 0:
            losses += 1
            gross_loss += abs(pnl)
        else:
            be += 1
    total = wins + losses + be
    return {
        "total": total, "wins": wins, "losses": losses, "be": be,
        "gross_win": gross_win, "gross_loss": gross_loss,
        "net": gross_win - gross_loss,
        "win_rate": (wins / total * 100) if total else 0.0,
        "pf": (gross_win / gross_loss) if gross_loss else float("inf") if gross_win else 0.0,
    }


def _print_path_split(trades: List[Dict[str, Any]], symbol: str) -> None:
    """Compare the planner path against everything else.

    Deciding whether to route more volume through the planner requires knowing
    whether the planner is actually better. With a small planner sample this is
    indicative only, and is labelled as such rather than presented as proof.
    """
    planner = _path_stats(trades, symbol, planner=True)
    other = _path_stats(trades, symbol, planner=False)
    if not planner["total"] and not other["total"]:
        return
    print(f"{'':<16}{'trades':>8}{'win rate':>10}{'net pts':>10}{'PF':>8}")
    for label, st in (("planner path", planner), ("other paths", other)):
        pf = "inf" if st["pf"] == float("inf") else f"{st['pf']:.2f}"
        print(f"{label:<16}{st['total']:>8}{st['win_rate']:>9.0f}%{st['net']:>10.0f}{pf:>8}")
    if 0 < planner["total"] < 20:
        print(f"  (planner sample is {planner['total']} trades — indicative, not conclusive)")
    print()


def _print_outcomes(trades: List[Dict[str, Any]], symbol: str) -> None:
    """Summarise how trades actually ended, across every entry path.

    The floor question only touches planner trades, but the status mix showed
    something that matters more: which paths are trading at all, and how they
    resolve. SL_HIT is also used for trailing exits, so status alone cannot
    separate a full loss from a protected win -- result and pnl are needed.
    """
    closed, planner, wins, losses, breakeven = 0, 0, 0, 0, 0
    gross_win, gross_loss = 0.0, 0.0
    never_filled = 0
    for trade in trades:
        if symbol and str(trade.get("symbol") or "") != symbol:
            continue
        status = str(trade.get("status") or "").upper()
        if status in {"PENDING", "OPEN", "PARTIAL", "TP1_HIT"}:
            continue
        if status in {"CANCELLED", "EXPIRED"}:
            never_filled += 1
            continue
        closed += 1
        if _structural_stop(trade) > 0:
            planner += 1
        pnl = _f(trade.get("final_pnl") or trade.get("final_pnl_points"))
        if pnl > 0:
            wins += 1
            gross_win += pnl
        elif pnl < 0:
            losses += 1
            gross_loss += abs(pnl)
        else:
            breakeven += 1

    if not closed:
        return
    print("─── Outcomes across all entry paths ───")
    _print_path_split(trades, symbol)
    print(f"Filled and closed             : {closed}")
    print(f"  from the planner path       : {planner} ({planner/closed*100:.0f}%)")
    print(f"  from other paths            : {closed - planner} ({(closed-planner)/closed*100:.0f}%)")
    if never_filled:
        print(f"Never filled (cancelled/expired): {never_filled}")
    print(f"Wins {wins} · Losses {losses} · Breakeven {breakeven}"
          f"   (win rate {wins/closed*100:.0f}%)")
    net = gross_win - gross_loss
    print(f"Gross +{gross_win:.0f} pts / -{gross_loss:.0f} pts   net {net:+.0f} pts")
    if gross_loss > 0:
        print(f"Profit factor                 : {gross_win/gross_loss:.2f}")
    print()


def _print_coverage(result: Dict[str, Any]) -> None:
    """Show what was excluded, so the sample can be judged before the verdict."""
    total = result.get("total", 0)
    rows = result["rows"]
    skipped = result.get("skipped", {})
    print(f"Trades pulled                 : {total}")
    print(f"Analysable (closed, with plan): {len(rows)}"
          + (f"  ({len(rows)/total*100:.0f}%)" if total else ""))
    labels = {
        "other_symbol": "different symbol",
        "not_closed": "still open / pending / cancelled",
        "no_structural_stop": "no planner stop recorded (non-planner entry)",
        "missing_prices": "missing entry or stop price",
        "no_side": "no trade direction",
    }
    excluded = {k: v for k, v in skipped.items() if v}
    if excluded:
        print("Excluded:")
        for key, count in sorted(excluded.items(), key=lambda kv: -kv[1]):
            print(f"    {count:>4}  {labels.get(key, key)}")
    statuses = result.get("statuses") or {}
    if statuses:
        top = sorted(statuses.items(), key=lambda kv: -kv[1])[:6]
        print("Status mix: " + ", ".join(f"{k}={v}" for k, v in top))
    print()


def report(result: Dict[str, Any]) -> None:
    rows = result["rows"]
    _print_coverage(result)
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
    if len(judged) < MIN_SAMPLE_FOR_VERDICT:
        print(f"  NOT ENOUGH DATA — {len(judged)} judged trade(s), need {MIN_SAMPLE_FOR_VERDICT}.")
        print("  Percentages on a sample this small are noise: one trade moves")
        print("  them by tens of points. What the sample does establish is the")
        print(f"  inflation itself ({mid:.1f}x median), which is a property of the")
        print("  configuration, not of the outcomes.")
        print()
        print("  To widen it, check the excluded counts above: if most trades")
        print("  lack a planner stop, the floor question only applies to the")
        print("  planner path and the rest were never affected by it.")
        return
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
    _print_outcomes(trades, args.symbol)
    report(result)

    if args.out:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            print(f"Pulled {len(trades)} trades")
            _print_outcomes(trades, args.symbol)
            report(result)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(buffer.getvalue(), encoding="utf-8")
        print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
