"""Every analyst label must be capable of being scored against.

WHY THIS EXISTS
---------------
``AnalystScoreboardService._score_analyst`` reads ``invalidation`` through
``_f()``, and ``_resolve_plan_outcome`` only enforces a stop when
``stop > 0``. So a label whose invalidation is written as prose --
``"Close above 4098.29"`` -- parses to ``0.0`` and the analyst can be scored
as a winner or as unfilled, but **never as stopped out**.

That is not a cosmetic defect. The scoreboard exists to measure our system
against a human analyst, and a stop that cannot fire flatters the side being
measured against. The docstring of ``_resolve_plan_outcome`` states the
principle directly: "The scoreboard must never flatter the side it is
measuring against."

The 2026-08-03 label was written that way first, caught here, and corrected
to the bare number before it was ever scored.

THE 2026-07-30 LABEL IS GRANDFATHERED
-------------------------------------
It carries ``"Close below 4057.36"`` and is already committed. Rewriting it
now could change a verdict that has already been reported, so it is listed
as a known exception rather than silently edited. New labels must be numeric.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE = os.path.join(ROOT, "storage")

#: Labels written before this rule existed. Do not add to this list -- fix the
#: label instead. Each entry is a verdict that may already have been reported.
GRANDFATHERED = {"LABEL_20260730_001"}

_NUMERIC_FIELDS = ("intended_entry", "invalidation", "tp1", "tp2")


def _labels() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(glob.glob(os.path.join(STORAGE, "*_analyst_label.csv"))):
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                row["_source"] = os.path.basename(path)
                rows.append(row)
    for path in sorted(glob.glob(os.path.join(STORAGE, "analyst_labels_*.json"))):
        with open(path, encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except (ValueError, TypeError):
                continue
        for row in data if isinstance(data, list) else []:
            if isinstance(row, dict):
                row = dict(row)
                row["_source"] = os.path.basename(path)
                rows.append(row)
    return rows


def _is_number(value) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def test_there_is_at_least_one_label_to_check() -> None:
    """A silent pass because nothing was found is not a pass."""
    assert _labels(), "no analyst labels found in storage/"


def test_every_numeric_field_is_actually_numeric() -> None:
    offenders: list[str] = []
    for label in _labels():
        label_id = str(label.get("id") or "?")
        if label_id in GRANDFATHERED:
            continue
        for field in _NUMERIC_FIELDS:
            value = label.get(field)
            if value in (None, ""):
                continue
            if not _is_number(value):
                offenders.append(
                    f"{label['_source']}::{label_id}.{field} = {value!r} "
                    "parses to 0.0, so the scoreboard cannot enforce it"
                )
    assert not offenders, "unscoreable label fields:\n  " + "\n  ".join(offenders)


def test_a_stop_that_parses_to_zero_can_never_stop_the_analyst_out() -> None:
    """Prove the consequence, so the rule above is not taken on trust."""
    from services.analyst_scoreboard import AnalystScoreboardService
    from utils.helpers import load_config

    service = AnalystScoreboardService(load_config())
    assert service._f("Close above 4098.29", 0.0) == 0.0

    # A SELL entered at 4080 with a prose stop, on a day that rallied to 4105:
    # the real invalidation (4098.29) was breached, yet nothing fires.
    outcome, _ = service._resolve_plan_outcome(
        side="SELL", entry=4080.0, stop=0.0, target=4020.0, high=4105.0, low=4030.0
    )
    assert outcome != "STOPPED", (
        "with a zero stop the analyst survives a move straight through his own "
        "invalidation -- this is why the field must be numeric"
    )

    # The same day, scored honestly.
    outcome, _ = service._resolve_plan_outcome(
        side="SELL", entry=4080.0, stop=4098.29, target=4020.0, high=4105.0, low=4030.0
    )
    assert outcome == "STOPPED"


def test_todays_label_is_scoreable_in_all_three_outcomes() -> None:
    """The 2026-08-03 label must be able to win, lose, or not trigger."""
    from services.analyst_scoreboard import AnalystScoreboardService
    from utils.helpers import load_config

    path = os.path.join(STORAGE, "2026-08-03_analyst_label.csv")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        labels = list(csv.DictReader(fh))

    service = AnalystScoreboardService(load_config())
    outcomes = {
        service._score_analyst(labels, hi, lo, "XAU/USD")["detail"][0]["outcome"]
        for hi, lo in ((4085.0, 4015.0), (4105.0, 4030.0), (4070.0, 4030.0))
    }
    assert outcomes == {"TARGET", "STOPPED", "NOT_TRIGGERED"}


def test_the_analyst_records_are_not_ignored_by_git() -> None:
    """`storage/` hid every label from the repository until this was fixed.

    Git does not descend into an excluded directory, so `!storage/...`
    exceptions under a bare `storage/` rule are never evaluated. The rule has
    to be `storage/*` for the exceptions to be read at all.
    """
    path = os.path.join(ROOT, ".gitignore")
    if not os.path.exists(path):
        return
    text = open(path, encoding="utf-8").read()

    assert "!storage/*_analyst_label.csv" in text
    assert "!storage/*_head_to_head.md" in text
    lines = [ln.strip() for ln in text.splitlines()]
    assert "storage/*" in lines, "the exceptions require the `storage/*` form"
    assert "storage/" not in lines, (
        "a bare `storage/` rule silently defeats every exception below it"
    )


def test_runtime_state_is_still_ignored() -> None:
    """The exception must be narrow: machine-written state stays out."""
    path = os.path.join(ROOT, ".gitignore")
    if not os.path.exists(path):
        return
    text = open(path, encoding="utf-8").read()
    for runtime in ("session_plans.json", "decision_audit.json", "trades.json"):
        assert f"!storage/{runtime}" not in text, (
            f"{runtime} is machine-written and must never be committed"
        )
