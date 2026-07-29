"""A finished signal must be arithmetically coherent before it is sent.

Every execution fault found in this system was individually invisible and
jointly obvious: a first target 5 points from entry against a 150-point stop;
a breakeven trigger that could never fire before that target; a stop distance
quoted from config rather than from the trade. None needed market knowledge to
catch -- only for someone to check the finished numbers against each other.

`validate_signal_before_send` is that check, at the single point where a
signal becomes real. These tests pin it against the two live signals that
shipped broken, and against the risk of it becoming a blanket refusal.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import scripts.run_analysis as ra

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def _signal(entry, stop, tp1, tp2, side="BUY"):
    return {
        "decision": side,
        "symbol": "XAU/USD",
        "current_price": entry,
        "signal": {
            "entry": {"price": entry},
            "stop_loss": stop,
            "tp1": tp1,
            "tp2": tp2,
        },
    }


def _check(decision):
    return ra.validate_signal_before_send(decision, CONFIG)


# --- the signals that actually shipped ----------------------------------

def test_the_28_july_signal_is_refused() -> None:
    """TP1 five points away against a 150-point stop."""
    violations = _check(_signal(4028.32, 4013.32, 4028.85, 4082.34))

    assert violations
    joined = " ".join(violations)
    assert "0.04R" in joined
    assert "breakeven" in joined


def test_the_29_july_plan_is_refused() -> None:
    """Same fault, next session: TP1 at 0.06R."""
    violations = _check(_signal(4029.64, 4014.64, 4030.47, 4089.64))

    assert violations
    assert any("tp1 is only" in v for v in violations)


def test_the_repaired_version_of_the_same_plan_passes() -> None:
    """The fixes must produce a signal this gate accepts."""
    assert _check(_signal(4029.64, 4014.64, 4059.64, 4089.64)) == []


# --- geometry -----------------------------------------------------------

def test_stop_on_the_wrong_side_is_refused() -> None:
    violations = _check(_signal(4030.0, 4045.0, 4060.0, 4090.0))
    assert any("not protective" in v for v in violations)


def test_target_behind_the_entry_is_refused() -> None:
    violations = _check(_signal(4030.0, 4015.0, 4020.0, 4090.0))
    assert any("not ahead" in v for v in violations)


def test_zero_risk_leg_is_refused() -> None:
    """A stop sitting on the entry is an instant exit, not a trade."""
    violations = _check(_signal(4030.0, 4030.0, 3950.0, 3900.0, side="SELL"))
    assert any("risk distance is zero" in v for v in violations)


def test_inverted_targets_are_refused() -> None:
    violations = _check(_signal(4000.0, 3985.0, 4080.0, 4020.0))
    assert any("tp2 is nearer than tp1" in v for v in violations)


def test_sell_side_geometry_is_validated_too() -> None:
    good = _check(_signal(4051.18, 4066.18, 4021.18, 3971.18, side="SELL"))
    assert good == []

    bad = _check(_signal(4051.18, 4036.18, 4021.18, 3971.18, side="SELL"))
    assert any("not protective" in v for v in bad)


# --- scope --------------------------------------------------------------

def test_a_wait_decision_is_not_validated() -> None:
    """The gate judges orders, not the absence of one."""
    assert ra.validate_signal_before_send({"decision": "WAIT"}, CONFIG) == []


def test_missing_signal_payload_is_reported() -> None:
    violations = ra.validate_signal_before_send({"decision": "BUY"}, CONFIG)
    assert violations == ["signal payload is missing"]


# --- wiring -------------------------------------------------------------

def test_validator_guards_the_direct_execution_path() -> None:
    source = inspect.getsource(ra._run_analysis_for_config)
    gate = source.find("validate_signal_before_send(")
    send = source.find("telegram.send_signal(")
    create = source.find("database.new_trade_id()")

    assert gate != -1, "the direct path can send without validation"
    assert gate < send, "validation must precede delivery"
    assert gate < create, "validation must precede trade creation"


def test_validator_guards_the_planner_ladder_path() -> None:
    source = inspect.getsource(ra._execute_session_plan_ladder)
    gate = source.find("validate_signal_before_send(")
    send = source.find("telegram.send_signal(")

    assert gate != -1, "the ladder can send without validation"
    assert gate < send, "validation must precede delivery"
