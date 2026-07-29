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
