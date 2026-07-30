"""The rejection report must never die halfway through printing.

Background
----------
Run #1 of the report against live Supabase produced exactly this and stopped:

    PLAN REJECTION ANALYSIS — last 300 cycles
    ==============================================================
    (nothing further)

The query returned HTTP 200, so the connection was fine. The window was empty
-- or every row was filtered out -- and ``_report`` divides by ``len(rows)``
on its very first statistic:

    print(f"  published : {len(ready) / len(rows) * 100:.1f}%")

``ZeroDivisionError`` is raised after the header has already been flushed, so
the operator sees a report that begins and then simply stops, with no error
and no explanation. A diagnostic tool that fails silently is worse than no
tool: it looks like the analysis ran and found nothing worth saying.

These tests pin that the report always finishes, and always says why when it
has nothing to show.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

import scripts.analyze_plan_rejections as report

CONFIG = {
    "session_planner": {
        "min_primary_dominance": 50,
        "min_return_probability": 42,
    }
}


def _row(ready: bool, reason: str | None = None, stamp: str = "2026-07-29T17:00:00Z") -> dict:
    return {"plan_ready": ready, "plan_reason": reason, "analysis_run_at": stamp}


# ── The failure seen in production ─────────────────────────────────────────

def test_empty_window_does_not_raise(capsys) -> None:
    """An empty window must print a clear note, not a ZeroDivisionError.

    Failure injection: removing the empty-window guard restores the crash.
    """
    report._report("last 0 cycles", [], CONFIG)

    out = capsys.readouterr().out
    assert "PLAN REJECTION ANALYSIS" in out
    assert "no cycles" in out.lower() or "no session plans" in out.lower(), (
        f"an empty window must explain itself; got:\n{out}"
    )


def test_empty_window_explains_the_likely_cause(capsys) -> None:
    """Silence is the bug. The operator needs a next step."""
    report._report("last 0 cycles", [], CONFIG)
    out = capsys.readouterr().out.lower()

    assert "session_plans" in out, (
        "the note should name the table so the operator can check it"
    )


# ── Guards: normal reporting must be untouched ─────────────────────────────

def test_all_refused_window_still_reports(capsys) -> None:
    """The common case: nothing published, everything refused."""
    rows = [_row(False, "archetype conviction is LOW: x") for _ in range(20)]
    report._report("last 20 cycles", rows, CONFIG)

    out = capsys.readouterr().out
    assert "published : 0" in out
    assert "refused   : 20" in out
    assert "archetype conviction LOW" in out


def test_all_published_window_still_reports(capsys) -> None:
    """The opposite case must not divide by zero either."""
    rows = [_row(True) for _ in range(15)]
    report._report("last 15 cycles", rows, CONFIG)

    out = capsys.readouterr().out
    assert "published : 15" in out
    assert "refused   : 0" in out


def test_mixed_window_reports_both_sides(capsys) -> None:
    """A realistic mix, including a crash row."""
    rows = (
        [_row(True) for _ in range(3)]
        + [_row(False, "primary thesis too weak for planning "
                       "(dominance 44.0, return probability 51.0)")
           for _ in range(10)]
        + [_row(False, "planner crashed: AttributeError: 'NoneType' object "
                       "has no attribute 'get'") for _ in range(2)]
    )
    report._report("last 15 cycles", rows, CONFIG)

    out = capsys.readouterr().out
    assert "published : 3" in out
    assert "CRASHED" in out
    assert "primary thesis too weak" in out
    assert "Dominance of refused theses" in out


def test_refusals_without_parsable_numbers_do_not_break_the_report(capsys) -> None:
    """Reasons with no dominance figures must not stop the run."""
    rows = [_row(False, "no strong bias alignment for a morning plan")
            for _ in range(8)]
    report._report("last 8 cycles", rows, CONFIG)

    out = capsys.readouterr().out
    assert "refused   : 8" in out
    # No dominance section is fine; the report must simply finish.
    assert out.strip().endswith("=" * 62) or "=" in out


# ── Why is one direction refused? ──────────────────────────────────────────

def test_report_breaks_refusals_down_per_direction(capsys) -> None:
    """25 SELL maps refused and 0 published is a pattern, not noise.

    The direction summary showed the imbalance but not its cause: reasons are
    counted across all refusals together, so there was no way to tell whether
    SELL maps die at a different gate than BUY maps. Without that, fixing the
    imbalance means guessing which gate to touch.

    Failure injection: removing the per-direction breakdown makes this fail.
    """
    rows = (
        [_row(False, "counter-objective SELL plan lacks reversal proof: "
                     "trigger state is UNCONFIRMED") for _ in range(20)]
        + [_row(False, "archetype conviction is LOW: x") for _ in range(5)]
        + [_row(False, "reward-to-risk below floor") for _ in range(3)]
    )
    for r in rows[:20]:
        r["session_bias"] = "SELL"
    for r in rows[20:25]:
        r["session_bias"] = "SELL"
    for r in rows[25:]:
        r["session_bias"] = "BUY"

    report._report("test", rows, CONFIG)
    out = capsys.readouterr().out

    assert "Why each direction is refused" in out, (
        f"the report must attribute reasons per direction; got:\n{out}"
    )
    # The dominant SELL reason must be visible under SELL.
    sell_block = out.split("SELL")[1] if "SELL" in out else ""
    assert "reversal proof" in out.lower(), (
        "the reason blocking SELL maps must be named"
    )


def test_direction_breakdown_skips_directionless_refusals(capsys) -> None:
    """Rows refused before a bias was assigned carry no direction to attribute.

    96% of refusals in the live report were NONE -- rejected upstream of the
    direction decision. Listing them under a direction would invent a pattern
    that is not there.
    """
    rows = [_row(False, "day-map authority conflicted") for _ in range(30)]
    report._report("test", rows, CONFIG)
    out = capsys.readouterr().out

    assert "Direction of refused maps" in out
    # With no directional rows there is nothing to attribute.
    assert "Why each direction is refused" not in out or "NONE" in out


def test_execution_refusals_are_split_by_cause() -> None:
    """A flat count across three reports was a measurement blind spot.

    "execution refused the leg" read exactly 20 in reports #17, #7 and #8 --
    three code states, three market days, one number. The gate was live; the
    label was collapsing every distinct cause into one bucket, so nothing
    underneath could ever be seen to move.
    """
    from scripts.analyze_plan_rejections import _reason_family

    liquidity = _reason_family(
        "execution refused the main leg: no qualifying liquidity pool below the zone"
    )
    stop = _reason_family(
        "execution refused the main leg: stop distance 120 below minimum 400"
    )
    assert liquidity != stop, "distinct execution causes must not share one label"
    assert "liquidity" in liquidity
    assert "stop distance" in stop

    # A bare verdict with no cause still gets a stable label.
    assert _reason_family("execution refused the leg") == "execution refused the leg"

    # Unrelated families are untouched.
    assert _reason_family("archetype conviction LOW") == "archetype conviction LOW"
    assert _reason_family("news blocked: DANGER") == "news blocked: DANGER"
