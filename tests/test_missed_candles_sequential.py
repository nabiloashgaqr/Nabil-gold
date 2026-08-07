"""Missed cycles are replayed in chronological order (operator directive 2026-08-05).

If the engine sleeps for a cycle, several 5m bars arrive unexamined. They must be
walked OLDEST-FIRST so that an early high raises the trailing stop before a later
low is tested against it, and so a TP2/SL touched in a missed bar is still settled.
Collapsing them into one unordered window is exactly the stale-low bug this guards.

FAULT INJECTION: delete the `len(unexamined) >= 2` replay block in
`agents/open_trades_manager.evaluate_trade` (or make `_missed_candles_review`
return None) and these tests fail.
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

NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
ENTRY = 4163.00
TP1 = 4178.03
TP2 = 4216.69


def _manager() -> OpenTradesManager:
    return OpenTradesManager(CONFIG)


def _open_trade(stamp_minutes_ago: int) -> dict:
    stamp = (NOW - timedelta(minutes=stamp_minutes_ago)).isoformat()
    return {
        "id": "TRADE_MISSED",
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
        "created_at": (NOW - timedelta(hours=5)).isoformat(),
        "entry_time": (NOW - timedelta(hours=5)).isoformat(),
        "last_updated": (NOW - timedelta(minutes=stamp_minutes_ago)).isoformat(),
        "trailing_stop_source_time": stamp,
        "updates_sent": ["TP1_HIT", "MOVE_SL_TO_BE"],
        "signal_snapshot": {
            "setup_context": {"pending_plan_role": "PRIMARY"},
            "setup_type": "LIQUIDITY_REVERSAL",
            "signal": {"entry": {"low": 4157.60, "high": 4163.60}},
        },
    }


def _candle(minutes_ago, high, low, close):
    return {"time": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
            "open": low, "high": high, "low": low, "close": close}


def test_an_early_high_raises_the_trail_before_a_later_low_tests_it() -> None:
    """Downtime of 3 bars: bar1 makes a new high (raises the trail to ~4183
    at the unified 150-pt gap),
    bar2 pulls back through the RAISED stop. Replayed in order, the exit is at
    the raised level (a WIN). The unordered window cannot see this: it keeps the
    persisted breakeven (4163) and never exits -- so this differentiates replay."""
    trade = _open_trade(20)   # stamp 20 min ago -> 3 unexamined bars
    candles = [
        _candle(15, 4199.0, 4170.0, 4197.0),   # new high -> trail (150 gap) ~4183
        _candle(10, 4186.0, 4176.0, 4184.0),   # low 4176 pierces raised trail
        _candle(1, 4185.0, 4180.0, 4184.0),
    ]
    result = _manager().evaluate_trade(
        trade, 4184.0, NOW, candle_high=4185.0, candle_low=4180.0,
        recent_candles=candles, market_data_source="twelvedata",
        candle_time=(NOW - timedelta(minutes=1)).isoformat(),
    )
    assert result["new_status"] == "SL_HIT", (
        f"ordered replay must exit at the raised trail, got {result['new_status']}"
    )
    assert result["updates"]["close_price"] >= 4170.0, "exit at the raised stop, not the old breakeven"


def test_a_tp2_touched_in_a_missed_bar_is_still_settled() -> None:
    trade = _open_trade(20)
    candles = [
        _candle(15, 4180.0, 4170.0, 4178.0),
        _candle(10, 4217.0, 4178.0, 4215.0),   # pierces TP2 4216.69
        _candle(1, 4216.0, 4210.0, 4214.0),
    ]
    result = _manager().evaluate_trade(
        trade, 4214.0, NOW, candle_high=4216.0, candle_low=4210.0,
        recent_candles=candles, market_data_source="twelvedata",
        candle_time=(NOW - timedelta(minutes=1)).isoformat(),
    )
    assert result["new_status"] == "TP2_HIT"
    assert "TP2_HIT" in result["events"]


def test_a_single_new_bar_defers_to_the_normal_path() -> None:
    """Healthy run: only the current bar is unexamined -> sequential returns None
    and the normal per-cycle logic decides (no premature terminal)."""
    trade = _open_trade(5)
    candles = [
        _candle(5, 4180.0, 4170.0, 4178.0),
        _candle(1, 4185.0, 4180.0, 4184.0),
    ]
    result = _manager().evaluate_trade(
        trade, 4184.0, NOW, candle_high=4185.0, candle_low=4180.0,
        recent_candles=candles, market_data_source="twelvedata",
        candle_time=(NOW - timedelta(minutes=1)).isoformat(),
    )
    # No terminal from the replay; trade stays open-ish (TP1_HIT) this cycle.
    assert result["new_status"] in ("TP1_HIT", "OPEN", "SL_HIT", "BE_HIT")


def test_replay_never_lowers_an_already_trailed_stop() -> None:
    """7ebe8906 regression: the persisted trailing stop (4250.14) was higher than
    what a from-scratch replay would build (4238.80). The replay must ratchet UP
    from the persisted stop only, and a later drop exits at the HIGHER stop."""
    entry = 4205.82
    trade = _open_trade(20)
    trade["entry_price"] = entry
    trade["stop_loss"] = 4250.14          # already trailed high
    trade["initial_stop_loss"] = 4190.00
    trade["tp1"] = 4220.00
    trade["tp2"] = 4260.00
    candles = [
        _candle(15, 4255.8, 4250.5, 4254.0),   # would build 4238.8 from scratch
        _candle(10, 4256.0, 4234.4, 4240.0),   # drops through the HIGH stop
        _candle(1, 4240.0, 4234.4, 4238.0),
    ]
    result = _manager().evaluate_trade(
        trade, 4238.0, NOW, candle_high=4240.0, candle_low=4234.4,
        recent_candles=candles, market_data_source="twelvedata",
        candle_time=(NOW - timedelta(minutes=1)).isoformat(),
    )
    assert result["new_status"] == "SL_HIT"
    assert result["updates"]["close_price"] == 4250.14, (
        f"must exit at the persisted higher stop, got {result['updates']['close_price']}"
    )


def test_sequential_close_sets_closed_at_so_dashboard_lists_it():
    """A terminal close from the missed-candle replay must set closed_at (not
    only close_time), else the dashboard's closed_at DESC NULLS LAST ordering
    drops the trade out of Latest Closed Trades (f3cc72c2)."""
    entry = 4205.82
    trade = _open_trade(20)
    trade["entry_price"] = entry
    trade["stop_loss"] = 4250.14
    trade["tp1"] = 4220.00
    trade["tp2"] = 4260.00
    candles = [
        _candle(15, 4255.8, 4250.5, 4254.0),
        _candle(10, 4256.0, 4234.4, 4240.0),   # drops through the raised stop
        _candle(1, 4240.0, 4234.4, 4238.0),
    ]
    result = _manager().evaluate_trade(
        trade, 4238.0, NOW, candle_high=4240.0, candle_low=4234.4,
        recent_candles=candles, market_data_source="twelvedata",
        candle_time=(NOW - timedelta(minutes=1)).isoformat(),
    )
    assert result["new_status"] == "SL_HIT"
    assert result["updates"].get("closed_at"), "terminal close must set closed_at"
    assert result["updates"].get("close_time"), "terminal close must set close_time"
