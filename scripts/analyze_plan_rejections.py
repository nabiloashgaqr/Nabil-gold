"""Why are day maps being refused, and are the refusals fair?

Two live cycles in a row reported "primary thesis too weak for planning" --
dominance 32.2 then 41.0, against a bar of 50. That single number decides
whether a confirmed sweep ever reaches the archetype layer at all, and there
was no way to see its distribution: every refusal is written to `session_plans`
with its reason, but nobody reads them back.

The question this answers is deliberately narrow. Not "should the bar be
lower" -- that trades quality for volume, which the operator has ruled out --
but "is the score itself measuring fairly?"

  • refusals bunched just under the bar (45-49) suggest the calculation is
    systematically conservative, and the fix belongs in the scoring;
  • refusals spread widely suggest the bar is doing its job and the setups
    genuinely were weak.

Reads only. Prints to the workflow log so the answer needs no database access.

    python scripts/analyze_plan_rejections.py [--limit 300] [--symbol XAU/USD]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections import Counter
from typing import Any, Dict, List

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import DatabaseService
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)

_NUMBERS = re.compile(r"dominance\s+([\d.]+).*?return probability\s+([\d.]+)", re.I)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bucket(value: float) -> str:
    low = int(value // 10) * 10
    return f"{low}-{low + 9}"


def _bar(count: int, total: int, width: int = 32) -> str:
    filled = int(round((count / total) * width)) if total else 0
    return "█" * filled + "·" * (width - filled)


def _is_crash(reason: str) -> bool:
    """A crash is not a refusal, and counting them together hides both."""
    text = (reason or "").lower()
    return (
        "planner crashed" in text
        or "nonetype" in text
        or "object has no attribute" in text
        or "traceback" in text
        or "keyerror" in text
        or "indexerror" in text
    )


def _reason_family(reason: str) -> str:
    text = (reason or "").lower()
    if _is_crash(reason):
        return f"⚠ CRASH — {(reason or '')[:44]}"
    # "execution refused the leg" held a flat 20 across reports #17, #7 and
    # #8 -- three different code states, three different market days, the
    # same number. That looked like a dead gate, but the gate is live: the
    # label was collapsing every distinct execution verdict into one bucket,
    # so whatever actually varied underneath could never be seen.
    #
    # The planner already reports WHY execution refused
    # (session_planner.py:1245, "execution refused the main leg: <cause>").
    # Keep that cause instead of throwing it away.
    if "execution refused" in text:
        _, _, cause = (reason or "").partition(":")
        cause = cause.strip()
        return f"execution refused: {cause[:34]}" if cause else "execution refused the leg"
    for needle, label in (
        ("too weak for planning", "primary thesis too weak"),
        ("archetype conviction", "archetype conviction LOW"),
        ("rr", "reward-to-risk"),
        ("too wide", "zone too wide"),
        ("too narrow", "zone too narrow"),
        ("opposing", "agents opposed"),
        ("supporting", "not enough support"),
        ("session", "session gate"),
    ):
        if needle in text:
            return label
    return (reason or "unknown")[:48]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--symbol", default=None)
    parser.add_argument(
        "--since",
        default=None,
        help="ISO timestamp; only cycles at or after it. Use to isolate the "
             "effect of a change instead of averaging it into a day of history.",
    )
    parser.add_argument(
        "--split-at",
        default=None,
        help="ISO timestamp; report before/after separately. A 300-cycle window "
             "spans ~25 hours, so a fix deployed an hour ago is invisible in the total.",
    )
    args = parser.parse_args()

    config = load_config()
    database = DatabaseService(config)
    rows: List[Dict[str, Any]] = database.get_recent_session_plans(
        limit=args.limit, symbol=args.symbol
    )

    if not rows:
        logger.info("No session plans on record yet.")
        return

    def _stamp(row: Dict[str, Any]) -> str:
        return str(row.get("analysis_run_at") or row.get("created_at") or "")

    if args.since:
        rows = [r for r in rows if _stamp(r) >= args.since]
        if not rows:
            logger.info("No session plans at or after %s.", args.since)
            return

    # A 300-cycle window covers roughly a day. Averaging a change deployed an
    # hour ago into that history hides it completely: the first report after
    # the archetype fix moved "conviction LOW" by 3 rows out of 102, which
    # says nothing about the fix and everything about the window.
    if args.split_at:
        before = [r for r in rows if _stamp(r) < args.split_at]
        after = [r for r in rows if _stamp(r) >= args.split_at]
        for label, subset in (("BEFORE " + args.split_at, before),
                              ("AFTER  " + args.split_at, after)):
            if subset:
                _report(label, subset, config)
            else:
                print(f"\n  ({label}: no cycles)")
        return

    _report(f"last {len(rows)} cycles", rows, config)


def _report(window_label: str, rows: List[Dict[str, Any]], config: Dict[str, Any]) -> None:
    ready = [r for r in rows if r.get("plan_ready")]
    refused = [r for r in rows if not r.get("plan_ready")]

    print("\n" + "=" * 62)
    print(f"PLAN REJECTION ANALYSIS — {window_label}")
    print("=" * 62)

    # An empty window used to divide by zero on the very next line, after the
    # header had already been printed. The operator saw a report that started
    # and then stopped -- no error, no explanation, looking exactly like an
    # analysis that ran and found nothing to say. A diagnostic that fails
    # silently is worse than none.
    if not rows:
        print("  No cycles in this window.")
        print()
        print("  Nothing was read back from the session_plans table for the")
        print("  requested range. Either no analysis cycle has written a plan")
        print("  yet, or every row fell outside --since / --split-at.")
        print()
        print("  Check: does session_plans contain rows, and do they carry an")
        print("  analysis_run_at timestamp inside the window you asked for?")
        print("=" * 62)
        return

    crashes = [r for r in refused if _is_crash(str(r.get("plan_reason") or ""))]
    genuine = [r for r in refused if r not in crashes]

    print(f"  published : {len(ready)}  ({len(ready) / len(rows) * 100:.1f}%)")
    print(f"  refused   : {len(genuine)}")
    if crashes:
        print(f"  CRASHED   : {len(crashes)}  ({len(crashes) / len(rows) * 100:.1f}%)"
              "  ← not refusals: cycles that produced no map at all")

    families = Counter(_reason_family(str(r.get("plan_reason") or "")) for r in refused)
    if families:
        shown = families.most_common(12)
        print("\n  Why refused")
        for label, count in shown:
            print(f"    {count:4d}  {_bar(count, len(refused))}  {label}")
        # The tail used to vanish silently. With only eight rows printed, 69
        # of 270 refusals were invisible, and the reason that matters can sit
        # anywhere in that tail.
        remainder = len(refused) - sum(c for _, c in shown)
        if remainder > 0:
            print(f"    {remainder:4d}  {'·' * 32}  (all other reasons)")

    # Which direction is the system refusing to map?
    #
    # A report of 300 cycles published eleven maps and every one was BUY, on a
    # day gold fell from 4048 to 3996 with a 95% bearish daily bias. Counting
    # only the published side hid that completely: the refusals are where the
    # missing direction lives.
    def _side(row: Dict[str, Any]) -> str:
        payload = row.get("payload")
        if isinstance(payload, str):
            import json as _json
            try:
                payload = _json.loads(payload)
            except (ValueError, TypeError):
                payload = None
        candidate = (
            row.get("session_bias")
            or row.get("authority_direction")
            or (payload or {}).get("session_bias")
            or (payload or {}).get("authority_direction")
        )
        return str(candidate or "none").upper()

    refused_sides = Counter(_side(r) for r in refused)
    if refused_sides:
        print("\n  Direction of refused maps")
        for label, count in refused_sides.most_common():
            print(f"    {count:4d}  {_bar(count, len(refused))}  {label}")
        buy = refused_sides.get("BUY", 0)
        sell = refused_sides.get("SELL", 0)
        if buy + sell > 0 and min(buy, sell) / max(buy, sell, 1) < 0.25:
            heavier = "BUY" if buy > sell else "SELL"
            print(f"    → the planner is barely forming {'SELL' if heavier == 'BUY' else 'BUY'} "
                  f"theses at all, not merely refusing them.")

    # Which gate kills each direction?
    #
    # The counts above showed 25 SELL maps refused against 0 published, while
    # BUY published 11 of 14. That is a pattern, but the reason list is
    # aggregated across every refusal, so there was no way to see whether SELL
    # maps die at a different gate than BUY maps -- and without that, fixing
    # the imbalance means guessing which gate to touch.
    #
    # Directionless refusals are deliberately excluded: they were rejected
    # upstream of the direction decision, so attributing them to a side would
    # invent a pattern that is not in the data.
    directional = [r for r in refused if _side(r) in {"BUY", "SELL"}]
    if directional:
        print("\n  Why each direction is refused")
        for side in ("SELL", "BUY"):
            subset = [r for r in directional if _side(r) == side]
            if not subset:
                continue
            print(f"    {side}  ({len(subset)} refused)")
            per_side = Counter(
                _reason_family(str(r.get("plan_reason") or "")) for r in subset
            )
            for label, count in per_side.most_common(5):
                share = count / len(subset) * 100
                print(f"      {count:4d}  {share:5.1f}%  {label}")

    # The dominance question.
    doms: List[float] = []
    rps: List[float] = []
    for row in refused:
        match = _NUMBERS.search(str(row.get("plan_reason") or ""))
        if match:
            doms.append(float(match.group(1)))
            rps.append(float(match.group(2)))

    planner_cfg = (config.get("session_planner") or {})
    dom_bar = _f(planner_cfg.get("min_primary_dominance"), 50.0)
    rp_bar = _f(planner_cfg.get("min_return_probability"), 42.0)

    if doms:
        doms.sort()
        near = len([d for d in doms if dom_bar - 5 <= d < dom_bar])
        print(f"\n  Dominance of refused theses  (bar {dom_bar:.0f})")
        print(f"    samples {len(doms)} · median {doms[len(doms) // 2]:.1f} "
              f"· min {doms[0]:.1f} · max {doms[-1]:.1f}")
        for bucket, count in sorted(Counter(_bucket(d) for d in doms).items()):
            print(f"    {bucket:>6}  {count:4d}  {_bar(count, len(doms))}")
        share = near / len(doms) * 100
        print(f"\n    within 5 points of the bar: {near}/{len(doms)} ({share:.0f}%)")
        if share >= 40:
            print("    → clustered just under the bar: the scoring looks")
            print("      systematically conservative, not the threshold.")
        else:
            print("    → spread out: refusals look genuinely earned;")
            print("      the bar is doing its job.")

    if rps:
        rps.sort()
        blocked_by_rp = len([r for r in rps if r < rp_bar])
        print(f"\n  Return probability  (bar {rp_bar:.0f})")
        print(f"    median {rps[len(rps) // 2]:.1f} · below bar {blocked_by_rp}/{len(rps)}")

    if doms and rps:
        only_dom = len([1 for d, r in zip(doms, rps) if d < dom_bar <= 100 and r >= rp_bar])
        print(f"\n  Refused on dominance alone (return probability was fine): {only_dom}")

    # What did the published maps look like, for contrast?
    if ready:
        grades = Counter(str(r.get("planner_grade") or "?") for r in ready)
        biases = Counter(str(r.get("session_bias") or "?") for r in ready)
        print("\n  Published maps")
        print(f"    grades: {dict(grades)}")
        print(f"    bias  : {dict(biases)}")

    archetypes = Counter()
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, dict):
            name = payload.get("day_archetype")
            if name:
                archetypes[str(name)] += 1
    if archetypes:
        print("\n  Day archetypes seen")
        for name, count in archetypes.most_common(10):
            print(f"    {count:4d}  {name}")

    print("=" * 62 + "\n")


if __name__ == "__main__":
    main()
