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


def _reason_family(reason: str) -> str:
    text = (reason or "").lower()
    for needle, label in (
        ("too weak for planning", "primary thesis too weak"),
        ("archetype conviction", "archetype conviction LOW"),
        ("execution refused", "execution refused the leg"),
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
    args = parser.parse_args()

    config = load_config()
    database = DatabaseService(config)
    rows: List[Dict[str, Any]] = database.get_recent_session_plans(
        limit=args.limit, symbol=args.symbol
    )

    if not rows:
        logger.info("No session plans on record yet.")
        return

    ready = [r for r in rows if r.get("plan_ready")]
    refused = [r for r in rows if not r.get("plan_ready")]

    print("\n" + "=" * 62)
    print(f"PLAN REJECTION ANALYSIS — last {len(rows)} cycles")
    print("=" * 62)
    print(f"  published : {len(ready)}  ({len(ready) / len(rows) * 100:.1f}%)")
    print(f"  refused   : {len(refused)}")

    families = Counter(_reason_family(str(r.get("plan_reason") or "")) for r in refused)
    if families:
        print("\n  Why refused")
        for label, count in families.most_common(8):
            print(f"    {count:4d}  {_bar(count, len(refused))}  {label}")

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
