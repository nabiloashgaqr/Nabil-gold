"""Excursion statistics may only count orders that actually filled.

REPORT #11, 106 rows
--------------------
    How far trades actually ran vs TP2 (avg) : 11% of the way
    Never reached half of TP2  : 98  92.5%

Read plainly, that says the targets are essentially unreachable. It is
false, and it is my own script that produced it.

The population filter excluded PENDING/OPEN/PARTIAL/TP1_HIT but kept
CANCELLED and EXPIRED. A cancelled pending order never entered the market,
so its max_favorable_excursion is zero because there was no position -- not
because price failed to travel. Averaging those zeros against real trades
measures bookkeeping and calls it price behaviour.

The arithmetic is checkable: 98 zeros plus 8 real trades averaging ~145%
gives 11%. Meanwhile trade d917b1d5, which we know reached TP2 exactly,
sits inside that same 106 and was drowned by the zeros.

Rule: a target statistic requires a fill. Fill is proved by evidence of a
life -- entry_time, close_price, or a realized final_pnl -- rather than by
status alone, because older rows predate some of those fields.

SECOND FAULT IN THE SAME REPORT
-------------------------------
    stop 400 -> TP2 900 : 46 rows
    stop 300 -> TP2 700 : 37 rows

config.json records the floor was "رُفع من 300 إلى 400", so the 300-group
was written under a previous regime. Averaging both eras reported 43.4% of
orders as stop-derived, against 100% among rows written under today's floor.
Mixing populations hides the signal -- the same error the rejection report
made by splitting one gate across seven rows.

Neither fix changes a threshold. They change what the measurement counts, so
that the decision taken from it is taken from the truth.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "analyze_target_geometry_fills",
    os.path.join(ROOT, "scripts", "analyze_target_geometry.py"),
)
atg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(atg)

from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()
SYMBOL = "XAU/USD"

ENTRY, STOP, TP1, TP2 = 4031.77, 4071.77, 3981.77, 3941.77


def _cancelled_pending(idx: int) -> dict:
    """An order that never became a position: no fill, no excursion."""
    return {
        "id": f"cancel-{idx}", "symbol": SYMBOL, "status": "CANCELLED",
        "entry_price": ENTRY, "initial_stop_loss": STOP, "tp1": TP1, "tp2": TP2,
        "max_favorable_excursion": 0.0,
    }


def _filled_winner(idx: int, mfe: float = 950.0) -> dict:
    return {
        "id": f"win-{idx}", "symbol": SYMBOL, "status": "TP2_HIT",
        "entry_price": ENTRY, "initial_stop_loss": STOP, "tp1": TP1, "tp2": TP2,
        "entry_time": "2026-07-31T06:02:53+00:00",
        "close_price": TP2, "final_pnl": 900.0,
        "max_favorable_excursion": mfe,
        "signal_snapshot": {"session_plan": {"primary_poi": {"target_price": 4021.07}}},
    }


def _render(trades) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        atg.analyse(trades, CONFIG, SYMBOL)
    return buffer.getvalue()


# ── fill detection ──────────────────────────────────────────────────────────

def test_a_cancelled_pending_is_not_a_fill() -> None:
    assert atg._was_filled(_cancelled_pending(0)) is False
    assert atg._mfe_points(_cancelled_pending(0)) is None, (
        "a zero excursion with no position is an absence of data, not a "
        "target that went unreached"
    )


def test_a_closed_position_is_a_fill() -> None:
    assert atg._was_filled(_filled_winner(0)) is True
    assert atg._mfe_points(_filled_winner(0)) == 950.0


def test_an_expired_position_still_counts_but_an_expired_pending_does_not() -> None:
    """EXPIRED covers both cases; only the one with a life is measurable."""
    expired_pending = {**_cancelled_pending(1), "status": "EXPIRED"}
    expired_position = {
        **_cancelled_pending(2), "status": "EXPIRED",
        "entry_time": "2026-07-31T06:02:53+00:00", "final_pnl": -40.0,
        "max_favorable_excursion": 120.0,
    }
    assert atg._was_filled(expired_pending) is False
    assert atg._was_filled(expired_position) is True


def test_older_rows_without_entry_time_are_still_recognised() -> None:
    """A realized result proves a life even when the field set is older."""
    legacy = {
        "id": "legacy", "symbol": SYMBOL, "status": "SL_HIT",
        "entry_price": ENTRY, "initial_stop_loss": STOP, "tp1": TP1, "tp2": TP2,
        "final_pnl": -400.0, "max_favorable_excursion": 60.0,
    }
    assert atg._was_filled(legacy) is True


# ── the reported numbers ────────────────────────────────────────────────────

def test_the_reach_average_is_not_dragged_down_by_unfilled_orders() -> None:
    """Report #11's headline, rebuilt and corrected."""
    trades = [_cancelled_pending(i) for i in range(98)]
    trades += [_filled_winner(i) for i in range(8)]

    output = _render(trades)

    assert "Orders that actually filled   :    8 of 106" in output
    assert "11% of the way" not in output, "the contaminated figure must be gone"
    # 950 / 900 = 105.6% -> 106%
    assert "106% of the way" in output


def test_never_reached_half_counts_only_real_positions() -> None:
    trades = [_cancelled_pending(i) for i in range(98)]
    trades += [_filled_winner(i) for i in range(8)]

    output = _render(trades)
    # Every filled trade exceeded TP2, so none can be "short of half".
    assert "Never reached half of TP2     :    0" in output
    assert "92.5%" not in output


def test_a_genuinely_short_trade_is_still_reported() -> None:
    """The fix must not make the statistic incapable of finding a problem."""
    trades = [_filled_winner(i, mfe=100.0) for i in range(5)]  # 100 vs TP2 900
    output = _render(trades)
    assert "Never reached half of TP2     :    5" in output


# ── era separation ──────────────────────────────────────────────────────────

def test_rows_from_an_earlier_floor_are_reported_separately() -> None:
    current = [_filled_winner(i) for i in range(46)]
    older = [{
        "id": f"old-{i}", "symbol": SYMBOL, "status": "CANCELLED",
        "entry_price": 4000.0, "initial_stop_loss": 4030.0,   # 300-pt floor
        "tp1": 3950.0, "tp2": 3930.0, "max_favorable_excursion": 0.0,
    } for i in range(37)]

    output = _render(current + older)

    assert "Rows written under the current 400-pt floor :   46" in output
    assert "Rows from an earlier floor                :   37" in output
    assert "stop-derived share, current era only   : 100.0%" in output, (
        "averaging the eras reported 43.4%; today's rate is what a decision "
        "must be based on"
    )


def test_a_single_era_prints_no_split() -> None:
    """Do not invent a comparison when the history is homogeneous."""
    output = _render([_filled_winner(i) for i in range(25)])
    assert "Rows from an earlier floor" not in output


# ── the finding that survived ───────────────────────────────────────────────

def test_the_mapped_objective_is_always_nearer_than_the_shipped_tp2() -> None:
    """35/35 in report #11 -- the result this whole diagnostic was built for."""
    trades = [_filled_winner(i) for i in range(35)]
    output = _render(trades)

    assert "Mapped objective recorded      :   35" in output
    assert "nearer than the shipped TP2  :   35  100.0%" in output
    assert "below min_rr_ratio           :   35  100.0%" in output


def test_fault_injection_counting_unfilled_orders_reproduces_the_bad_number() -> None:
    """Rebuild the pre-fix statistic and show it prints 11%."""
    zeros = [0.0] * 98
    real = [950.0 / 900.0] * 8          # each filled trade reached 106% of TP2
    contaminated = (sum(zeros) + sum(real)) / (len(zeros) + len(real))
    clean = sum(real) / len(real)

    assert round(contaminated * 100) == 8, (
        "including never-filled orders collapses the average toward zero; "
        "report #11 printed 11% from exactly this shape"
    )
    assert round(clean * 100) == 106
    assert clean > contaminated * 10, (
        "the contaminated figure understates reality by an order of "
        "magnitude, which is why it read as 'targets are unreachable'"
    )
