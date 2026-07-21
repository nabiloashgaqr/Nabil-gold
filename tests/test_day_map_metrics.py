from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.day_map_metrics import summarize_day_map_execution


def _trade(trade_id: str, *, role: str, status: str, pnl: float, scenario_id: str = "SCENARIO::A") -> dict:
    return {
        "id": trade_id,
        "symbol": "XAU/USD",
        "type": "SELL",
        "status": status,
        "final_pnl": pnl,
        "signal_snapshot": {
            "session_plan": {"scenario_id": scenario_id},
            "setup_context": {
                "scenario_id": scenario_id,
                "pending_plan_role": role,
                "selection_role": role,
                "execution_leg_label": role,
            },
        },
    }


def test_day_map_metrics_can_count_main_add_and_failures() -> None:
    trades = [
        _trade("T1", role="PRIMARY", status="TP2_HIT", pnl=550.0, scenario_id="SCENARIO::MAIN_WORKED"),
        _trade("T2", role="STARTER", status="SL_HIT", pnl=230.0, scenario_id="SCENARIO::STARTER_ALONE"),
        _trade("T3", role="PRIMARY", status="SL_HIT", pnl=-300.0, scenario_id="SCENARIO::FAILED"),
        _trade("T4", role="STANDBY", status="TP2_HIT", pnl=420.0, scenario_id="SCENARIO::ADD_NEEDED"),
        _trade("T5", role="PRIMARY", status="TP1_HIT", pnl=120.0, scenario_id="SCENARIO::ADD_NEEDED"),
    ]
    summary = summarize_day_map_execution(trades)
    metrics = summary["scenario_metrics"]
    assert summary["tracked_trade_count"] == 5
    assert summary["scenario_count"] == 4
    assert metrics["main_worked_count"] == 3
    assert metrics["add_needed_count"] == 1
    assert metrics["starter_survived_alone_count"] == 1
    assert metrics["day_map_failed_count"] == 1
