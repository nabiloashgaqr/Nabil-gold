"""Tests for the standalone agent-accuracy analyser.

Letting one agent admit trades by itself changes who controls risk, so the
measurement behind that decision has to be trustworthy and has to refuse a
verdict when the evidence is thin.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from scripts.analyze_agent_accuracy import collect, report


def _trade(pnl: float, side: str = "SELL", *, smc: str | None = None,
           smc_conf: float = 88.0, status: str = "SL_HIT"):
    details = {}
    if smc:
        details["smc"] = {"direction": smc, "confidence": smc_conf}
    return {
        "status": status,
        "type": side,
        "final_pnl": pnl,
        "signal_snapshot": {"agent_details": details},
    }


def test_agreement_with_a_winning_trade_counts_as_correct() -> None:
    result = collect([_trade(100.0, "SELL", smc="SELL")], 68.0)
    smc = result["stats"]["smc"]
    assert smc["n"] == 1 and smc["right"] == 1
    assert smc["pnl"] == 100.0


def test_opposing_a_losing_trade_also_counts_as_correct() -> None:
    """Arguing against a trade that lost is a correct read, not a wrong one."""
    result = collect([_trade(-100.0, "SELL", smc="BUY")], 68.0)
    smc = result["stats"]["smc"]
    assert smc["right"] == 1
    assert smc["pnl"] == 100.0, "avoiding the loss is worth the points saved"


def test_opinions_below_the_confidence_bar_are_ignored() -> None:
    result = collect([_trade(100.0, "SELL", smc="SELL", smc_conf=50.0)], 68.0)
    assert "smc" not in result["stats"]


def test_open_and_cancelled_trades_are_excluded() -> None:
    trades = [
        _trade(100.0, "SELL", smc="SELL", status="PENDING"),
        _trade(100.0, "SELL", smc="SELL", status="CANCELLED"),
        _trade(100.0, "SELL", smc="SELL", status="TP2_HIT"),
    ]
    assert collect(trades, 68.0)["closed"] == 1


def test_trades_without_agent_details_are_counted_but_not_scored() -> None:
    result = collect([_trade(100.0, "SELL")], 68.0)
    assert result["closed"] == 1
    assert result["without_details"] == 1
    assert result["stats"] == {}


def test_verdict_is_withheld_below_the_minimum_sample(capsys) -> None:
    trades = [_trade(100.0, "SELL", smc="SELL") for _ in range(10)]
    report(collect(trades, 68.0), 68.0)
    out = capsys.readouterr().out
    assert "Not enough evidence" in out
    assert "supports giving it a dedicated solo admission path" not in out


def test_strong_record_on_a_sufficient_sample_supports_solo_entry(capsys) -> None:
    trades = [_trade(100.0, "SELL", smc="SELL") for _ in range(22)]
    report(collect(trades, 68.0), 68.0)
    out = capsys.readouterr().out
    assert "supports giving it a dedicated solo admission path" in out


def test_weak_record_does_not_support_solo_entry(capsys) -> None:
    trades = [_trade(100.0, "SELL", smc="SELL") for _ in range(11)]
    trades += [_trade(-100.0, "SELL", smc="SELL") for _ in range(11)]
    report(collect(trades, 68.0), 68.0)
    out = capsys.readouterr().out
    assert "not justified by this data" in out
