"""Tests for TradingSessionAgent."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.trading_session_agent import TradingSessionAgent


def make_config(**overrides):
    base = {
        "trading_hours": {
            "enabled": True,
            "timezone": "UTC",
            "sessions": [
                # Main session: 11:00-17:00 UTC, Sun(6)-Thu(3) [Python weekday: 0=Mon,6=Sun]
                {"name": "London-NY Trading", "start_hour": 11, "end_hour": 17, "days": [6, 0, 1, 2, 3], "quality": "HIGH", "description": "11:00-17:00 UTC"},
                # Overlap: 13:00-17:00 UTC, Mon(0)-Thu(3) — BEST quality, narrowest (4h)
                {"name": "London-NY Overlap", "start_hour": 13, "end_hour": 17, "days": [0, 1, 2, 3], "quality": "BEST", "description": "Best overlap"},
                # Asian: 1:00-6:00 UTC, Sun-Thu
                {"name": "Asian", "start_hour": 1, "end_hour": 6, "days": [6, 0, 1, 2, 3], "quality": "LOW", "description": "Asian session"},
            ],
            "min_quality_required": "HIGH",
            "exclude_weekends": False,
            "friday_cutoff_hour": 20,
        },
        "signal_filters": {"allow_friday_after_hours": False},
    }
    for key, value in overrides.items():
        base[key] = value
    return base


# Static dates for predictable testing:
# Monday June 15, Tuesday June 16, Wednesday June 17, Thursday June 18, Friday June 19, Saturday June 20, Sunday June 21
def monday(hour: int) -> datetime:
    return datetime(2026, 6, 15, hour, 0, 0, tzinfo=timezone.utc)


def tuesday(hour: int) -> datetime:
    return datetime(2026, 6, 16, hour, 0, 0, tzinfo=timezone.utc)


def thursday(hour: int) -> datetime:
    return datetime(2026, 6, 18, hour, 0, 0, tzinfo=timezone.utc)


def friday(hour: int) -> datetime:
    return datetime(2026, 6, 19, hour, 0, 0, tzinfo=timezone.utc)


def saturday(hour: int) -> datetime:
    return datetime(2026, 6, 20, hour, 0, 0, tzinfo=timezone.utc)


def sunday(hour: int) -> datetime:
    return datetime(2026, 6, 21, hour, 0, 0, tzinfo=timezone.utc)


# ─────────────────────────── Main session (11:00-17:00) ────────────────────


def test_trading_allowed_at_14_monday():
    """Monday 14:00 should be allowed (within 11:00-17:00)."""
    config = make_config()
    agent = TradingSessionAgent(config)
    result = agent.check(now=monday(14))
    assert result["trading_allowed"] is True
    assert result["current_session"] in {"London-NY Trading", "London-NY Overlap"}
    assert result["session_quality"] in {"HIGH", "BEST"}


def test_trading_blocked_before_11_monday():
    """Monday 10:00 should be blocked (before 11:00)."""
    config = make_config()
    agent = TradingSessionAgent(config)
    result = agent.check(now=monday(10))
    assert result["trading_allowed"] is False
    assert "Outside trading hours" in result["reason"]


def test_trading_blocked_after_17_monday():
    """Monday 23:00 should be blocked (after 17:00)."""
    config = make_config()
    agent = TradingSessionAgent(config)
    result = agent.check(now=monday(23))
    assert result["trading_allowed"] is False
    assert "Outside trading hours" in result["reason"]


def test_trading_allowed_at_11_exact():
    """Monday 11:00 exactly should be allowed (start of session)."""
    config = make_config()
    agent = TradingSessionAgent(config)
    result = agent.check(now=monday(11))
    assert result["trading_allowed"] is True
    assert result["current_session"] == "London-NY Trading"


def test_trading_allowed_at_17_exact():
    """Monday 17:00 exactly should be allowed (end of session)."""
    config = make_config()
    agent = TradingSessionAgent(config)
    result = agent.check(now=monday(17))
    assert result["trading_allowed"] is True


def test_trading_allowed_at_16_59():
    """Monday 16:59 should be allowed."""
    config = make_config()
    agent = TradingSessionAgent(config)
    result = agent.check(now=datetime(2026, 6, 15, 16, 59, 0, tzinfo=timezone.utc))
    assert result["trading_allowed"] is True


def test_trading_allowed_sunday_14():
    """Sunday 14:00 should be allowed (Sunday in days [0,1,2,3,4])."""
    config = make_config()
    agent = TradingSessionAgent(config)
    result = agent.check(now=sunday(14))
    assert result["trading_allowed"] is True
    assert result["current_session"] == "London-NY Trading"


def test_trading_allowed_thursday_15():
    """Thursday 15:00 should be allowed (overlap BEST quality)."""
    config = make_config()
    agent = TradingSessionAgent(config)
    result = agent.check(now=thursday(15))
    assert result["trading_allowed"] is True
    assert result["current_session"] == "London-NY Overlap"
    assert result["session_quality"] == "BEST"


# ─────────────────────────── Friday exclusion ─────────────────────────────


def test_friday_blocked():
    """Friday (weekday=4) should be blocked - not in days [0,1,2,3,4]."""
    config = make_config()
    agent = TradingSessionAgent(config)
    result = agent.check(now=friday(14))
    assert result["trading_allowed"] is False
    assert result["current_session"] is None


# ─────────────────────────── Saturday exclusion ────────────────────────────


def test_saturday_blocked():
    """Saturday (weekday=5) should be blocked - not in days [0,1,2,3,4]."""
    config = make_config()
    agent = TradingSessionAgent(config)
    result = agent.check(now=saturday(14))
    assert result["trading_allowed"] is False
    assert result["current_session"] is None


# ─────────────────────────── Disabled / utility ────────────────────────────


def test_disabled_always_allows():
    """When trading_hours.enabled is False, always allow."""
    config = make_config()
    config["trading_hours"]["enabled"] = False
    agent = TradingSessionAgent(config)
    result = agent.check(now=saturday(3))  # Would normally be Asian LOW
    assert result["trading_allowed"] is True
    assert result["session_quality"] == "UNKNOWN"


def test_returns_all_required_fields():
    """check() must return all required fields."""
    agent = TradingSessionAgent(make_config())
    result = agent.check(now=monday(14))
    for field in ("agent", "trading_allowed", "reason", "current_session", "session_quality", "is_trading_hours", "summary"):
        assert field in result, f"Missing field: {field}"