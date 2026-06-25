"""Guards for the production trading window in config.json."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from agents.trading_session_agent import TradingSessionAgent
from utils.helpers import load_config


def _local(hour: int, minute: int = 0) -> datetime:
    # 2026-06-22 is Monday. Use an aware local datetime to avoid UTC confusion.
    return datetime(2026, 6, 22, hour, minute, tzinfo=ZoneInfo("Asia/Hebron"))


def test_config_trading_window_11_to_before_19_local() -> None:
    config = load_config()
    agent = TradingSessionAgent(config)

    before = agent.check(now=_local(10, 59))
    start = agent.check(now=_local(11, 0))
    last_minute = agent.check(now=_local(18, 59))
    after = agent.check(now=_local(19, 0))

    assert before["trading_allowed"] is False
    assert start["trading_allowed"] is True
    assert start["allow_signals"] is True
    assert last_minute["trading_allowed"] is True
    assert last_minute["allow_signals"] is True
    assert after["trading_allowed"] is False
