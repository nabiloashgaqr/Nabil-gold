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


def test_exclusions_are_counted_not_silently_dropped() -> None:
    """4 analysable out of 86 must not look like an analysis of 86."""
    trades = [_trade("A", mae=-30)]
    no_plan = _trade("B", mae=-30)
    no_plan["signal_snapshot"] = {}
    trades.append(no_plan)
    trades.append(_trade("C", mae=-30, status="PENDING"))
    trades.append({**_trade("D", mae=-30), "symbol": "EUR/USD"})

    result = analyse(trades, "XAU/USD")
    assert len(result["rows"]) == 1
    assert result["total"] == 4
    assert result["skipped"]["no_structural_stop"] == 1
    assert result["skipped"]["not_closed"] == 1
    assert result["skipped"]["other_symbol"] == 1


def test_verdict_is_withheld_below_the_minimum_sample(capsys) -> None:
    """A percentage from four trades is noise, and must not be stated as fact."""
    from scripts.analyze_sl_floor import report

    report(analyse([_trade(f"T{i}", mae=-15) for i in range(4)], "XAU/USD"))
    out = capsys.readouterr().out
    assert "NOT ENOUGH DATA" in out
    assert "dead weight" not in out
    assert "doing real work" not in out


def test_verdict_is_given_once_the_sample_is_large_enough(capsys) -> None:
    from scripts.analyze_sl_floor import report

    report(analyse([_trade(f"T{i}", mae=-15) for i in range(25)], "XAU/USD"))
    out = capsys.readouterr().out
    assert "NOT ENOUGH DATA" not in out
    assert "dead weight" in out


def test_path_split_separates_planner_from_other_entries(capsys) -> None:
    """Routing more volume to the planner requires knowing it is better."""
    from scripts.analyze_sl_floor import _print_outcomes

    # TP1_HIT is an open status, so use terminal ones here.
    planner_win = _trade("P1", mae=-30, pnl=500, status="TP2_HIT")
    planner_loss = _trade("P2", mae=-30, pnl=-100, status="SL_HIT")
    other = _trade("O1", mae=-30, pnl=200, status="SL_HIT")
    other["signal_snapshot"] = {}

    _print_outcomes([planner_win, planner_loss, other], "XAU/USD")
    out = capsys.readouterr().out
    assert "planner path" in out
    assert "other paths" in out
    assert "indicative, not conclusive" in out


def test_trailing_exits_are_counted_as_wins_not_losses(capsys) -> None:
    """SL_HIT is reused for trailing exits; only pnl distinguishes them."""
    from scripts.analyze_sl_floor import _print_outcomes

    trailing_win = _trade("A", mae=-30, pnl=250, status="SL_HIT")
    real_loss = _trade("B", mae=-30, pnl=-400, status="SL_HIT")
    _print_outcomes([trailing_win, real_loss], "XAU/USD")
    out = capsys.readouterr().out
    assert "Wins 1 · Losses 1" in out
