"""A leg execution refuses must never be published as a ready map.

`_execution_levels` delegates pricing to the execution module and carries back
its verdict in `reject_reason`. The planner's quality guard never read it. The
plan was still stopped -- but only because a rejected leg happens to return
rr = 0, which trips the RR check by coincidence.

Two problems follow. A rejection that returned any non-zero ratio would sail
through and publish a READY map for a leg execution had already refused. And
the operator was told "main area RR 0.00 below 1.50" when the real reason was
that no qualifying liquidity existed at all.
"""

from __future__ import annotations

import pytest

import json
from pathlib import Path

from services.session_planner import SessionPlannerService

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def _service(**planner_overrides):
    config = {"symbol": "XAU/USD", "risk_settings": CONFIG["risk_settings"],
              "session_planner": {"enabled": True, **planner_overrides}}
    return SessionPlannerService(config)


def _guard(service, levels, direction="BUY"):
    return service._plan_quality_guard(
        direction=direction, primary={}, standby=None,
        primary_execution=levels, all_results={}, symbol="XAU/USD",
    )


def test_rejected_leg_is_refused_by_name_not_by_accident() -> None:
    """The live 2026-07-29 levels: no qualifying liquidity ahead."""
    service = _service()
    levels = service._execution_levels(
        direction="BUY", entry_price=4028.32, stop_loss=4027.0,
        target_price=4028.85, symbol="XAU/USD", candidate={},
    )
    assert levels["reject_reason"], "fixture no longer reproduces a rejection"

    ok, reason, diagnostics = _guard(service, levels)

    assert ok is False
    assert "execution refused the main leg" in reason
    assert "usable liquidity" in reason
    assert diagnostics["execution_reject_reason"]


def test_rejection_is_honoured_even_when_the_ratio_looks_acceptable() -> None:
    """The coincidence that used to do the work is removed.

    A rejected leg that still reported a healthy ratio would previously pass
    the guard outright, because only `main_rr` was consulted.
    """
    service = _service(min_main_rr_for_ready=1.5)
    levels = {
        "entry_price": 4028.32,
        "stop_loss": 4013.32,
        "tp1": 4050.0,
        "tp2": 4080.0,
        "rr_ratio": 3.6,                       # comfortably above the floor
        "reject_reason": "no qualifying liquidity ahead of entry",
    }

    ok, reason, _ = _guard(service, levels)

    assert ok is False, "a refused leg must not be admitted on its ratio alone"
    assert "execution refused the main leg" in reason


def test_a_priceable_leg_still_passes() -> None:
    """The guard must not turn into a blanket refusal."""
    service = _service()
    levels = service._execution_levels(
        direction="BUY", entry_price=4000.0, stop_loss=3990.0,
        target_price=4060.0, symbol="XAU/USD",
        candidate={"details": {"liquidity": {"buy_side": [4015.0, 4060.0]}}},
    )

    assert not levels["reject_reason"]
    ok, reason, _ = _guard(service, levels)
    assert ok is True
    assert reason is None


def test_planner_rr_gate_still_applies_to_an_accepted_leg() -> None:
    """Execution accepting a leg does not bypass the planner's own bar."""
    service = _service(min_main_rr_for_ready=2.5)
    levels = service._execution_levels(
        direction="SELL", entry_price=4051.18, stop_loss=4066.18,
        target_price=3991.18, symbol="XAU/USD",
        candidate={"details": {"liquidity": {"sell_side": [4021.18, 3991.18],
                                             "buy_side": [4081.18]}}},
    )

    assert not levels["reject_reason"]
    assert levels["rr_ratio"] == pytest.approx(1.62, abs=0.01)

    ok, reason, _ = _guard(service, levels, direction="SELL")
    assert ok is False
    assert "RR 1.62 below 2.50" in reason


def test_the_reason_names_the_real_cause() -> None:
    """Operators were shown a ratio complaint for a liquidity problem."""
    service = _service()
    levels = service._execution_levels(
        direction="BUY", entry_price=4028.32, stop_loss=4027.0,
        target_price=4028.85, symbol="XAU/USD", candidate={},
    )

    _, reason, _ = _guard(service, levels)

    assert "RR 0.00" not in reason, "the old message blamed the wrong thing"
