""""Published" counts maps, not orders. The report must say which.

THE GAP
-------
``analyze_plan_rejections`` splits every cycle into ``published`` and
``refused`` on ``plan_ready``. That answers "why are day maps refused".

It does not answer the question the operator actually kept asking: *plans
arrive, orders do not.*

A READY map still has to clear ``_planner_execution_gate`` before any pending
order is created. When that gate refuses, nothing reaches the market -- and
the map is still filed under ``published``, indistinguishable from one that
produced a live order.

THE CASE
--------
2026-08-04 12:38. The planner published::

    Session plan ready for XAU/USD: BUY FAILED_RECLAIM_CONTINUATION
    primary=4066.39 | score=80.2

Grade A, 80.2%. No pending order followed. Running the real gate on the
agent reads from that card returns::

    allow          : False
    support_count  : 2  (technical, multitimeframe)
    reason         : planner execution requires 3 qualified agents
                     or 2 agents + macro/gemini; got 2

Price Action read BUY at 68% -- two points under ``agent_min_confidence`` --
so it was skipped, leaving two supporters instead of three.

``run_analysis`` already records that verdict in
``payload.execution_audit`` (``planner_gate_allow``, ``planner_gate_reason``,
``ladder_created``). Nothing read it back: the report had zero references to
``execution_audit``. So the one number that explains the silence -- READY
maps that produced no order -- was never printed.

WHAT WAS ADDED
--------------
A section that reads the audit and reports orders placed against maps
published, then groups the gate reasons for the ones that produced nothing.

WHAT THIS IS NOT
----------------
No threshold moved. ``min_agents_agree`` is still 3 and
``agent_min_confidence`` is still 70; no map that was refused becomes
executable. This is measurement, not admission.

FAULT INJECTION
---------------
Delete the ``What happened to the published maps`` block and
``test_the_report_separates_maps_from_orders`` fails: a run where 17 of 20
READY maps produced no order prints as 20 published with no hint that the
market never saw them.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "plan_rejections_under_test",
    os.path.join(ROOT, "scripts", "analyze_plan_rejections.py"),
)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)

from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()

GATE_SHORTFALL = (
    "planner execution requires 3 qualified agents or 2 agents + macro/gemini; got 2"
)
GATE_OPPOSED = "2 qualified agents oppose the mapped BUY (limit 1): smc, classical"


def _ready(created: int, reason: str | None = None) -> dict:
    return {
        "plan_ready": True, "session_bias": "BUY", "planner_grade": "A",
        "payload": {
            "execution_audit": {
                "ladder_created": created,
                "planner_gate_allow": created > 0,
                "planner_gate_reason": reason,
            }
        },
    }


def _refused(reason: str) -> dict:
    return {"plan_ready": False, "plan_reason": reason}


def _rows():
    return (
        [_ready(0, GATE_SHORTFALL) for _ in range(14)]
        + [_ready(0, GATE_OPPOSED) for _ in range(3)]
        + [_ready(1) for _ in range(3)]
        + [_refused("archetype conviction is LOW: X at 50%") for _ in range(60)]
    )


def _run(rows, capsys) -> str:
    report._report("last 80 cycles", rows, CONFIG)
    return capsys.readouterr().out


# ── the gap ─────────────────────────────────────────────────────────────────

def test_the_report_separates_maps_from_orders(capsys):
    """20 published with 3 orders must not read the same as 20 orders."""
    out = _run(_rows(), capsys)
    assert "orders placed" in out, (
        "the report counts published maps but never says how many became "
        "orders, which is the question being asked"
    )
    assert "3 of 20" in out, out
    assert "published, no order  : 17" in out, out


def test_the_gate_reason_is_grouped(capsys):
    out = _run(_rows(), capsys)
    assert "Why a READY map produced no order" in out
    assert "planner execution requires 3 qualified agents" in out
    assert "qualified agents oppose the mapped BUY" in out


def test_the_dominant_gate_reason_is_ranked_first(capsys):
    out = _run(_rows(), capsys)
    block = out[out.index("Why a READY map produced no order"):]
    lines = [ln for ln in block.splitlines() if ln.strip() and ln.strip()[0].isdigit()]
    assert lines, block
    assert "14" in lines[0], lines[:3]


def test_the_section_says_these_are_not_planning_failures(capsys):
    """The distinction is the whole point; it must be stated."""
    out = _run(_rows(), capsys)
    assert "NOT planning failures" in out


# ── it must not distort the existing report ─────────────────────────────────

def test_the_published_and_refused_counts_are_unchanged(capsys):
    out = _run(_rows(), capsys)
    assert "published : 20" in out
    assert "refused   : 60" in out


def test_refusal_families_are_still_reported(capsys):
    out = _run(_rows(), capsys)
    assert "archetype conviction LOW" in out


# ── degrade honestly ────────────────────────────────────────────────────────

def test_maps_without_an_audit_are_counted_not_guessed(capsys):
    """Older rows predate the audit; they must be declared, not assumed."""
    rows = [_ready(1)] + [{"plan_ready": True, "session_bias": "BUY"}] * 4
    rows += [_refused("x") for _ in range(3)]
    out = _run(rows, capsys)
    assert "no audit recorded   : 4" in out, out


def test_no_audited_rows_prints_no_section(capsys):
    rows = [{"plan_ready": True, "session_bias": "BUY"}] + [_refused("x")]
    out = _run(rows, capsys)
    assert "What happened to the published maps" not in out


def test_a_healthy_run_reports_every_map_as_an_order(capsys):
    rows = [_ready(1) for _ in range(5)] + [_refused("x")]
    out = _run(rows, capsys)
    assert "orders placed        : 5 of 5" in out
    assert "Why a READY map produced no order" not in out


def test_a_missing_reason_does_not_crash(capsys):
    out = _run([_ready(0, None), _refused("x")], capsys)
    assert "unknown" in out


def test_an_empty_window_is_handled(capsys):
    report._report("empty", [], CONFIG)
    capsys.readouterr()


# ── the thresholds this report describes must not have moved ────────────────

def test_no_admission_threshold_was_changed():
    req = CONFIG.get("signal_requirements") or {}
    assert int(req.get("min_agents_agree", 3)) == 3
    assert float(req.get("agent_min_confidence", 70)) == 70
    planner = CONFIG.get("session_planner") or {}
    assert float(planner.get("min_primary_quality_score", 70)) == 70
    assert int(planner.get("min_authority_alignment_count", 2)) == 2
