"""Staleness excursion must count only movement AWAY from activation.

Operator audit 2026-08-07 (card 17:02): a BUY LIMIT born 368 pts below the
market was cancelled with "market moved 256 pts without fill" while the
market had in fact moved TOWARD the entry (the setup working). The old
absolute-value drift treated approach as excursion. Now:

  * price falling toward a BUY entry  -> drift 0, never stale-by-excursion;
  * price running away above creation -> adverse drift, stale at the threshold;
  * the reason names the creation price baseline explicitly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.open_trades_manager import OpenTradesManager  # noqa: E402
from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()
NOW = datetime(2026, 8, 7, 17, 0, tzinfo=timezone.utc)


def _pending(current_at_creation: float) -> dict:
    return {
        "id": "TRADE_STALE_DIR",
        "symbol": "XAU/USD",
        "type": "BUY",
        "order_type": "BUY_LIMIT",
        "status": "PENDING",
        "entry_price": 4319.46,
        "stop_loss": 4279.46,
        "tp1": 4361.24,
        "tp2": 4403.02,
        "created_at": (NOW - timedelta(hours=1)).isoformat(),
        "entry_time": (NOW - timedelta(hours=1)).isoformat(),
        "signal_snapshot": {
            "pending_runtime": {"creation_price": current_at_creation},
            "setup_context": {"pending_plan_role": "PRIMARY",
                              "scenario_id": "SCENARIO::STALE::TEST"},
        },
    }


def _evaluate(trade, current):
    mgr = OpenTradesManager(CONFIG)
    return mgr.evaluate_trade(
        trade, current, NOW,
        candle_high=current + 1.0, candle_low=current - 1.0,
        recent_candles=[{"time": (NOW - timedelta(minutes=5)).isoformat(),
                         "open": current, "high": current + 1.0,
                         "low": current - 1.0, "close": current}],
        market_data_source="twelvedata",
        candle_time=(NOW - timedelta(minutes=1)).isoformat(),
    )


def test_approach_toward_entry_is_not_excursion() -> None:
    """Born at 4356.21; market now 4330 (toward the 4319.46 entry)."""
    result = _evaluate(_pending(4356.21), 4330.0)
    assert result.get("new_status") != "CANCELLED", (
        f"approach toward entry must not cancel; got "
        f"{result.get('new_status')} / {result.get('reasons')}"
    )


def test_running_away_from_creation_is_stale_with_baseline() -> None:
    """Born at 4356.21; market now 4390 (338 pts away above creation)."""
    result = _evaluate(_pending(4356.21), 4390.0)
    assert result.get("new_status") == "CANCELLED"
    text = " ".join(str(r) for r in ((result.get("updates") or {}).get("reasons") or []))
    assert "from the 4356.21 creation price without fill" in text
    assert "born 368 pts from entry" in text


def test_wrong_side_target_liquidity_never_reaches_the_card() -> None:
    """Operator audit 2026-08-07: BUY target liquidity 4326.71 < entry 4327.57
    must be dropped at the card boundary."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_analysis_tl", ROOT / "scripts" / "run_analysis.py")
    ra = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ra)
    decision = {"decision": "BUY", "current_price": 4327.57}
    all_results = {"smc": {"setup_candidates": [
        {"id": "C1", "direction": "BUY", "target_liquidity": 4326.71,
         "quality_score": 60.0}]}}
    payload = ra._setup_context_payload(decision, all_results)
    assert "target_liquidity" not in payload

    all_results["smc"]["setup_candidates"][0]["target_liquidity"] = 4400.0
    payload = ra._setup_context_payload(decision, all_results)
    assert payload.get("target_liquidity") == 4400.0
