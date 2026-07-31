"""A refusal reason that embeds a measured value must not splinter.

REPORT #10, 300 cycles
----------------------
    Why refused
        51  archetype conviction LOW
        38  not enough support
        36  primary thesis too weak
        24  primary quality 68.0 below planner floor 70.0
        12  primary quality 62.0 below planner floor 70.0
        10  primary quality 55.0 below planner floor 70.0
         8  primary quality 50.0 below planner floor 70.0
         6  primary quality 63.0 below planner floor 70.0
         5  primary quality 58.0 below planner floor 70.0
         5  primary quality 54.0 below planner floor 70.0

Read as printed, the top cause is "archetype conviction LOW" at 51 and the
quality floor looks like seven small problems of 24 or fewer.

Summed, the quality floor is 70 -- the single largest cause in the report,
and invisible as such. Anyone tuning from this list would work on the wrong
gate, which is the entire purpose the list exists to serve.

This is the mirror image of a bug already fixed in this file. "execution
refused the leg" once collapsed every distinct verdict into ONE bucket, so a
live gate looked dead. Here a single gate is split into MANY buckets, so a
dominant gate looks minor. Both come from the same root: the family label
does not distinguish the gate's identity from the value it measured.

THE SCORE DISTRIBUTION IS THE POINT
-----------------------------------
Grouping restores the ranking, but the spread is what says whether the floor
is calibrated, so it is printed separately rather than discarded.

24 of the 70 scored exactly 68.0 against a floor of 70.0. The smallest award
in ``SMCAgent._setup_quality`` is +4 (trend is BULLISH/BEARISH), so nothing a
68 can earn lands it on 70 -- it must gain a whole extra qualifying
condition. The bar is not trimming a weak tail; it is cutting through the
middle of the population at a point those setups cannot step over.

That is a finding for the user to act on, not a change to make silently:
lowering the floor is a quality decision he has explicitly reserved
("لا تخفض العتبة اريد جودة وليس كثرة"). The report's job is to show him the
cliff, accurately.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
from collections import Counter
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "analyze_plan_rejections_grouping",
    os.path.join(ROOT, "scripts", "analyze_plan_rejections.py"),
)
apr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apr)

from utils.helpers import load_config  # noqa: E402

# Report #10 verbatim: (score, how many cycles refused at that score)
REPORT_10_QUALITY = [(68.0, 24), (62.0, 12), (55.0, 10), (50.0, 8),
                     (63.0, 6), (58.0, 5), (54.0, 5)]
REPORT_10_QUALITY_TOTAL = sum(n for _, n in REPORT_10_QUALITY)  # 70
REPORT_10_ARCHETYPE = 51


def _quality_reason(score: float) -> str:
    return f"primary quality {score} below planner floor 70.0"


def _rows() -> list[dict]:
    rows: list[dict] = []
    for score, count in REPORT_10_QUALITY:
        for _ in range(count):
            rows.append({"plan_reason": _quality_reason(score),
                         "plan_ready": False, "session_bias": "none"})
    for _ in range(REPORT_10_ARCHETYPE):
        rows.append({"plan_reason": "archetype conviction LOW for this map",
                     "plan_ready": False, "session_bias": "BUY"})
    for _ in range(33):
        rows.append({"plan_reason": "", "plan_ready": True, "session_bias": "BUY"})
    return rows


def _render() -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        apr._report("test window", _rows(), load_config())
    return buffer.getvalue()


def test_every_quality_refusal_shares_one_family() -> None:
    families = {apr._reason_family(_quality_reason(score))
                for score, _ in REPORT_10_QUALITY}
    assert len(families) == 1, (
        f"seven scores produced {len(families)} labels: {families}. Each "
        "distinct measurement becomes its own row and the gate disappears "
        "from the ranking."
    )


def test_the_quality_floor_is_the_top_cause() -> None:
    counts = Counter(apr._reason_family(str(r.get("plan_reason") or ""))
                     for r in _rows() if not r.get("plan_ready"))
    label, count = counts.most_common(1)[0]

    assert count == REPORT_10_QUALITY_TOTAL == 70
    assert "quality" in label and "floor" in label, (
        f"top cause is {label!r} at {count}; the report ranked "
        f"'archetype conviction LOW' at {REPORT_10_ARCHETYPE} instead"
    )
    assert count > REPORT_10_ARCHETYPE


def test_other_families_are_not_swallowed_by_the_grouping() -> None:
    """Widening one label must not merge unrelated gates into it."""
    assert apr._reason_family("archetype conviction LOW") != apr._reason_family(
        _quality_reason(68.0)
    )
    for reason in (
        "primary thesis too weak for planning (dominance 40, return probability 30)",
        "not enough supporting agents",
        "execution refused the main leg: target liquidity too close",
    ):
        assert "planner floor" not in apr._reason_family(reason)


def test_the_score_distribution_is_still_reported() -> None:
    """Grouping must not destroy the detail that shows the cliff."""
    output = _render()
    assert "Quality scores that missed the floor" in output
    assert "samples 70" in output
    for score, count in REPORT_10_QUALITY:
        assert f"{score:5.0f}  {count:4d}" in output, (
            f"score {score} x{count} vanished from the distribution"
        )


def test_the_cliff_at_the_floor_is_called_out() -> None:
    output = _render()
    assert "within 4 points of the floor: 24/70" in output
    assert "cutting through the middle" in output, (
        "24 plans sat exactly 2 points short of the bar while the smallest "
        "award in _setup_quality is +4; the report must say so"
    )


def test_a_healthy_distribution_is_not_called_a_cliff() -> None:
    """The warning must be earned by the data, not printed always."""
    rows = [
        {"plan_reason": _quality_reason(score), "plan_ready": False,
         "session_bias": "none"}
        for score in (30.0, 35.0, 40.0, 42.0, 45.0, 48.0, 50.0, 52.0, 55.0, 58.0)
    ]
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        apr._report("healthy", rows, load_config())
    output = buffer.getvalue()

    assert "spread well below the floor" in output
    assert "cutting through the middle" not in output


def test_no_threshold_was_changed_by_this_report_fix() -> None:
    """This package measures. It must not tune anything."""
    config = load_config()
    planner = config.get("session_planner") or {}
    assert float(planner.get("min_primary_quality_score")) == 70.0, (
        "the floor is the user's quality decision; the report exists to show "
        "him where it lands, not to move it"
    )
    # archetype_conviction is nested under session_planner, not at the root.
    # An earlier version of this test looked at the root, found nothing, and
    # failed -- the test was wrong, not the config, so it is corrected here
    # rather than dropped.
    conviction = planner.get("archetype_conviction") or {}
    assert float(conviction.get("medium_conviction_confidence")) == 60.0
    assert float(conviction.get("high_conviction_confidence")) == 75.0


def test_fault_injection_the_old_label_splintered_the_gate() -> None:
    """Reproduce the pre-fix labelling and show the gate ranks below noise."""
    def old_family(reason: str) -> str:
        text = (reason or "").lower()
        for needle, label in (
            ("too weak for planning", "primary thesis too weak"),
            ("archetype conviction", "archetype conviction LOW"),
        ):
            if needle in text:
                return label
        return (reason or "unknown")[:48]  # the catch-all that splintered

    counts = Counter(old_family(str(r.get("plan_reason") or ""))
                     for r in _rows() if not r.get("plan_ready"))
    top_label, top_count = counts.most_common(1)[0]

    assert top_label == "archetype conviction LOW" and top_count == 51, (
        "under the old labelling the report's headline cause is the archetype "
        "gate at 51, while the quality floor -- 70 refusals -- is scattered "
        "across seven rows of 24 or fewer"
    )
    quality_rows = [c for l, c in counts.items() if "planner floor" in l]
    assert len(quality_rows) == 7 and max(quality_rows) == 24
    assert sum(quality_rows) == REPORT_10_QUALITY_TOTAL
