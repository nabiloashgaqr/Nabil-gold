"""Guards for the production trading window in config.json."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from agents.trading_session_agent import TradingSessionAgent
from utils.helpers import load_config


def _local(day: int, hour: int, minute: int = 0) -> datetime:
    # 2026-06-22 is Monday. Use aware local datetimes to avoid UTC confusion.
    return datetime(2026, 6, day, hour, minute, tzinfo=ZoneInfo("Asia/Hebron"))


def test_config_trading_window_3am_to_10pm_on_weekdays() -> None:
    config = load_config()
    agent = TradingSessionAgent(config)

    before_session = agent.check(now=_local(22, 2, 0))    # 2:00 AM - outside
    session_start = agent.check(now=_local(22, 3, 0))     # 3:00 AM - start
    midday = agent.check(now=_local(22, 12, 0))           # 12:00 PM - inside
    session_end = agent.check(now=_local(22, 22, 0))      # 10:00 PM - end
    after_session = agent.check(now=_local(22, 23, 0))    # 11:00 PM - outside

    # Before 3:00 AM - blocked
    assert before_session["trading_allowed"] is False

    # 3:00 AM - 10:00 PM - allowed
    assert session_start["trading_allowed"] is True
    assert session_start["allow_signals"] is True
    assert midday["trading_allowed"] is True
    assert midday["allow_signals"] is True
    assert session_end["trading_allowed"] is True
    assert session_end["allow_signals"] is True

    # After 10:00 PM - blocked
    assert after_session["trading_allowed"] is False


def test_config_trading_window_blocks_weekends() -> None:
    config = load_config()
    agent = TradingSessionAgent(config)

    saturday = agent.check(now=_local(27, 12, 0))
    sunday = agent.check(now=_local(28, 12, 0))

    assert saturday["trading_allowed"] is False
    assert sunday["trading_allowed"] is False
