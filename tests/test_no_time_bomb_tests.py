"""No test may depend on the wall clock passing a threshold.

2026-07-31, 11:00 UTC — live analysis stopped:

    tests/test_thesis_exit_agent_vote.py
      test_scale_out_books_the_closed_half_at_its_own_price
      AssertionError: assert 'THESIS_SCALE_OUT' in
                             ['LONG_RUNNING', 'EXIT_WARNING', 'EXPIRED']
    Error: Process completed with exit code 1.

Nothing had changed. The test hardcoded ``created_at = 2026-07-30T06:31``
and called ``evaluate_trade`` without ``now=``, so the manager compared a
frozen timestamp against the real clock. Twenty-four hours later the trade
crossed ``expire_after_hours`` and the manager -- correctly -- reported
EXPIRED instead of the scale-out under test.

The test passed on the day it was written and failed the next, taking the CI
barrier and therefore live trading down with it. That is the worst kind of
failure: green when authored, red at an hour nobody chose.

Two ways to write an age safely:

    opened = datetime.now(timezone.utc) - timedelta(minutes=30)   # relative
    manager.evaluate_trade(trade, price, now=FIXED_MOMENT)        # pinned

This test enforces one or the other.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

# A frozen ISO timestamp used as the trade's OWN age. Candle timestamps are
# irrelevant here -- they never feed expire_after_hours -- so only the two
# fields the manager ages a trade by are inspected.
_FROZEN = re.compile(
    r'"(?:created_at|entry_time)"\s*:\s*"(20\d\d-\d\d-\d\dT\d\d:\d\d(?::\d\d)?)'
)
# A call to evaluate_trade, with balanced single-level parentheses.
_CALL = re.compile(r"\.evaluate_trade\((?:[^()]|\([^()]*\))*\)", re.S)
_DEF = re.compile(r"def (test_\w+)\(.*?(?=\ndef |\Z)", re.S)


def _expire_hours() -> float:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        config = json.load(fh)
    return float((config.get("trade_management") or {}).get("expire_after_hours") or 0)


def test_no_test_ages_a_trade_against_the_real_clock() -> None:
    """A frozen created_at plus an unpinned `now` is a delayed failure."""
    expire_hours = _expire_hours()
    assert expire_hours > 0, "expire_after_hours must be set for this guard to mean anything"

    now = datetime.now(timezone.utc)
    offenders: list[str] = []

    for name in sorted(os.listdir(TESTS)):
        if not name.endswith(".py"):
            continue
        # This file quotes the offending shape on purpose, to prove the
        # detector works. Scanning itself would always report a hit.
        if name == os.path.basename(__file__):
            continue
        source = open(os.path.join(TESTS, name), encoding="utf-8").read()
        for block in _DEF.finditer(source):
            body = block.group(0)
            calls = _CALL.findall(body)
            if not calls:
                continue
            # A pinned `now=` on every call makes the wall clock irrelevant.
            if all("now=" in call for call in calls):
                continue
            for stamp in _FROZEN.findall(body):
                try:
                    moment = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                age_hours = (now - moment).total_seconds() / 3600.0
                if age_hours >= expire_hours:
                    offenders.append(
                        f"{name}::{block.group(1)} — created {stamp} is "
                        f"{age_hours:.0f}h old, past the {expire_hours:.0f}h "
                        f"expiry, and evaluate_trade is called without now="
                    )
                    break

    assert not offenders, (
        "these tests will fail purely because time passed:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_would_have_caught_the_live_failure() -> None:
    """Prove the detector works, using the exact shape that broke CI."""
    expire_hours = _expire_hours()
    now = datetime.now(timezone.utc)
    sample = (
        'def test_example() -> None:\n'
        '    trade = {"created_at": "2026-07-30T06:31:00+00:00"}\n'
        '    res = manager.evaluate_trade(trade, 4049.94, candle_high=4050.2)\n'
    )
    block = _DEF.search(sample)
    assert block is not None
    calls = _CALL.findall(block.group(0))
    assert calls and not all("now=" in c for c in calls)
    stamp = _FROZEN.findall(block.group(0))[0]
    age = (now - datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)).total_seconds() / 3600.0
    assert age >= expire_hours, (
        "this sample is the 2026-07-30 trade; once it is older than the "
        "expiry window the detector must flag it"
    )


def test_a_pinned_now_is_accepted() -> None:
    sample = (
        'def test_example() -> None:\n'
        '    trade = {"created_at": "2026-07-30T06:31:00+00:00"}\n'
        '    res = manager.evaluate_trade(trade, 4049.94, now=FIXED)\n'
    )
    block = _DEF.search(sample)
    calls = _CALL.findall(block.group(0))
    assert calls and all("now=" in c for c in calls)
