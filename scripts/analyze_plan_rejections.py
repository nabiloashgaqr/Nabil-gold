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
        # Group crashes by WHERE, not by the first 44 characters.
        #
        # Every planner crash reads "planner crashed: AttributeError:
        # 'NoneType' object has no attribute 'get'". Truncated to 44 chars
        # they collapse into one bucket, so five crashes at five different
        # lines looked like one recurring fault -- and none of them could be
        # located. The site is now appended to the stored reason as
        # "... @ file.py:line in func"; keying on it separates them.
        site = ""
        if " @ " in (reason or ""):
            site = str(reason).rsplit(" @ ", 1)[-1].strip()
        kind = str(reason or "")[len("planner crashed: "):].split(":")[0].strip()
        if site:
            return f"⚠ CRASH — {kind} @ {site}"[:56]
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
    # The opposite mistake to the one above: a reason that embeds a MEASURED
    # value splinters into one row per value, so the gate disappears from the
    # ranking even when it is the single largest cause.
    #
    # Report #10 printed seven separate rows -- "primary quality 68.0 below
    # planner floor 70.0" (24), then 62.0 (12), 55.0 (10), 50.0 (8), 63.0 (6),
    # 58.0 (5), 54.0 (5) -- so the quality floor read as seven small problems
    # of 24 or fewer, while "archetype conviction LOW" (51) took the top slot.
    # Summed, the floor was 70+: the actual number one, and invisible as such.
    #
    # The distribution of those scores is still worth seeing, so it is printed
    # separately below rather than discarded.
    if "below planner floor" in text:
        return "primary quality below planner floor"
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


def _order_outcome_section(ready: List[Dict[str, Any]], config: Dict[str, Any]) -> None:
    """Orders placed vs maps published.

    Called immediately after the published/refused counts, not at the end
    of the report. The run log is copied out of the Actions UI and four
    consecutive runs were cut mid-way through the dominance histogram, so
    everything below it -- including this section -- never reached the
    operator. The script completed fine each time; the channel was lossy.
    A conclusion printed below the cut has not been reported.
    """
    sig_cfg = (config.get("signal_requirements") or {}) if isinstance(config, dict) else {}
    min_agent_conf = _f(sig_cfg.get("agent_min_confidence"), 70.0) or 70.0

    audited = []
    for row in ready:
        payload = row.get("payload")
        audit = (payload or {}).get("execution_audit") if isinstance(payload, dict) else None
        if isinstance(audit, dict) and audit:
            audited.append(audit)

    if ready and not audited:
        # SILENCE IS NOT AN ANSWER.
        #
        # The section below only prints when at least one published map
        # carries an execution audit. Those audits are written by
        # run_analysis as each cycle runs, so immediately after the feature
        # ships every row in the window predates it and the section vanishes
        # entirely -- looking exactly like the old report and leaving the
        # operator's question unanswered with no explanation.
        #
        # That happened on 2026-08-04: the report was run three minutes after
        # the change was deployed, 21 maps were published, none carried an
        # audit, and nothing was printed. Saying so costs one line and
        # distinguishes "no data yet" from "nothing to report".
        print("\n  What happened to the published maps")
        print(f"    no execution audit on any of the {len(ready)} published maps")
        print(
            "    → audits are written as each cycle runs, so this stays empty\n"
            "      until new maps are published after the change was deployed.\n"
            "      Re-run once a fresh READY map has been produced."
        )

    if audited:
        placed = [a for a in audited if int(a.get("ladder_created") or 0) > 0]
        blocked = [a for a in audited if int(a.get("ladder_created") or 0) == 0]
        print("\n  What happened to the published maps")
        print(f"    orders placed        : {len(placed)} of {len(audited)} audited")
        print(f"    published, no order  : {len(blocked)}")
        if len(audited) < len(ready):
            print(f"    (no audit recorded   : {len(ready) - len(audited)})")

        if blocked:
            # PREFER THE LADDER'S OWN REASON.
            #
            # `planner_gate_reason` is the verdict of the admission gate. When
            # the gate ALLOWED the map and a later check stopped the ladder,
            # that string reads as a pass -- "3 qualified agents aligned with
            # the mapped direction" -- and grouping on it points at the wrong
            # step. Measured on 2026-08-04: 9 of 20 no-order maps looked like
            # successes for exactly this reason.
            #
            # `ladder_stop_reason` names the check that actually fired. Fall
            # back to the gate reason for rows written before that field
            # existed, marking them so the two are never confused.
            def _cause(audit):
                stop = str(audit.get("ladder_stop_reason") or "").strip()
                if stop:
                    return stop[:56]
                gate = str(audit.get("planner_gate_reason") or "").strip()
                if audit.get("planner_gate_allow") and gate:
                    return f"[gate allowed; stop not recorded] {gate}"[:56]
                return (gate or "unknown")[:56]

            reasons = Counter(_cause(a) for a in blocked)
            print("\n  Why a READY map produced no order")
            widest = max(reasons.values())
            for reason, count in reasons.most_common(10):
                bar = "█" * max(1, int(count / widest * 24))
                print(f"    {count:4d}  {bar:<24}  {reason}")
            print(
                "\n    → these are NOT planning failures. The map was good enough\n"
                "      to publish and was then refused at execution."
            )

        # ── HOW CLOSE WAS THE AGENT COUNT? ──────────────────────────────
        #
        # "requires 3 qualified agents ... got 2" is the single largest
        # reason a READY map produces no order. That refusal is either the
        # bar doing its job, or the bar missing by a hair -- and the two
        # call for opposite decisions.
        #
        # The distinction is measurable from data already stored:
        # `payload.agent_opinions` records each agent's direction and
        # confidence at plan time. An agent that agreed with the map but
        # fell short of `agent_min_confidence` is a NEAR MISS. If most
        # shortfalls are within a point or two, the threshold is filtering
        # on noise; if they are far below, the agents genuinely disagreed
        # and the silence is correct.
        #
        # Reported, never acted on. No threshold is read as a target here.
        near = []
        missing_reads = 0
        for row in ready:
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            audit = payload.get("execution_audit") or {}
            if int(audit.get("ladder_created") or 0) > 0:
                continue
            # Read the audit's own copy first.
            #
            # `payload.agent_opinions` is only ever attached to the throwaway
            # object built for the Telegram card, never to the stored row --
            # which is why the first version of this section printed nothing
            # at all. `execution_audit.agent_reads` is written with the audit
            # itself, so it is the field that actually exists in history.
            reads = audit.get("agent_reads") or payload.get("agent_opinions") or []
            bar = _f(audit.get("agent_min_confidence"), min_agent_conf) or min_agent_conf
            side = str(
                audit.get("mapped_side")
                or row.get("session_bias")
                or payload.get("session_bias")
                or ""
            ).upper()
            if side not in {"BUY", "SELL"}:
                continue
            if not reads:
                missing_reads += 1
                continue
            for op in reads:
                if str(op.get("key")) == "macro_fundamental":
                    continue  # confirms separately, not part of the count
                if str(op.get("direction") or "").upper() != side:
                    continue
                conf = _f(op.get("confidence"))
                if 0 < conf < bar:
                    near.append((bar - conf, str(op.get("key")), conf))

        if not near and missing_reads:
            print(f"\n  Agents that AGREED but missed the {min_agent_conf:.0f}% bar")
            print(f"    no agent reads recorded on {missing_reads} of these maps")
            print("    → agent_reads is written with the execution audit, so this\n"
                  "      stays empty until new maps are published after the change\n"
                  "      was deployed. Re-run once a fresh READY map exists.")

        if near:
            near.sort()
            within_2 = sum(1 for gap, _, _ in near if gap <= 2.0)
            within_5 = sum(1 for gap, _, _ in near if gap <= 5.0)
            print(f"\n  Agents that AGREED but missed the {min_agent_conf:.0f}% bar")
            print(f"    occurrences: {len(near)} · within 2 pts: {within_2} · "
                  f"within 5 pts: {within_5}")
            by_agent = Counter(key for _, key, _ in near)
            for key, count in by_agent.most_common(6):
                gaps = [g for g, k, _ in near if k == key]
                print(f"    {count:4d}  {key:<16} median shortfall {sorted(gaps)[len(gaps) // 2]:.1f} pts")
            if within_2 >= max(3, len(near) // 2):
                print("\n    → most shortfalls are within 2 points. The bar is\n"
                      "      separating on noise, not on disagreement.")
            else:
                print("\n    → shortfalls are spread well below the bar; the\n"
                      "      agents genuinely disagreed. The bar is earning its place.")


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

    _order_outcome_section(ready, config)

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

    # How far below the floor did the quality scores actually land?
    #
    # Grouping them into one family (see _reason_family) restores the gate to
    # the ranking, but the spread is the part that says whether the floor is
    # calibrated. A cluster sitting two points short means the bar is cutting
    # through the middle of the population; a long tail means the refusals are
    # genuinely poor setups.
    #
    # Report #10: 24 plans scored exactly 68.0 against a floor of 70.0. The
    # smallest award in _setup_quality is +4, so nothing a 68 can earn lands
    # it on 70 -- it must gain a whole extra qualifying condition. That is a
    # cliff, and it is only visible once the scores are read together.
    quality_scores: List[float] = []
    for row in refused:
        text = str(row.get("plan_reason") or "")
        if "below planner floor" not in text.lower():
            continue
        match = re.search(r"primary quality\s+([0-9]+(?:\.[0-9]+)?)", text)
        if match:
            try:
                quality_scores.append(float(match.group(1)))
            except ValueError:
                continue
    if quality_scores:
        floor_match = re.search(r"planner floor\s+([0-9]+(?:\.[0-9]+)?)", 
                                " ".join(str(r.get("plan_reason") or "") for r in refused))
        floor = float(floor_match.group(1)) if floor_match else 70.0
        ordered = sorted(quality_scores)
        median = ordered[len(ordered) // 2]
        gaps = [floor - s for s in quality_scores]
        within_4 = sum(1 for g in gaps if g <= 4.0)
        print(f"\n  Quality scores that missed the floor ({floor:.0f})")
        print(f"    samples {len(quality_scores)} · median {median:.1f} · "
              f"min {min(ordered):.1f} · max {max(ordered):.1f}")
        buckets = Counter(round(s, 0) for s in quality_scores)
        for score, count in sorted(buckets.items(), reverse=True)[:8]:
            print(f"     {score:5.0f}  {count:4d}  {_bar(count, len(quality_scores))}")
        print(f"\n    within 4 points of the floor: {within_4}/{len(quality_scores)} "
              f"({within_4 / len(quality_scores) * 100:.0f}%)")
        if within_4 / len(quality_scores) >= 0.3:
            print("    → the bar is cutting through the middle of the population,")
            print("      not trimming a weak tail. The smallest award in")
            print("      _setup_quality is +4, so these cannot inch over the line.")
        else:
            print("    → the misses are spread well below the floor;")
            print("      the bar is separating weak setups, as intended.")

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

    # ── What happened to the maps that WERE published? ──────────────────
    #
    # "published" counts maps the planner was willing to build. It does not
    # count orders. A READY map still has to clear `_planner_execution_gate`,
    # and when that refuses, nothing reaches the market -- yet the map is
    # filed here as a success.
    #
    # That gap is exactly the question the operator kept asking: plans arrive,
    # orders do not. On 2026-08-04 a BUY map scored A 80.2% and published, and
    # no pending order was created, because only two qualified agents backed
    # it. The refusal was recorded in `payload.execution_audit` by
    # run_analysis, and nothing ever read it back.
    #
    # Reading it here turns "why are maps refused" into "why are orders not
    # placed", which is the question that matters.
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
