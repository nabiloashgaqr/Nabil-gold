"""The floor measurement must cover the path most trades actually take.

WHY THIS EXISTS
---------------
``analyze_sl_floor`` answers the one question that decides whether the noise
floor earns its keep: *would the structural stop have survived?* A structural
stop that survives means the extra distance was risked and never used. One
that would have been breached means the floor kept a winner alive.

It read the pre-floor stop from ``signal_snapshot.session_plan.primary_poi``.
Only the planner path writes that key. Trades from the consensus and
dual-agent routes have no ``session_plan``, so every one of them landed in
``skipped["no_structural_stop"]`` and disappeared from the sample.

That is not a rounding error. The dual-agent route is where 36e5cc8a came
from -- the exact trade that prompted the question -- and excluding it would
have produced a verdict about the minority path while reading as a verdict
about the system.

THE FIX
-------
The consensus path now records ``risk_geometry`` on the decision it saves:
the structural distance in points, the shipped distance, the floor that was
applied, and the target method. ``_structural_stop`` falls back to it and
converts points back to a price on the same side as the shipped stop.

WHAT THIS IS NOT
----------------
No risk setting is changed and no trade behaviour changes. This is
instrumentation: it makes an existing measurement honest before it is used to
justify anything.

FAULT INJECTION
---------------
Remove the ``risk_geometry`` fallback from ``_structural_stop`` and
``test_a_consensus_trade_is_measured`` fails with the trade counted under
``no_structural_stop``.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "analyze_sl_floor_under_test", os.path.join(ROOT, "scripts", "analyze_sl_floor.py")
)
slf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(slf)

SYMBOL = "XAU/USD"
# The live 16:41 card.
ENTRY = 4037.09
SHIPPED_STOP = 4073.51
STRUCTURAL_POINTS = 121.4


def _consensus_trade(**over) -> dict:
    trade = {
        "id": "TRADE_20260803_164102_847471_36e5cc8a",
        "symbol": SYMBOL, "status": "SL_HIT", "type": "SELL",
        "entry_price": ENTRY,
        "stop_loss": SHIPPED_STOP, "initial_stop_loss": SHIPPED_STOP,
        "final_pnl_points": -364.0,
        "max_adverse_excursion": -364.0,
        "closed_at": "2026-08-03T16:41:00+00:00",
        "signal_snapshot": {
            "decision": "SELL", "entry_path": 2,
            "entry_mode": "two_agent_macro",
            "risk_geometry": {
                "structural_sl_points": STRUCTURAL_POINTS,
                "shipped_sl_points": 364.2,
                "floor_points": 364.2,
                "target_method": "rr_from_floored_sl",
            },
        },
    }
    trade.update(over)
    return trade


def _planner_trade() -> dict:
    trade = _consensus_trade(id="PLANNER_1")
    trade["signal_snapshot"] = {
        "session_plan": {"primary_poi": {"stop_loss": 4047.46}}
    }
    return trade


# ── the defect ──────────────────────────────────────────────────────────────

def test_a_consensus_trade_is_measured():
    """A dual-agent trade must not be silently excluded."""
    out = slf.analyse([_consensus_trade()], SYMBOL)
    assert out["skipped"]["no_structural_stop"] == 0, (
        "the consensus path has no session_plan, so it was dropped from the "
        "sample that decides the floor question"
    )
    assert len(out["rows"]) == 1


def test_both_paths_appear_in_one_sample():
    out = slf.analyse([_consensus_trade(), _planner_trade()], SYMBOL)
    assert len(out["rows"]) == 2
    assert out["skipped"]["no_structural_stop"] == 0


def test_the_structural_distance_is_recovered_exactly():
    row = slf.analyse([_consensus_trade()], SYMBOL)["rows"][0]
    assert row["structural_risk"] == pytest.approx(STRUCTURAL_POINTS, abs=0.5)
    assert row["shipped_risk"] == pytest.approx(364.2, abs=0.5)
    assert row["inflation"] == pytest.approx(3.0, abs=0.05)


def test_the_recovered_stop_sits_on_the_correct_side():
    """A SELL stop must be ABOVE entry; a BUY stop below it."""
    sell = slf._structural_stop(_consensus_trade())
    assert sell > ENTRY, f"SELL structural stop {sell} is not above entry"

    buy = _consensus_trade(
        type="BUY", stop_loss=ENTRY - 36.42, initial_stop_loss=ENTRY - 36.42
    )
    assert slf._structural_stop(buy) < ENTRY, "BUY structural stop is not below entry"


# ── the measurement must stay honest ────────────────────────────────────────

def test_a_trade_with_no_geometry_is_still_excluded_not_guessed():
    """Missing data must be reported as missing, never invented."""
    trade = _consensus_trade()
    trade["signal_snapshot"] = {"decision": "SELL"}
    out = slf.analyse([trade], SYMBOL)
    assert out["rows"] == []
    assert out["skipped"]["no_structural_stop"] == 1


def test_the_planner_plan_still_wins_when_both_exist():
    """The planner's own stop is the more direct record; prefer it."""
    trade = _consensus_trade()
    trade["signal_snapshot"]["session_plan"] = {
        "primary_poi": {"stop_loss": 4047.46}
    }
    assert slf._structural_stop(trade) == pytest.approx(4047.46, abs=0.01)


def test_zero_or_negative_points_are_rejected():
    for bad in (0, -50, None, "abc"):
        trade = _consensus_trade()
        trade["signal_snapshot"]["risk_geometry"]["structural_sl_points"] = bad
        assert slf._structural_stop(trade) == 0.0


def test_the_verdict_threshold_is_unchanged():
    """A verdict still requires a real sample."""
    assert slf.MIN_SAMPLE_FOR_VERDICT >= 20


def test_the_consensus_path_records_the_geometry():
    """Source guard: run_analysis must persist what the tool now reads."""
    source = open(
        os.path.join(ROOT, "scripts", "run_analysis.py"), encoding="utf-8"
    ).read()
    assert '"risk_geometry"' in source, (
        "the consensus path does not record the pre-floor stop, so its trades "
        "cannot be included in the floor measurement"
    )
    assert '"structural_sl_points": _rm.get("structural_sl_points")' in source
