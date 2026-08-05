"""A breakeven stop must not be executed by a wick that closes far above it.

Operator report (b8ae314a, 2026-08-05): after TP1 armed the breakeven at the
entry (4163.00), a candle printed a low wick near the entry but CLOSED at
4180.75, and the manager reported BE_HIT at 4163.00 with PnL +0 -- "the stop
was hit even though price never came back". The cause was judging the
breakeven on the raw candle low via `_stop_touched`.

Rule pinned here: a breakeven stop on a protected trade executes only by
  * the current candle CLOSING through the stop, or
  * a completed candle printed after the stop was armed whose extreme pierced it.
A wick of the (possibly forming) current candle that closes far above must not
close the trade.

FAULT INJECTION: revert the `elif protected_trade:` branch in
`agents/open_trades_manager.py` to `_stop_touched(active_protective_stop)` and
the phantom test fails.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.open_trades_manager import OpenTradesManager

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

NOW = datetime(2026, 8, 5, 11, 25, 0, tzinfo=timezone.utc)
ENTRY = 4163.00          # breakeven stop sits here
TP1 = 4178.03
TP2 = 4216.69


def _protected_trade() -> dict:
    stamp = (NOW - timedelta(hours=4)).isoformat()
    return {
        "id": "TRADE_BE_WICK",
        "symbol": "XAU/USD",
        "type": "BUY",
        "order_type": "BUY_LIMIT",
        "status": "TP1_HIT",
        "entry_price": ENTRY,
        "stop_loss": ENTRY,          # breakeven
        "initial_stop_loss": 4148.00,
        "tp1": TP1,
        "tp2": TP2,
        "sl_moved_to_entry": True,
        "partial_close": True,
        "created_at": stamp,
        "entry_time": stamp,
        "last_updated": (NOW - timedelta(minutes=5)).isoformat(),
        "trailing_stop_source_time": (NOW - timedelta(minutes=6)).isoformat(),
        "updates_sent": ["TP1_HIT", "MOVE_SL_TO_BE"],
        "signal_snapshot": {
            "setup_context": {"pending_plan_role": "PRIMARY", "selection_role": "PRIMARY"},
            "setup_type": "LIQUIDITY_REVERSAL",
            "signal": {"entry": {"low": 4157.60, "high": 4163.60}},
        },
    }


def _evaluate(trade, price, high, low, candles):
    return OpenTradesManager(CONFIG).evaluate_trade(
        trade, price, NOW, candle_high=high, candle_low=low,
        recent_candles=candles, market_data_source="twelvedata",
        candle_time=(NOW - timedelta(minutes=1)).isoformat(),
    )


# --- the phantom -----------------------------------------------------------

def test_a_wick_that_closes_far_above_does_not_hit_breakeven() -> None:
    """b8ae314a: low wick 4162 but the candle closes at 4180.75. No BE_HIT."""
    current = [{"time": (NOW - timedelta(minutes=5)).isoformat(),
                "high": 4176.0, "low": 4168.0, "close": 4174.0}]
    current.append({"time": (NOW - timedelta(minutes=1)).isoformat(),
                    "high": 4180.75, "low": 4162.0, "close": 4180.75})
    result = _evaluate(_protected_trade(), 4180.75, 4180.75, 4162.0, current)
    assert result.get("new_status") != "BE_HIT", (
        "breakeven executed by a wick that closed far above it"
    )
    assert "BE_HIT" not in (result.get("events") or [])


# --- genuine executions still work ------------------------------------------

def test_a_candle_closing_through_breakeven_still_hits_it() -> None:
    candles = [
        {"time": (NOW - timedelta(minutes=5)).isoformat(), "high": 4170.0, "low": 4165.0, "close": 4168.0},
        {"time": (NOW - timedelta(minutes=1)).isoformat(), "high": 4166.0, "low": 4159.0, "close": 4160.0},
    ]
    result = _evaluate(_protected_trade(), 4160.0, 4166.0, 4159.0, candles)
    assert result.get("new_status") == "BE_HIT"
    assert "BE_HIT" in (result.get("events") or [])


def test_a_completed_candle_after_arming_piercing_breakeven_hits_it() -> None:
    """A completed (not latest) candle printed after the stop was armed that
    pierced the breakeven must still execute it."""
    candles = [
        {"time": (NOW - timedelta(minutes=5)).isoformat(), "high": 4170.0, "low": 4161.0, "close": 4169.0},
        {"time": (NOW - timedelta(minutes=1)).isoformat(), "high": 4180.0, "low": 4175.0, "close": 4180.0},
    ]
    result = _evaluate(_protected_trade(), 4180.0, 4180.0, 4175.0, candles)
    assert result.get("new_status") == "BE_HIT"
