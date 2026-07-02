"""Tests for utils.sessions — unified session classification."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from utils.sessions import (
    classify_session,
    session_label_from_utc,
    session_label_from_trade,
    session_sort_key,
    session_arabic,
    SESSION_ORDER,
    SESSION_AR,
)


class TestClassifySession:
    """Test classify_session(hour_local)."""

    def test_asia_morning(self):
        assert classify_session(3) == "Asia Morning"
        assert classify_session(5) == "Asia Morning"
        assert classify_session(9) == "Asia Morning"

    def test_london_europe_midday(self):
        assert classify_session(10) == "London / Europe Midday"
        assert classify_session(12) == "London / Europe Midday"
        assert classify_session(14) == "London / Europe Midday"

    def test_london_ny_afternoon(self):
        assert classify_session(15) == "London + New York Afternoon"
        assert classify_session(17) == "London + New York Afternoon"
        assert classify_session(18) == "London + New York Afternoon"

    def test_ny_evening(self):
        assert classify_session(19) == "New York Evening"
        assert classify_session(21) == "New York Evening"
        assert classify_session(23) == "New York Evening"

    def test_late_ny_night(self):
        assert classify_session(0) == "Late New York Night"
        assert classify_session(1) == "Late New York Night"
        assert classify_session(2) == "Late New York Night"

    def test_boundary_at_3(self):
        assert classify_session(3) == "Asia Morning"

    def test_boundary_at_10(self):
        assert classify_session(10) == "London / Europe Midday"

    def test_boundary_at_15(self):
        assert classify_session(15) == "London + New York Afternoon"

    def test_boundary_at_19(self):
        assert classify_session(19) == "New York Evening"


class TestSessionLabelFromUtc:
    """Test session_label_from_utc(timestamp).

    Jerusalem offset in January = UTC+2:
      UTC 00:00 → Jerusalem 02:00 → Late NY Night
      UTC 01:00 → Jerusalem 03:00 → Asia Morning
      UTC 07:00 → Jerusalem 09:00 → Asia Morning
      UTC 08:00 → Jerusalem 10:00 → London / Europe Midday
      UTC 13:00 → Jerusalem 15:00 → London + NY Afternoon
      UTC 17:00 → Jerusalem 19:00 → NY Evening
      UTC 22:00 → Jerusalem 00:00 → Late NY Night
    """

    def test_utc_01_00_asia(self):
        # 01:00 UTC = 03:00 Jerusalem → Asia Morning
        assert session_label_from_utc("2026-01-01T01:00:00Z") == "Asia Morning"

    def test_utc_07_00_asia(self):
        # 07:00 UTC = 09:00 Jerusalem → Asia Morning
        assert session_label_from_utc("2026-01-01T07:00:00Z") == "Asia Morning"

    def test_utc_08_00_london(self):
        # 08:00 UTC = 10:00 Jerusalem → London / Europe Midday
        assert session_label_from_utc("2026-01-01T08:00:00Z") == "London / Europe Midday"

    def test_utc_13_00_afternoon(self):
        # 13:00 UTC = 15:00 Jerusalem → London + NY Afternoon
        assert session_label_from_utc("2026-01-01T13:00:00Z") == "London + New York Afternoon"

    def test_utc_17_00_evening(self):
        # 17:00 UTC = 19:00 Jerusalem → NY Evening
        assert session_label_from_utc("2026-01-01T17:00:00Z") == "New York Evening"

    def test_utc_22_00_night(self):
        # 22:00 UTC = 00:00 Jerusalem → Late NY Night
        assert session_label_from_utc("2026-01-01T22:00:00Z") == "Late New York Night"

    def test_none_returns_unknown(self):
        assert session_label_from_utc(None) == "Unknown"

    def test_empty_string_returns_unknown(self):
        assert session_label_from_utc("") == "Unknown"

    def test_invalid_string_returns_unknown(self):
        assert session_label_from_utc("not-a-date") == "Unknown"

    def test_datetime_object(self):
        dt = datetime(2026, 1, 1, 7, 0, 0, tzinfo=timezone.utc)  # 09:00 Jerusalem → Asia
        assert session_label_from_utc(dt) == "Asia Morning"

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime(2026, 1, 1, 7, 0, 0)  # no tzinfo
        result = session_label_from_utc(dt)
        assert result != "Unknown"


class TestSessionLabelFromTrade:
    """Test session_label_from_trade(trade_dict)."""

    def test_valid_session_label_stored(self):
        trade = {"session_label": "Asia Morning", "entry_time": "2026-01-01T07:00:00Z"}
        assert session_label_from_trade(trade) == "Asia Morning"

    def test_legacy_config_name_falls_back_to_timestamp(self):
        """Legacy 'Main Trading Session' should be reclassified from timestamp."""
        trade = {
            "session_label": "Main Trading Session",
            "entry_time": "2026-01-01T08:00:00Z",  # 10:00 Jerusalem → London
        }
        result = session_label_from_trade(trade)
        assert result == "London / Europe Midday"

    def test_snapshot_session_info(self):
        trade = {
            "signal_snapshot": {
                "session_info": {"current_session": "London + New York Afternoon"}
            },
            "entry_time": "2026-01-01T13:00:00Z",
        }
        assert session_label_from_trade(trade) == "London + New York Afternoon"

    def test_snapshot_legacy_name_falls_back(self):
        trade = {
            "signal_snapshot": {
                "session_info": {"current_session": "Main Trading Session"}
            },
            "entry_time": "2026-01-01T13:00:00Z",  # 15:00 Jerusalem → London+NY
        }
        result = session_label_from_trade(trade)
        assert result == "London + New York Afternoon"

    def test_compute_from_entry_time(self):
        # 17:00 UTC = 19:00 Jerusalem → NY Evening
        trade = {"entry_time": "2026-01-01T17:00:00Z"}
        result = session_label_from_trade(trade)
        assert result == "New York Evening"

    def test_compute_from_created_at(self):
        # 01:00 UTC = 03:00 Jerusalem → Asia Morning
        trade = {"created_at": "2026-01-01T01:00:00Z"}
        result = session_label_from_trade(trade)
        assert result == "Asia Morning"

    def test_empty_trade_returns_unknown(self):
        assert session_label_from_trade({}) == "Unknown"

    def test_string_snapshot_parsed(self):
        import json
        trade = {
            "signal_snapshot": json.dumps({"session_info": {"current_session": "New York Evening"}}),
            "entry_time": "2026-01-01T17:00:00Z",
        }
        result = session_label_from_trade(trade)
        assert result == "New York Evening"


class TestSessionSortKey:
    """Test session_sort_key(label)."""

    def test_order(self):
        assert session_sort_key("Asia Morning") < session_sort_key("London / Europe Midday")
        assert session_sort_key("London / Europe Midday") < session_sort_key("London + New York Afternoon")
        assert session_sort_key("London + New York Afternoon") < session_sort_key("New York Evening")
        assert session_sort_key("New York Evening") < session_sort_key("Late New York Night")

    def test_unknown_is_last(self):
        assert session_sort_key("Unknown") == 99
        assert session_sort_key("RandomName") == 99


class TestSessionArabic:
    """Test session_arabic(label)."""

    def test_all_labels(self):
        for label in SESSION_ORDER:
            ar = session_arabic(label)
            assert ar in SESSION_AR.values()

    def test_unknown_returns_as_is(self):
        assert session_arabic("Something Else") == "Something Else"


class TestSessionOrder:
    """Validate SESSION_ORDER has all expected entries."""

    def test_five_sessions(self):
        assert len(SESSION_ORDER) == 5

    def test_expected_names(self):
        assert "Asia Morning" in SESSION_ORDER
        assert "London / Europe Midday" in SESSION_ORDER
        assert "London + New York Afternoon" in SESSION_ORDER
        assert "New York Evening" in SESSION_ORDER
        assert "Late New York Night" in SESSION_ORDER
