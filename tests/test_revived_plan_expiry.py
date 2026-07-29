"""An order must be judged by its own age, not its source map's.

A pending BUY created at 07:41 was cancelled at 10:14 as "session plan
expired" -- after 2.5 hours, against an order staleness window of 6. Half an
hour later the planner published the same area again, 4 points away. To the
operator that reads as the system arguing with itself.

The cause: the order was built from a *revived* map. `_revive_recent_ready_plan`
reuses a still-valid snapshot when the current cycle cannot rebuild one, and it
copied `plan_expires_at` verbatim. A map built at 02:00 expires at 10:00, so an
order derived from it at 07:41 was already 71% through its life at birth.

Reviving the thesis is correct. Inheriting its deadline is not.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.run_analysis as ra

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

NOW = datetime(2026, 7, 29, 7, 41, tzinfo=timezone.utc)


class _Database:
    def __init__(self, rows):
        self._rows = rows

    def get_recent_session_plans(self, **_kwargs):
        return self._rows


def _snapshot(*, built_at: datetime, expires_at: datetime, ready: bool = True):
    return {
        "analysis_run_at": built_at.replace(microsecond=0).isoformat(),
        "payload": {
            "plan_ready": ready,
            "session_bias": "BUY",
            "plan_expires_at": expires_at.replace(microsecond=0).isoformat(),
            "primary_poi": {"entry_price": 4028.32, "stop_loss": 4013.32},
        },
    }


def _revive(rows, now=NOW):
    return ra._revive_recent_ready_plan(_Database(rows), CONFIG, symbol="XAU/USD", now=now)


# --- the live failure ----------------------------------------------------

def test_a_revived_map_does_not_hand_down_a_spent_deadline() -> None:
    """The 02:00 map that killed a 07:41 order at 10:14."""
    built = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    revived = _revive([_snapshot(built_at=built, expires_at=built + timedelta(hours=8))])

    assert revived is not None
    expiry = ra._parse_datetime(revived["plan_expires_at"])
    assert expiry > NOW + timedelta(hours=6), (
        "the revived map expires before the order's own 6h staleness window, "
        "so the order would be cancelled while still fresh"
    )


def test_the_order_outlives_the_moment_that_cancelled_it() -> None:
    """10:14 must no longer be past the deadline."""
    built = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    revived = _revive([_snapshot(built_at=built, expires_at=built + timedelta(hours=8))])

    cancelled_at = datetime(2026, 7, 29, 10, 14, tzinfo=timezone.utc)
    assert ra._parse_datetime(revived["plan_expires_at"]) > cancelled_at


def test_the_renewal_is_recorded_not_hidden() -> None:
    """An audit trail must show the deadline moved, and from what."""
    built = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    original = built + timedelta(hours=8)
    revived = _revive([_snapshot(built_at=built, expires_at=original)])

    assert revived["plan_expiry_renewed_on_revival"] is True
    assert revived["original_plan_expires_at"] == original.replace(microsecond=0).isoformat()
    assert revived["revived_from_snapshot"] is True


def test_the_renewed_window_matches_the_configured_lifetime() -> None:
    built = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    revived = _revive([_snapshot(built_at=built, expires_at=built + timedelta(hours=8))])

    hours = float(CONFIG["session_planner"]["expire_after_hours"])
    expected = NOW + timedelta(hours=hours)
    actual = ra._parse_datetime(revived["plan_expires_at"])
    assert abs((actual - expected).total_seconds()) < 60


# --- what must NOT change ------------------------------------------------

def test_an_already_expired_map_is_still_refused() -> None:
    """Renewal on revival must not resurrect dead maps."""
    built = datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc)
    expired = _snapshot(built_at=built, expires_at=built + timedelta(hours=2))  # died 03:00

    assert _revive([expired]) is None


def test_a_map_that_was_never_ready_is_refused() -> None:
    built = datetime(2026, 7, 29, 6, 0, tzinfo=timezone.utc)
    rows = [_snapshot(built_at=built, expires_at=built + timedelta(hours=8), ready=False)]

    assert _revive(rows) is None


def test_a_map_without_a_primary_leg_is_refused() -> None:
    built = datetime(2026, 7, 29, 6, 0, tzinfo=timezone.utc)
    row = _snapshot(built_at=built, expires_at=built + timedelta(hours=8))
    row["payload"]["primary_poi"] = {}

    assert _revive([row]) is None


def test_the_thesis_itself_is_preserved() -> None:
    """Only the deadline is refreshed; the map keeps its identity and age."""
    built = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    revived = _revive([_snapshot(built_at=built, expires_at=built + timedelta(hours=8))])

    assert revived["session_bias"] == "BUY"
    assert revived["primary_poi"]["entry_price"] == 4028.32
    assert revived["revived_age_minutes"] == pytest.approx(341.0, abs=1.0)
