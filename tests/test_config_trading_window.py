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
    config = load_config()
    agent = TradingSessionAgent(config)

    monday_start = agent.check(now=_local(22, 0, 0))
    monday_midday = agent.check(now=_local(22, 12, 0))
    monday_end = agent.check(now=_local(22, 23, 59))

    assert monday_start["trading_allowed"] is True
    assert monday_start["allow_signals"] is True
    assert monday_midday["trading_allowed"] is True
    assert monday_midday["allow_signals"] is True
    assert monday_end["trading_allowed"] is True
    assert monday_end["allow_signals"] is True


def test_config_trading_window_blocks_weekends() -> None:
    config = load_config()
    agent = TradingSessionAgent(config)

    saturday = agent.check(now=_local(27, 12, 0))
    sunday = agent.check(now=_local(28, 12, 0))

    assert saturday["trading_allowed"] is False
    assert sunday["trading_allowed"] is False
