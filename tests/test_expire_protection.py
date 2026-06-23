"""Tests for keep_protected_winners_open: a time-expired trade that is winning
AND protected (stop at breakeven or better) must NOT be force-closed."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from agents.open_trades_manager import OpenTradesManager


def _mgr(keep=True, expire_h=24):
    cfg = {
        "trade_management": {"expire_after_hours": expire_h, "time_warning_hours": 999,
                             "keep_protected_winners_open": keep,
                             "auto_move_sl_to_entry_after_tp1": True},
        "trailing_stop": {"enabled": True, "trailing_distance": 100.0, "trailing_step": 5.0,
                          "early_breakeven_points": 100.0, "min_profit_lock": 0.0},
    }
    return OpenTradesManager(cfg)


def _old_trade(**over):
    now = datetime.now(timezone.utc)
    opened = (now - timedelta(hours=30)).isoformat()
    t = {"id": "T", "type": "SELL", "status": "OPEN", "entry_price": 4130.14,
         "stop_loss": 4150.14, "tp1": 4103.47, "tp2": 4083.47,
         "sl_moved_to_entry": False, "updates_sent": [], "entry_time": opened, "created_at": opened}
    t.update(over)
    return t, now


def test_protected_winner_not_expired():
    mgr = _mgr()
    # Winning SELL, stop already at entry (protected).
    t, now = _old_trade(stop_loss=4130.14, sl_moved_to_entry=True)
    ev = mgr.evaluate_trade(t, 4111.21, now=now)  # +189 pts
    assert "EXPIRED" not in ev["events"]
    assert ev["new_status"] != "EXPIRED"


def test_losing_trade_still_expires():
    mgr = _mgr()
    t, now = _old_trade(stop_loss=4150.14, sl_moved_to_entry=False)
    ev = mgr.evaluate_trade(t, 4135.0, now=now)  # SELL losing
    assert "EXPIRED" in ev["events"]
    assert ev["new_status"] == "EXPIRED"


def test_winning_but_unprotected_still_expires():
    mgr = _mgr()
    # Small +21 pts profit, stop NOT moved -> could still reverse to a loss.
    t, now = _old_trade(stop_loss=4150.14, sl_moved_to_entry=False)
    ev = mgr.evaluate_trade(t, 4128.0, now=now)
    assert "EXPIRED" in ev["events"]


def test_flag_off_reverts_to_legacy_expire():
    mgr = _mgr(keep=False)
    # Even protected winner expires when the protection flag is disabled.
    t, now = _old_trade(stop_loss=4130.14, sl_moved_to_entry=True)
    ev = mgr.evaluate_trade(t, 4111.21, now=now)
    assert "EXPIRED" in ev["events"]


def test_buy_protected_winner_not_expired():
    mgr = _mgr()
    now = datetime.now(timezone.utc)
    opened = (now - timedelta(hours=30)).isoformat()
    t = {"id": "B", "type": "BUY", "status": "OPEN", "entry_price": 4000.0,
         "stop_loss": 4010.0, "tp1": 4026.7, "tp2": 4047.0,  # stop above entry = protected
         "sl_moved_to_entry": True, "updates_sent": [], "entry_time": opened, "created_at": opened}
    ev = mgr.evaluate_trade(t, 4020.0, now=now)  # +200 pts
    assert "EXPIRED" not in ev["events"]
