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


def test_no_audited_rows_explains_itself(capsys):
    """UPDATED 2026-08-04. This asserted the section stays silent when no
    audit exists. That silence was the defect: three minutes after the
    feature shipped the report looked byte-identical to the old one and the
    operator could not tell whether it had deployed. The section now prints
    an explicit "no audit yet" line instead, and the assertion was inverted
    to match -- see test_published_maps_with_no_audit_say_so.
    """
    rows = [{"plan_ready": True, "session_bias": "BUY"}] + [_refused("x")]
    out = _run(rows, capsys)
    assert "What happened to the published maps" in out
    assert "no execution audit" in out
    assert "orders placed" not in out


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
    # UPDATED 2026-08-04: agent_min_confidence 70 -> 67 by operator decision.
    # The assertion is kept, not deleted, so the shipped bar stays pinned to
    # one number -- it now pins the new one. See
    # tests/test_agent_bar_is_sixty_seven.py for the evidence behind it.
    assert float(req.get("agent_min_confidence", 67)) == 67
    planner = CONFIG.get("session_planner") or {}
    assert float(planner.get("min_primary_quality_score", 70)) == 70
    assert int(planner.get("min_authority_alignment_count", 2)) == 2


# ── the empty state must explain itself ─────────────────────────────────────
#
# Added 2026-08-04 after the first deployment. The section was written to
# print only when at least one audit existed, so three minutes after shipping
# -- with 21 published maps, none of them audited yet -- the report looked
# byte-identical to the old one. The operator ran it, saw no new section, and
# reasonably asked why. "No data yet" and "nothing to report" must not look
# the same.

def test_published_maps_with_no_audit_say_so(capsys):
    rows = [{"plan_ready": True, "session_bias": "BUY", "planner_grade": "A"}
            for _ in range(21)]
    rows += [_refused("archetype conviction is LOW: X") for _ in range(30)]
    out = _run(rows, capsys)
    assert "What happened to the published maps" in out, (
        "a window with published maps but no audits printed nothing at all, "
        "which is indistinguishable from the feature not being deployed"
    )
    assert "no execution audit on any of the 21 published maps" in out
    assert "until new maps are published" in out


def test_the_empty_state_does_not_claim_orders_were_placed(capsys):
    rows = [{"plan_ready": True, "session_bias": "BUY"} for _ in range(5)]
    rows += [_refused("x")]
    out = _run(rows, capsys)
    assert "orders placed" not in out
    assert "Why a READY map produced no order" not in out


def test_a_window_with_no_published_maps_stays_quiet(capsys):
    out = _run([_refused("x") for _ in range(5)], capsys)
    assert "What happened to the published maps" not in out


def test_partial_audits_report_both_numbers(capsys):
    """Mixed windows during rollout must show audited and un-audited."""
    rows = [_ready(0, GATE_SHORTFALL) for _ in range(4)]
    rows += [{"plan_ready": True, "session_bias": "BUY"} for _ in range(6)]
    rows += [_refused("x")]
    out = _run(rows, capsys)
    assert "published, no order  : 4" in out
    assert "no audit recorded   : 6" in out


# ── the answer must survive a truncated log ─────────────────────────────────
#
# Added 2026-08-04. The section was correct and complete, and the operator
# still never saw it: four consecutive runs (#16-#19) were copied out of the
# Actions UI cut at the same point, mid dominance-histogram. Everything after
# that was absent. The script completed each time; the channel was lossy.

def _head(rows, capsys, n=18):
    report._report("last 300 cycles", rows, CONFIG)
    return "\n".join(capsys.readouterr().out.splitlines()[:n])


def test_the_order_outcome_appears_near_the_top(capsys):
    head = _head(_rows(), capsys)
    assert "What happened to the published maps" in head, (
        "the answer sits below the fold; a truncated log will not carry it"
    )
    assert "orders placed" in head


def test_it_precedes_the_refusal_families(capsys):
    report._report("last 300 cycles", _rows(), CONFIG)
    out = capsys.readouterr().out
    assert out.index("What happened to the published maps") < out.index("Why refused")


# ── the cause must name the step that actually fired ────────────────────────
#
# `planner_gate_reason` is the ADMISSION verdict. When the gate allowed the
# map and a later check stopped the ladder, that string reads as a pass --
# "3 qualified agents aligned with the mapped direction" -- and grouping on
# it blames the wrong step. Measured on 2026-08-04: 9 of 20 no-order maps.

def _audited(created, gate, allow, stop=None):
    return {
        "plan_ready": True, "session_bias": "BUY", "planner_grade": "A",
        "payload": {"execution_audit": {
            "ladder_created": created, "planner_gate_allow": allow,
            "planner_gate_reason": gate, "ladder_stop_reason": stop,
        }},
    }


def test_the_ladder_stop_reason_is_preferred_over_the_gate_verdict(capsys):
    rows = [_audited(0, "3 qualified agents aligned with the mapped direction",
                     True, "1 live trade(s) already open")] + [_refused("x")]
    out = _head(rows, capsys, 20)
    assert "1 live trade(s) already open" in out
    assert "3 qualified agents aligned" not in out, (
        "a passing gate verdict was reported as the cause of a refusal"
    )


def test_an_allowing_gate_with_no_stop_reason_is_flagged(capsys):
    """Rows written before the field existed must not read as causes."""
    rows = [_audited(0, "2 qualified agents + macro context confirms SELL", True)]
    rows += [_refused("x")]
    out = _head(rows, capsys, 20)
    assert "gate allowed; stop not recorded" in out


def test_a_refusing_gate_still_reports_its_own_reason(capsys):
    rows = [_audited(0, "planner execution requires 3 qualified agents; got 2", False)]
    rows += [_refused("x")]
    out = _head(rows, capsys, 20)
    assert "requires 3 qualified agents" in out
    assert "gate allowed" not in out


def test_the_recorder_is_cleared_between_cycles():
    """A stale reason would read as fact."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ra_stop", os.path.join(ROOT, "scripts", "run_analysis.py"))
    ra = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ra)
    ra._ladder_stop("primary leg terminal state ENTRY_TRIGGERED")
    assert ra._LAST_LADDER_STOP["reason"].startswith("primary leg terminal")
    ra._ladder_stop("2 live trade(s) already open", live_trades=2)
    assert ra._LAST_LADDER_STOP == {
        "reason": "2 live trade(s) already open", "live_trades": 2
    }


def test_every_ladder_exit_records_a_reason():
    """No silent `return 0` may remain in the ladder."""
    import re
    src = open(os.path.join(ROOT, "scripts", "run_analysis.py"), encoding="utf-8").read()
    start = src.index("def _execute_session_plan_ladder(")
    end = src.index("\ndef ", start + 10)
    body = src[start:end]
    silent = re.findall(r"^\s+return 0\s*$", body, re.M)
    assert not silent, (
        f"{len(silent)} silent exit(s) left in the ladder; the audit cannot "
        f"say why no order was created"
    )


# ── how close was the agent count? ──────────────────────────────────────────
#
# "requires 3 qualified agents ... got 2" is the largest single reason a READY
# map produces no order (11 of 20 on 2026-08-04). That refusal is either the
# bar working or the bar missing by a hair, and the two call for opposite
# decisions. `payload.agent_opinions` already records each agent's direction
# and confidence at plan time, so the distinction is measurable from data
# that has been stored all along.

def _with_opinions(created, ops, gate="planner execution requires 3 qualified agents; got 2"):
    out = {
        "plan_ready": True, "session_bias": "BUY", "planner_grade": "A",
        "payload": {
            "execution_audit": {
                "ladder_created": created, "planner_gate_allow": created > 0,
                "planner_gate_reason": gate,
            },
            "agent_opinions": ops,
        },
    }
    # UPDATED: the stored row never carried `agent_opinions` -- that key is
    # only attached to the throwaway copy built for the Telegram card, which
    # is why the first version of this section printed nothing against live
    # data. The audit now carries `agent_reads`; both are exercised.
    out["payload"]["execution_audit"]["agent_reads"] = ops
    out["payload"]["execution_audit"]["agent_min_confidence"] = 70.0
    out["payload"]["execution_audit"]["mapped_side"] = "BUY"
    return out


NEAR_OPS = [
    {"key": "technical", "direction": "BUY", "confidence": 92},
    {"key": "price_action", "direction": "BUY", "confidence": 68},
    {"key": "smc", "direction": "WAIT", "confidence": 37},
    {"key": "multitimeframe", "direction": "BUY", "confidence": 92},
]
FAR_OPS = [
    {"key": "technical", "direction": "BUY", "confidence": 92},
    {"key": "price_action", "direction": "BUY", "confidence": 40},
    {"key": "multitimeframe", "direction": "BUY", "confidence": 92},
]


def test_a_near_miss_is_reported_as_noise(capsys):
    rows = [_with_opinions(0, NEAR_OPS) for _ in range(6)] + [_refused("x")]
    report._report("sim", rows, CONFIG)
    out = capsys.readouterr().out
    assert "AGREED but missed" in out
    assert "within 2 pts: 6" in out
    assert "separating on noise" in out


def test_a_genuine_disagreement_is_reported_as_earned(capsys):
    rows = [_with_opinions(0, FAR_OPS) for _ in range(6)] + [_refused("x")]
    report._report("sim", rows, CONFIG)
    out = capsys.readouterr().out
    assert "AGREED but missed" in out
    assert "within 2 pts: 0" in out
    assert "earning its place" in out


def test_the_agent_that_missed_is_named(capsys):
    rows = [_with_opinions(0, NEAR_OPS) for _ in range(4)] + [_refused("x")]
    report._report("sim", rows, CONFIG)
    assert "price_action" in capsys.readouterr().out


def test_disagreeing_agents_are_not_counted_as_near_misses(capsys):
    """SMC read WAIT, not BUY. It is not a shortfall, it is a dissent."""
    rows = [_with_opinions(0, NEAR_OPS) for _ in range(4)] + [_refused("x")]
    report._report("sim", rows, CONFIG)
    out = capsys.readouterr().out
    block = out[out.index("AGREED but missed"):]
    assert "smc" not in block.split("→")[0]


def test_macro_is_excluded_from_the_agent_count(capsys):
    """Macro confirms separately; it is not one of the five voting agents."""
    ops = NEAR_OPS + [{"key": "macro_fundamental", "direction": "BUY", "confidence": 64}]
    rows = [_with_opinions(0, ops) for _ in range(4)] + [_refused("x")]
    report._report("sim", rows, CONFIG)
    block = capsys.readouterr().out
    block = block[block.index("AGREED but missed"):]
    assert "macro_fundamental" not in block.split("→")[0]


def test_maps_that_produced_orders_are_excluded(capsys):
    rows = [_with_opinions(1, NEAR_OPS) for _ in range(5)] + [_refused("x")]
    report._report("sim", rows, CONFIG)
    assert "AGREED but missed" not in capsys.readouterr().out


def test_the_bar_is_read_from_config_not_hard_coded(capsys):
    import copy as _copy
    cfg = _copy.deepcopy(CONFIG)
    cfg.setdefault("signal_requirements", {})["agent_min_confidence"] = 60
    rows = [_with_opinions(0, NEAR_OPS) for _ in range(4)] + [_refused("x")]
    report._report("sim", rows, cfg)
    out = capsys.readouterr().out
    # price_action at 68 now clears a 60 bar, so it is no longer a shortfall.
    assert "missed the 60% bar" in out or "AGREED but missed" not in out


# ── the agent reads must be PERSISTED, not just rendered ────────────────────
#
# The first version of the near-miss section read `payload.agent_opinions`.
# That key is attached by `_decorate_session_plan_for_delivery`, which builds
# a deepcopy for the Telegram card and throws it away; the row written to
# session_plans never had it. So the section was correct, found nothing, and
# printed nothing against live data -- an assumption about storage that was
# never checked.
#
# `execution_audit.agent_reads` is written with the audit itself.

def test_the_audit_carries_the_agent_reads():
    src = open(os.path.join(ROOT, "scripts", "run_analysis.py"), encoding="utf-8").read()
    assert '"agent_reads": _session_plan_agent_opinions(' in src, (
        "the persisted audit has no agent reads, so the near-miss question "
        "cannot be answered from history"
    )
    assert '"agent_min_confidence": _safe_float(' in src
    assert '"mapped_side": str(' in src


def test_the_section_reads_the_audit_field():
    src = open(
        os.path.join(ROOT, "scripts", "analyze_plan_rejections.py"), encoding="utf-8"
    ).read()
    assert 'audit.get("agent_reads")' in src


def test_missing_agent_reads_are_declared_not_silent(capsys):
    """Absence must be stated, exactly as the empty-audit state is."""
    rows = [{
        "plan_ready": True, "session_bias": "BUY", "planner_grade": "A",
        "payload": {"execution_audit": {
            "ladder_created": 0, "planner_gate_allow": False,
            "planner_gate_reason": "requires 3 qualified agents; got 2",
            "mapped_side": "BUY",
        }},
    } for _ in range(7)]
    rows += [_refused("x")]
    report._report("sim", rows, CONFIG)
    out = capsys.readouterr().out
    assert "AGREED but missed" in out
    assert "no agent reads recorded on 7" in out


def test_the_bar_comes_from_the_audit_when_present(capsys):
    """A row recorded under a different bar must be judged against that bar."""
    ops = [{"key": "price_action", "direction": "BUY", "confidence": 62}]
    row = {
        "plan_ready": True, "session_bias": "BUY", "planner_grade": "A",
        "payload": {"execution_audit": {
            "ladder_created": 0, "planner_gate_allow": False,
            "planner_gate_reason": "got 2", "agent_reads": ops,
            "agent_min_confidence": 65.0, "mapped_side": "BUY",
        }},
    }
    report._report("sim", [row, _refused("x")], CONFIG)
    out = capsys.readouterr().out
    # 65 - 62 = 3 point shortfall, not 70 - 62 = 8.
    assert "median shortfall 3.0 pts" in out
