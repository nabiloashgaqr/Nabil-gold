"""A revived map must earn permission again, not replay an old stamp.

A live BUY went out headed "Admission: 3 qualified agents aligned with the
mapped direction" while the same message listed Technical 92%, Price Action
79% and Multi-Timeframe 92% all opposing. The dissent veto added earlier was
present in that build and, tested against those votes, refuses them outright.

The signal escaped because it came from a *revived* snapshot.
`_revive_recent_ready_plan` checked only that the map was unexpired and had a
primary leg. Everything else -- authority CONFIRMED, an archetype scored 86%,
planner grade A+ -- was replayed from a verdict reached hours earlier, when
the agents said something different.

Reviving a thesis is not re-approving it. The map may keep its shape; the
permission to trade it has to come from the current book.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import scripts.run_analysis as ra

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

NOW = datetime(2026, 7, 29, 12, 21, tzinfo=timezone.utc)

# The exact votes printed under the live signal.
OPPOSED = {
    "technical": {"direction": "SELL", "confidence": 92},
    "classical": {"direction": "WAIT", "confidence": 30},
    "smc": {"direction": "WAIT", "confidence": 31},
    "price_action": {"direction": "SELL", "confidence": 79},
    "multitimeframe": {"direction": "SELL", "confidence": 92},
}
ALIGNED = {
    "technical": {"direction": "BUY", "confidence": 85},
    "classical": {"direction": "BUY", "confidence": 78},
    "smc": {"direction": "BUY", "confidence": 88},
    "price_action": {"direction": "WAIT", "confidence": 30},
    "multitimeframe": {"direction": "WAIT", "confidence": 40},
}


class _Database:
    def __init__(self, rows):
        self._rows = rows

    def get_recent_session_plans(self, **_kwargs):
        return self._rows


def _snapshot(**payload_overrides):
    payload = {
        "plan_ready": True,
        "session_bias": "BUY",
        "authority_state": "CONFIRMED",
        "authority_direction": "BUY",
        "day_archetype": "CONTINUATION_AFTER_SWEEP_DAY",
        "day_archetype_confidence": 86,
        "planner_grade": "A+",
        "plan_expires_at": (NOW + timedelta(hours=5)).isoformat(),
        "primary_poi": {"entry_price": 4028.77, "stop_loss": 4013.77},
    }
    payload.update(payload_overrides)
    return {"analysis_run_at": (NOW - timedelta(hours=2)).isoformat(), "payload": payload}


def _revive(details, rows=None):
    return ra._revive_recent_ready_plan(
        _Database(rows if rows is not None else [_snapshot()]),
        CONFIG,
        symbol="XAU/USD",
        now=NOW,
        base_decision={"symbol": "XAU/USD", "agent_details": details},
    )


# --- the signal that shipped ---------------------------------------------

def test_a_stale_stamp_cannot_outvote_the_live_book() -> None:
    """authority CONFIRMED + 86% archetype must not survive 3 dissenters."""
    assert _revive(OPPOSED) is None


def test_a_still_supported_map_is_revived() -> None:
    """Re-authorisation must not become a blanket refusal."""
    revived = _revive(ALIGNED)

    assert revived is not None
    assert revived["session_bias"] == "BUY"
    assert revived["revived_from_snapshot"] is True


def test_one_dissenter_is_still_tolerated() -> None:
    """The ceiling is the planner's own, not stricter."""
    details = dict(ALIGNED)
    details["multitimeframe"] = {"direction": "SELL", "confidence": 90}

    assert _revive(details) is not None


def test_two_dissenters_refuse_the_revival() -> None:
    details = dict(ALIGNED)
    details["price_action"] = {"direction": "SELL", "confidence": 88}
    details["multitimeframe"] = {"direction": "SELL", "confidence": 90}

    assert _revive(details) is None


def test_low_confidence_dissent_does_not_block_revival() -> None:
    """Only qualified agents vote, on revival as anywhere else."""
    details = dict(ALIGNED)
    details["price_action"] = {"direction": "SELL", "confidence": 35}
    details["multitimeframe"] = {"direction": "SELL", "confidence": 40}

    assert _revive(details) is not None


def test_a_sell_map_is_re_authorised_the_same_way() -> None:
    sell_snapshot = _snapshot(session_bias="SELL", authority_direction="SELL")
    buyers = {
        "technical": {"direction": "BUY", "confidence": 90},
        "classical": {"direction": "WAIT", "confidence": 30},
        "smc": {"direction": "BUY", "confidence": 85},
        "price_action": {"direction": "BUY", "confidence": 80},
        "multitimeframe": {"direction": "WAIT", "confidence": 40},
    }

    assert _revive(buyers, rows=[sell_snapshot]) is None


# --- what must not change ------------------------------------------------

def test_expired_maps_are_still_refused_before_any_vote() -> None:
    expired = _snapshot(plan_expires_at=(NOW - timedelta(minutes=1)).isoformat())

    assert _revive(ALIGNED, rows=[expired]) is None


def test_a_map_without_a_primary_leg_is_still_refused() -> None:
    assert _revive(ALIGNED, rows=[_snapshot(primary_poi={})]) is None


def test_omitting_the_decision_keeps_the_old_behaviour() -> None:
    """Callers that cannot supply live votes must not break."""
    revived = ra._revive_recent_ready_plan(
        _Database([_snapshot()]), CONFIG, symbol="XAU/USD", now=NOW
    )

    assert revived is not None


def test_the_expiry_renewal_still_applies_to_an_approved_revival() -> None:
    """The previous fix must survive this one."""
    revived = _revive(ALIGNED)

    assert revived["plan_expiry_renewed_on_revival"] is True
    assert ra._parse_datetime(revived["plan_expires_at"]) > NOW + timedelta(hours=6)


def test_the_caller_passes_live_votes() -> None:
    """Wiring guard: the ladder must hand its decision to the revival."""
    import inspect

    source = inspect.getsource(ra._execute_session_plan_ladder)
    assert "base_decision=base_decision" in source, (
        "revival is called without the live book, so it cannot re-authorise"
    )
