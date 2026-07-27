"""Tests for the SL-floor analyser.

The floor question moves the risk on every trade, so the tool that answers it
must be trustworthy: it has to read the structural stop the planner derived
before the floor, judge it against the excursion actually recorded, and say
plainly when the data cannot settle the question.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from scripts.analyze_sl_floor import analyse


def _trade(tid: str, *, mae=None, pnl: float = 100.0, status: str = "TP1_HIT",
           structural: float = 4093.48, shipped: float = 4060.28, side: str = "BUY"):
    trade = {
        "id": tid,
        "symbol": "XAU/USD",
        "type": side,
        "status": status,
        "entry_price": 4100.28,
        "initial_stop_loss": shipped,
        "final_pnl": pnl,
        "signal_snapshot": {"session_plan": {"primary_poi": {"stop_loss": structural}}},
    }
    if mae is not None:
        trade["max_adverse_excursion"] = mae
    return trade


def test_open_and_pending_trades_are_excluded() -> None:
    rows = analyse([
        _trade("A", mae=-30),
        _trade("B", mae=-30, status="PENDING"),
        _trade("C", mae=-30, status="OPEN"),
        _trade("D", mae=-30, status="CANCELLED"),
    ], "XAU/USD")["rows"]
    assert [r["id"] for r in rows] == ["A"]


def test_risk_inflation_is_measured_against_the_structural_stop() -> None:
    row = analyse([_trade("A", mae=-30)], "XAU/USD")["rows"][0]
    assert row["shipped_risk"] == 400.0
    assert row["structural_risk"] == 68.0
    assert round(row["inflation"], 1) == 5.9
    assert row["floored"] is True


def test_shallow_excursion_means_the_extra_risk_went_unused() -> None:
    """MAE of 30 pts never reaches a 68 pt structural stop."""
    row = analyse([_trade("A", mae=-30)], "XAU/USD")["rows"][0]
    assert row["structural_hit"] is False


def test_deep_excursion_means_the_floor_kept_the_trade_alive() -> None:
    row = analyse([_trade("A", mae=-90, pnl=200)], "XAU/USD")["rows"][0]
    assert row["structural_hit"] is True
    assert row["won"] is True


def test_missing_excursion_is_reported_as_unknown_not_guessed() -> None:
    row = analyse([_trade("A")], "XAU/USD")["rows"][0]
    assert row["structural_hit"] is None


def test_trades_without_a_structural_stop_are_skipped() -> None:
    bare = _trade("A", mae=-30)
    bare["signal_snapshot"] = {}
    assert analyse([bare], "XAU/USD")["rows"] == []


def test_sell_side_uses_the_same_arithmetic() -> None:
    row = analyse([
        _trade("S", mae=-30, side="SELL", structural=4107.08, shipped=4140.28),
    ], "XAU/USD")["rows"][0]
    assert row["structural_risk"] == 68.0
    assert row["shipped_risk"] == 400.0
    assert row["structural_hit"] is False


def test_string_snapshots_are_parsed() -> None:
    import json as _json
    trade = _trade("A", mae=-30)
    trade["signal_snapshot"] = _json.dumps(trade["signal_snapshot"])
    assert len(analyse([trade], "XAU/USD")["rows"]) == 1
