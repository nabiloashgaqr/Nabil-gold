"""Guards for the production trading window in config.json."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from agents.trading_session_agent import TradingSessionAgent
from utils.helpers import load_config


def _local(day: int, hour: int, minute: int = 0) -> datetime:
    # 2026-06-22 is Monday. Use aware local datetimes to avoid UTC confusion.
    return datetime(2026, 6, day, hour, minute, tzinfo=ZoneInfo("Asia/Hebron"))


def test_config_trading_window_24h_on_weekdays() -> None:
    """Operator directive 2026-08-09: FULL 24h on weekdays."""
    config = load_config()
    agent = TradingSessionAgent(config)

    for hour in (0, 2, 3, 12, 22, 23):
        r = agent.check(now=_local(22, hour, 30))  # Monday
        assert r["trading_allowed"] is True, hour
        assert r["allow_signals"] is True, hour


def test_config_trading_window_blocks_weekends() -> None:
    config = load_config()
    agent = TradingSessionAgent(config)

    saturday = agent.check(now=_local(27, 12, 0))
    sunday = agent.check(now=_local(28, 12, 0))

    assert saturday["trading_allowed"] is False
    assert sunday["trading_allowed"] is False
