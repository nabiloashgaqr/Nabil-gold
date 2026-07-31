#!/usr/bin/env python3
"""Measure whether targets come from the map or from arithmetic.

THE QUESTION
------------
2026-07-31, TRADE_20260731_141102_627592_b4f85832:

    Stop Loss: 4071.77      (400.0 pts from entry)
    TP1:       3981.77      (500.0 pts)
    TP2:       3941.77      (900.0 pts)
    Target liquidity: 4021.07   (107 pts -- appears nowhere in the order)

Those three distances are not a coincidence. `risk_management_agent` widens
the stop to `risk_settings.min_sl_distance_points` (a flat 400 on XAU) and
then rebuilds both targets from the SAME stop:

    tp1 = entry -/+ floor x (atr_multiplier_tp1 / atr_multiplier_sl)   = 400 x 1.25
    tp2 = entry -/+ floor x (atr_multiplier_tp2 / atr_multiplier_sl)   = 400 x 2.25

Reproduced exactly: 4071.77 / 3981.77 / 3941.77, and the card's "1.25R / 2.25R".

So on any floored plan the geometry is a constant -400/+500/+900 regardless of
what the analysis found, and the mapped objective is discarded. config.json
warns about precisely this signature in
`session_planner.description_zone_width_vs_sl_floor`.

Two consequences worth measuring rather than assuming:

  1. Uniformity. If every floored plan carries identical geometry, the
     scoreboard cannot learn which setups deserve wider targets -- every trade
     reports the same shape.
  2. Truncation. A target derived from the stop can land short of the level
     the analysis says price is drawn to, or far beyond it. Both are visible
     in closed trades: compare where the trade actually travelled (MFE) with
     where TP2 was placed and where the mapped objective sat.

WHAT THIS SCRIPT DOES
---------------------
Reads closed trades and reports, per trade and in aggregate:

  * whether the shipped geometry matches the floored-arithmetic signature
  * the mapped objective, if the snapshot preserved one, and its R multiple
  * how far the trade actually ran (MFE) versus TP2
  * how often a trade cleared TP2 and kept going -- points left on the table
  * how often TP2 was never approached -- targets set beyond reach

It writes nothing and changes nothing. It answers the question the -400/
+500/+900 signature raises, so the decision to change any risk number is made
from your own history instead of from a plausible argument.

Usage:
    python scripts/analyze_target_geometry.py [--limit 500] [--symbol XAU/USD]
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

# A verdict drawn from a handful of trades is a coin toss wearing a suit.
MIN_SAMPLE_FOR_VERDICT = 20


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


def _mapped_objective(trade: Dict[str, Any]) -> float:
    """The liquidity level the analysis said price was drawn to."""
    snap = _snapshot(trade)
    plan = snap.get("session_plan") or {}
    primary = plan.get("primary_poi") or {}
    setup = snap.get("setup_context") or {}
    for source in (
        primary.get("target_price"),
        primary.get("target_liquidity"),
        setup.get("target_liquidity"),
        setup.get("target_price"),
    ):
        value = _f(source)
        if value > 0:
            return value
    return 0.0


def _was_filled(trade: Dict[str, Any]) -> bool:
    """True only when the order actually became a position.

    Report #11 counted 106 "trades" and concluded that 92.5% never reached
    half of TP2, with an average reach of 11%. Both numbers were wrong, and
    they were wrong because this distinction was missing.

    A CANCELLED or EXPIRED pending order never entered the market, so its
    excursion is zero by definition -- not because the target was too far.
    Averaging those zeros against real positions produced a figure that
    describes bookkeeping, not price. The arithmetic is plain: 98 zeros plus
    8 positions averaging ~145% gives the 11% that was printed.

    Fill is proved by evidence of a life: a fill timestamp, a realized
    result, or a recorded excursion -- not by status alone, since older rows
    predate some of these fields.
    """
    status = str(trade.get("status") or "").upper()
    if status in {"CANCELLED", "EXPIRED", "REJECTED"}:
        # An expired PENDING never filled; an expired position did. Only the
        # latter carries a fill time or a realized number.
        return bool(
            trade.get("entry_time")
            or trade.get("close_price")
            or trade.get("final_pnl") is not None
        )
    if trade.get("entry_time") or trade.get("close_price"):
        return True
    return trade.get("final_pnl") is not None


def _mfe_points(trade: Dict[str, Any]) -> float | None:
    """Best excursion in the trade's favour, in points.

    Returns None for an order that never filled, so it is excluded from the
    reach statistics rather than counted as a target that was never
    approached.
    """
    if not _was_filled(trade):
        return None
    raw = trade.get("max_favorable_excursion")
    if raw is None:
        return None
    return max(0.0, _f(raw, 0.0))


def _matches_floored_signature(
    entry: float, stop: float, tp1: float, tp2: float,
    symbol: str, config: Dict[str, Any],
) -> bool:
    """True when TP1/TP2 look derived from the stop rather than the map.

    Uses the same ratios the risk agent applies, with a tolerance of one
    point to absorb rounding.
    """
    risk_cfg = config.get("risk_settings") or {}
    sl_mult = _f(risk_cfg.get("atr_multiplier_sl"), 2.0) or 2.0
    tp1_ratio = _f(risk_cfg.get("atr_multiplier_tp1"), 2.5) / sl_mult
    tp2_ratio = _f(risk_cfg.get("atr_multiplier_tp2"), 4.5) / sl_mult

    risk_points = abs(price_to_points(entry - stop, symbol=symbol))
    if risk_points <= 0:
        return False
    tp1_points = abs(price_to_points(tp1 - entry, symbol=symbol)) if tp1 > 0 else 0.0
    tp2_points = abs(price_to_points(tp2 - entry, symbol=symbol)) if tp2 > 0 else 0.0

    return (
        abs(tp1_points - risk_points * tp1_ratio) <= 1.0
        and abs(tp2_points - risk_points * tp2_ratio) <= 1.0
    )


def _pct(part: int, whole: int) -> str:
    return f"{(part / whole * 100.0):5.1f}%" if whole else "  n/a"


def analyse(trades: List[Dict[str, Any]], config: Dict[str, Any], symbol: str) -> int:
    risk_cfg = config.get("risk_settings") or {}
    floor_points = _f(risk_cfg.get("min_sl_distance_points"), 0.0)

    considered = 0
    floored_signature = 0
    risk_distances: Dict[float, int] = {}
    tp2_distances: Dict[float, int] = {}
    objective_known = 0
    objective_shorter_than_tp2 = 0
    objective_rr: List[float] = []
    ran_past_tp2 = 0
    left_on_table: List[float] = []
    never_neared_tp2 = 0
    mfe_vs_tp2: List[float] = []

    for trade in trades:
        entry = _f(trade.get("entry_price"))
        stop = _f(trade.get("initial_stop_loss")) or _f(trade.get("stop_loss"))
        tp1 = _f(trade.get("tp1"))
        tp2 = _f(trade.get("tp2"))
        if entry <= 0 or stop <= 0 or tp2 <= 0:
            continue
        considered += 1

        risk_points = round(abs(price_to_points(entry - stop, symbol=symbol)), 0)
        tp2_points = round(abs(price_to_points(tp2 - entry, symbol=symbol)), 0)
        risk_distances[risk_points] = risk_distances.get(risk_points, 0) + 1
        tp2_distances[tp2_points] = tp2_distances.get(tp2_points, 0) + 1

        if _matches_floored_signature(entry, stop, tp1, tp2, symbol, config):
            floored_signature += 1

        objective = _mapped_objective(trade)
        if objective > 0 and risk_points > 0:
            objective_known += 1
            obj_points = abs(price_to_points(objective - entry, symbol=symbol))
            objective_rr.append(obj_points / risk_points)
            if obj_points < tp2_points:
                objective_shorter_than_tp2 += 1

        mfe = _mfe_points(trade)
        if mfe is not None and tp2_points > 0:
            mfe_vs_tp2.append(mfe / tp2_points)
            if mfe > tp2_points:
                ran_past_tp2 += 1
                left_on_table.append(mfe - tp2_points)
            if mfe < tp2_points * 0.5:
                never_neared_tp2 += 1

    if not considered:
        print("No closed trades carried the fields needed for this measurement.")
        return 0

    print()
    print("=" * 68)
    print(f"  TARGET GEOMETRY — {considered} trades on {symbol}")
    print("=" * 68)

    print()
    print(f"  Configured stop floor          : {floor_points:.0f} pts")
    print(f"  Targets derived from the stop  : {floored_signature:4d}  {_pct(floored_signature, considered)}")
    print("     (TP1/TP2 match floor x 1.25 / floor x 2.25 to within 1 pt)")

    # Rows written under a previous floor are a different population.
    #
    # Report #11 showed two dominant shapes: stop 400 -> TP2 900 (46 rows) and
    # stop 300 -> TP2 700 (37 rows). config.json records that the floor was
    # "رُفع من 300 إلى 400", so the second group predates the change. Averaging
    # the two eras gave 43.4% -- against 69% among rows written under today's
    # floor. The same mistake as the rejection report: mixing populations
    # hides the very signal being measured.
    current_era = [
        d for d in (risk_distances or {})
        if floor_points > 0 and abs(d - floor_points) <= 1.0
    ]
    current_count = sum(risk_distances[d] for d in current_era)
    older = considered - current_count
    if older > 0 and floor_points > 0:
        print()
        print(f"  Rows written under the current {floor_points:.0f}-pt floor : {current_count:4d}")
        print(f"  Rows from an earlier floor                : {older:4d}")
        if current_count:
            print(f"     stop-derived share, current era only   : "
                  f"{_pct(floored_signature, current_count)}")
            print("     (the headline % above averages both eras together)")

    print()
    print("  Distinct stop distances actually shipped:")
    for distance, count in sorted(risk_distances.items(), key=lambda kv: -kv[1])[:6]:
        print(f"     {distance:6.0f} pts : {count:4d}  {_pct(count, considered)}")
    if len(risk_distances) <= 2:
        print("     -> risk is effectively a constant; structure is not setting it")

    print()
    print("  Distinct TP2 distances actually shipped:")
    for distance, count in sorted(tp2_distances.items(), key=lambda kv: -kv[1])[:6]:
        print(f"     {distance:6.0f} pts : {count:4d}  {_pct(count, considered)}")

    if objective_known:
        avg_rr = sum(objective_rr) / len(objective_rr)
        below_min = sum(1 for rr in objective_rr if rr < _f(risk_cfg.get("min_rr_ratio"), 1.5))
        print()
        print(f"  Mapped objective recorded      : {objective_known:4d}  {_pct(objective_known, considered)}")
        print(f"     average distance             : {avg_rr:.2f}R")
        print(f"     below min_rr_ratio           : {below_min:4d}  {_pct(below_min, objective_known)}")
        print(f"     nearer than the shipped TP2  : {objective_shorter_than_tp2:4d}  {_pct(objective_shorter_than_tp2, objective_known)}")
        print("     -> an objective the order cannot use is an objective the")
        print("        order ignores; TP2 then comes from the stop instead")

    if mfe_vs_tp2:
        avg_reach = sum(mfe_vs_tp2) / len(mfe_vs_tp2)
        print()
        print(f"  Orders that actually filled   : {len(mfe_vs_tp2):4d} of {considered}")
        print("     (cancelled and expired pending orders never entered the")
        print("      market, so their zero excursion is excluded)")
        print(f"  How far filled trades ran vs TP2 (avg)   : {avg_reach * 100:.0f}% of the way")
        print(f"  Ran past TP2 and kept going   : {ran_past_tp2:4d}  {_pct(ran_past_tp2, len(mfe_vs_tp2))}")
        if left_on_table:
            avg_left = sum(left_on_table) / len(left_on_table)
            print(f"     average points left behind   : {avg_left:6.0f} pts")
            print(f"     total across those trades    : {sum(left_on_table):6.0f} pts")
        print(f"  Never reached half of TP2     : {never_neared_tp2:4d}  {_pct(never_neared_tp2, len(mfe_vs_tp2))}")

    print()
    print("-" * 68)
    if considered < MIN_SAMPLE_FOR_VERDICT:
        print(f"  NO VERDICT — {considered} trades is too few (need {MIN_SAMPLE_FOR_VERDICT}).")
        print("  The numbers above are printed so the sample can grow, not")
        print("  so a decision can be taken from them today.")
        print("-" * 68)
        print()
        return 0

    share = floored_signature / considered
    print("  READING")
    if share >= 0.8:
        print(f"  {share * 100:.0f}% of orders carry stop-derived targets. The map's own")
        print("  objective is not what the order is aiming at. Every plan of the")
        print("  same grade then reports the same geometry, so outcome data")
        print("  cannot distinguish a good target from a bad one.")
    elif share >= 0.3:
        print(f"  {share * 100:.0f}% of orders carry stop-derived targets -- common but not")
        print("  universal. Worth comparing the two groups' outcomes directly.")
    else:
        print(f"  Only {share * 100:.0f}% of orders carry stop-derived targets; targets are")
        print("  mostly coming from the map, as intended.")

    if ran_past_tp2 and left_on_table:
        print()
        print(f"  {ran_past_tp2} trades ran past TP2, leaving {sum(left_on_table):.0f} pts behind in total.")
        print("  That is the cost of a target set by arithmetic rather than by")
        print("  the level price was actually heading for.")
    print("-" * 68)
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--limit", type=int, default=500, help="how many recent trades to pull")
    parser.add_argument("--symbol", type=str, default=None, help="restrict to one symbol")
    args = parser.parse_args()

    config = load_config()
    symbol = args.symbol or str(config.get("symbol", "XAU/USD"))
    database = DatabaseService(config)

    try:
        trades = database.get_recent_trades(limit=args.limit)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read trades: {exc}")
        return 1

    closed = [
        t for t in (trades or [])
        if str(t.get("status") or "").upper() not in {"PENDING", "OPEN", "PARTIAL", "TP1_HIT"}
        and (not args.symbol or str(t.get("symbol") or "") == args.symbol)
    ]
    if not closed:
        print("No closed trades found. Nothing to measure yet.")
        return 0

    return analyse(closed, config, symbol)


if __name__ == "__main__":
    raise SystemExit(main())
