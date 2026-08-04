"""A refusal count is a count of cycles, not of opportunities.

WHY THIS MATTERS
----------------
The analysis loop runs every ~5 minutes against the same market structure. An
unchanged setup is re-scored and re-refused on every pass, and each pass
writes a new session_plans row. So "91 refusals" can mean 91 distinct setups
turned away, or a dozen setups that kept repeating -- and the two readings
call for completely different responses.

THE EVIDENCE THAT FORCED THIS
-----------------------------
On 2026-08-04 the quality histogram showed::

    69   3
    68  17   ← 4.2x its neighbours
    66   5
    65   5

No scoring formula produces a spike like that. Two hypotheses were tested
against the real ``SMCAgent._setup_quality`` and both were disproved:

* quantisation by the +4 award -- wrong, ``rank_score * 0.12`` is continuous
  and lets the score take any value;
* a common input combination landing on 68 -- wrong, an exhaustive simulation
  over every combination gives a flat distribution (66:332, 68:325, 67:323),
  with no peak.

That leaves repetition. ``primary_poi.state_key`` encodes role, direction,
scenario type and zone bounds, and is already persisted on every row, so the
question is answerable from stored data rather than argued.

WHAT THIS IS NOT
----------------
Nothing about refusal behaviour changes. This counts what is already there.

FAULT INJECTION
---------------
Delete the "Distinct setups behind those" block and
``test_a_repeated_setup_is_not_counted_as_many`` fails: 17 rows from one zone
report as 17 separate refusals with nothing to say otherwise.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "plan_rejections_setups",
    os.path.join(ROOT, "scripts", "analyze_plan_rejections.py"),
)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)

from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()

ZONE_A = "DAYMAP::PRIMARY::XAU/USD::SELL::STRUCTURE_CONTINUATION::4034.09:4040.09"
ZONE_B = "DAYMAP::PRIMARY::XAU/USD::BUY::FAILED_RECLAIM::4061.90:4070.88"


def _refused(score, key):
    return {
        "plan_ready": False,
        "plan_reason": f"primary quality {score} below planner floor 70.0",
        "payload": {"primary_poi": ({"state_key": key} if key else {})},
    }


def _run(rows, capsys):
    report._report("sim", rows, CONFIG)
    return capsys.readouterr().out


# ── the question ────────────────────────────────────────────────────────────

def test_a_repeated_setup_is_not_counted_as_many(capsys):
    rows = [_refused(68.0, ZONE_A) for _ in range(17)]
    rows += [_refused(59.0, ZONE_B) for _ in range(3)]
    out = _run(rows, capsys)
    assert "Distinct setups behind those" in out, (
        "20 rows are reported as 20 refusals with no way to tell whether "
        "they are 20 opportunities or one setup re-scored"
    )
    assert "unique setups : 2" in out
    assert "17x" in out
    assert "overstates how many distinct" in out


def test_distinct_setups_are_reported_as_distinct(capsys):
    rows = [_refused(60 + i * 0.5, f"{ZONE_A}#{i}") for i in range(20)]
    out = _run(rows, capsys)
    assert "unique setups : 20" in out
    assert "genuinely separate opportunities" in out


def test_the_repeated_zone_is_named(capsys):
    rows = [_refused(68.0, ZONE_A) for _ in range(9)] + [_refused(59.0, ZONE_B)]
    out = _run(rows, capsys)
    assert "4034.09:4040.09" in out


# ── degrade honestly ────────────────────────────────────────────────────────

def test_rows_without_a_state_key_are_declared(capsys):
    rows = [_refused(68.0, None) for _ in range(12)]
    out = _run(rows, capsys)
    assert "no state_key recorded on 12" in out
    assert "cannot tell distinct setups from repeats" in out


def test_a_partial_window_reports_both(capsys):
    rows = [_refused(68.0, ZONE_A) for _ in range(6)]
    rows += [_refused(68.0, None) for _ in range(4)]
    out = _run(rows, capsys)
    assert "unique setups : 1" in out
    assert "no state_key recorded : 4" in out


def test_refusals_of_other_kinds_are_not_counted(capsys):
    """Only quality-floor refusals carry a comparable score."""
    rows = [_refused(68.0, ZONE_A) for _ in range(5)]
    rows += [{"plan_ready": False, "plan_reason": "archetype conviction is LOW: X"}
             for _ in range(30)]
    out = _run(rows, capsys)
    assert "unique setups : 1" in out


def test_an_empty_window_does_not_crash(capsys):
    report._report("sim", [{"plan_ready": False, "plan_reason": "conviction LOW"}], CONFIG)
    capsys.readouterr()


def test_the_existing_histogram_is_unchanged(capsys):
    rows = [_refused(68.0, ZONE_A) for _ in range(17)]
    out = _run(rows, capsys)
    assert "Quality scores that missed the floor" in out
    assert "samples 17" in out


def test_no_threshold_was_changed():
    planner = CONFIG.get("session_planner") or {}
    assert float(planner.get("min_primary_quality_score", 70)) == 70
    req = CONFIG.get("signal_requirements") or {}
    assert int(req.get("min_agents_agree", 3)) == 3
    assert float(req.get("agent_min_confidence", 70)) == 70
