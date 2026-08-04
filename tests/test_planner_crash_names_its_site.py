"""A crash report that cannot be located is not a report.

THE DEFECT
----------
When the session planner raises, run_analysis catches it and stores::

    "planner crashed: AttributeError: 'NoneType' object has no attribute 'get'"

That names the symptom and nothing else. There are hundreds of ``.get(``
calls under ``build_plan`` and the message fits every one of them.

Five crashed cycles appeared in every rejection report from #16 through #22 --
1.7% of all cycles, producing no map at all -- and none of them could be
acted on. ``logger.exception`` does write a traceback, but it goes to the run
log while the REPORT reads the stored row, and the stored row had only text.

Worse, ``_reason_family`` truncated the reason to 44 characters when grouping.
Since every crash begins with the same words, five crashes at five different
lines collapsed into a single bucket that looked like one recurring fault.

THE FIX
-------
``_crash_site`` walks the traceback and returns the deepest frame inside this
repository -- skipping site-packages, where the raise usually surfaces but the
bug rarely lives -- as ``file.py:line in func``. It is appended to the stored
reason, kept in ``crash_site``, and the full traceback is stored alongside.
``_reason_family`` keys crashes on the site so distinct faults separate.

WHAT THIS IS NOT
----------------
No behaviour changes: the planner still fails the same way and the cycle still
produces no map. This makes the failure findable.

FAULT INJECTION
---------------
Remove the ``@ {_crash_site(exc)}`` suffix and
``test_the_stored_reason_carries_the_site`` fails; remove the site branch in
``_reason_family`` and ``test_two_crash_sites_do_not_collapse`` fails.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_ra_spec = importlib.util.spec_from_file_location(
    "run_analysis_crash_site", os.path.join(ROOT, "scripts", "run_analysis.py")
)
ra = importlib.util.module_from_spec(_ra_spec)
_ra_spec.loader.exec_module(ra)

_rep_spec = importlib.util.spec_from_file_location(
    "plan_rejections_crash", os.path.join(ROOT, "scripts", "analyze_plan_rejections.py")
)
report = importlib.util.module_from_spec(_rep_spec)
_rep_spec.loader.exec_module(report)

from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()
BARE = "planner crashed: AttributeError: 'NoneType' object has no attribute 'get'"


def _raise_none_get():
    payload = None
    return payload.get("x")


# ── the site ────────────────────────────────────────────────────────────────

def test_crash_site_names_file_line_and_function():
    try:
        _raise_none_get()
    except AttributeError as exc:
        site = ra._crash_site(exc)
    assert "_raise_none_get" in site, site
    assert ":" in site, site


def test_crash_site_prefers_project_code_over_dependencies():
    """The raise often surfaces inside a library; the bug is usually ours."""
    import json as _json
    try:
        _json.loads("{not json")
    except Exception as exc:
        site = ra._crash_site(exc)
    # This test file IS project code, so the deepest owned frame is here.
    assert "site-packages" not in site
    assert site


def test_crash_site_never_raises():
    class Odd(Exception):
        pass
    assert ra._crash_site(Odd()) in {"unknown", "outside project"}


def test_the_stored_reason_carries_the_site():
    src = open(os.path.join(ROOT, "scripts", "run_analysis.py"), encoding="utf-8").read()
    assert 'f" @ {_crash_site(exc)}"' in src, (
        "the stored crash reason has no location, so the five crashed cycles "
        "in every report since #16 cannot be found"
    )
    assert '"crash_site": _crash_site(exc)' in src
    assert '"crash_traceback"' in src


# ── grouping ────────────────────────────────────────────────────────────────

def test_two_crash_sites_do_not_collapse():
    a = report._reason_family(f"{BARE} @ session_planner.py:912 in _resolve_authority")
    b = report._reason_family(f"{BARE} @ smc_agent.py:1148 in _setup_candidates")
    assert a != b, "distinct crash sites grouped into one bucket"
    assert "session_planner.py:912" in a
    assert "smc_agent.py:1148" in b


def test_the_family_still_marks_it_as_a_crash():
    label = report._reason_family(f"{BARE} @ session_planner.py:912 in _x")
    assert label.startswith("⚠ CRASH")
    assert "AttributeError" in label


def test_a_legacy_reason_without_a_site_still_groups():
    """Rows written before the change must not break the report."""
    label = report._reason_family(BARE)
    assert label.startswith("⚠ CRASH")


def test_crashes_are_still_counted_separately_from_refusals(capsys):
    rows = [{"plan_ready": False, "plan_reason": f"{BARE} @ a.py:1 in f"} for _ in range(3)]
    rows += [{"plan_ready": False, "plan_reason": "archetype conviction is LOW: X"}
             for _ in range(10)]
    report._report("sim", rows, CONFIG)
    out = capsys.readouterr().out
    assert "CRASHED   : 3" in out
    assert "refused   : 10" in out


def test_distinct_sites_appear_as_distinct_rows(capsys):
    rows = [{"plan_ready": False, "plan_reason": f"{BARE} @ session_planner.py:912 in _a"}
            for _ in range(3)]
    rows += [{"plan_ready": False, "plan_reason": f"{BARE} @ smc_agent.py:1148 in _b"}
             for _ in range(2)]
    rows += [{"plan_ready": False, "plan_reason": "conviction LOW"} for _ in range(9)]
    report._report("sim", rows, CONFIG)
    out = capsys.readouterr().out
    assert "session_planner.py:912" in out
    assert "smc_agent.py:1148" in out


def test_no_behaviour_setting_was_changed():
    planner = CONFIG.get("session_planner") or {}
    req = CONFIG.get("signal_requirements") or {}
    assert float(planner.get("min_primary_quality_score", 70)) == 70
    assert int(req.get("min_agents_agree", 3)) == 3
    # UPDATED 2026-08-04: agent_min_confidence 70 -> 67 by operator decision.
    # The assertion is kept, not deleted, so the shipped bar stays pinned to
    # one number -- it now pins the new one. See
    # tests/test_agent_bar_is_sixty_seven.py for the evidence behind it.
    assert float(req.get("agent_min_confidence", 67)) == 67
