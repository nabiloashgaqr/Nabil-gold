"""Operator directives 2026-08-09 — regression + fault-injection.

1) Trading is 24h on weekdays (Mon-Fri), weekends fully closed.
2) Base candles are 15m (not 5m); 1H/4H resampled from 15m.
3) The old 4H daily-bias contrarian gate is DISABLED.
4) Saturday/Sunday: no analysis, no trade updates (hard gate).
"""
from datetime import datetime

from utils.helpers import is_weekend_hebron, load_config


def test_weekend_detection():
    sat = datetime(2026, 8, 8, 12, 0)   # Saturday
    sun = datetime(2026, 8, 9, 12, 0)   # Sunday
    mon = datetime(2026, 8, 10, 3, 0)   # Monday
    fri = datetime(2026, 8, 14, 23, 59)  # Friday late
    assert is_weekend_hebron(sat) is True
    assert is_weekend_hebron(sun) is True
    assert is_weekend_hebron(mon) is False
    assert is_weekend_hebron(fri) is False


def test_config_24h_weekdays_only():
    cfg = load_config()
    th = cfg["trading_hours"]
    s = th["sessions"][0]
    assert (s["start_hour"], s["start_minute"]) == (0, 0)
    assert (s["end_hour"], s["end_minute"]) == (24, 0)
    # days encoding in the agent: 0=Monday -> Mon..Fri = 0..4
    assert s["days"] == [0, 1, 2, 3, 4]


def test_config_all_four_candles_available():
    """Operator clarification 2026-08-09: 5m stays, PLUS 15m/1H/4H."""
    cfg = load_config()
    assert cfg["data_source"]["base_timeframe"] == "5m"
    for tf in ("5m", "15m", "1H", "4H"):
        assert tf in cfg["timeframes"]


def test_config_daily_bias_gate_removed():
    cfg = load_config()
    assert cfg["daily_bias_filter"]["enabled"] is False


def test_trade_updates_hard_stop_on_weekend(monkeypatch):
    import scripts.run_trade_updates as ru
    import utils.helpers as uh
    monkeypatch.setattr(uh, "is_weekend_hebron", lambda now=None: True)

    def _boom(*a, **k):
        raise AssertionError("weekend must not touch config/db/telegram")

    monkeypatch.setattr(ru, "load_config", _boom)
    ru.main()  # must return silently


def test_analysis_hard_stop_on_weekend(monkeypatch):
    import scripts.run_analysis as ra
    import utils.helpers as uh
    monkeypatch.setattr(uh, "is_weekend_hebron", lambda now=None: True)

    def _boom(*a, **k):
        raise AssertionError("weekend must not run the analysis pipeline")

    monkeypatch.setattr(ra, "load_config", _boom)
    ra.main()  # must return silently
