"""Hourly status must survive the failures that used to silence it.

The delivery tracker closed the "fourteen return statements" hole, but three
narrower paths could still end a cycle with no message and a green workflow:

1. the cycle is armed before the database handle exists;
2. the message builder itself raises (Supabase down, malformed trade row);
3. a path that sends its own message does not tell the tracker, so the
   subscriber gets the same cycle reported twice.

These are regression guards for those three cases.
"""

from __future__ import annotations

import scripts.run_analysis as ra


class _FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, text: str, **_kwargs) -> bool:
        self.messages.append(text)
        return True


class _BrokenDatabase:
    """Stands in for Supabase being unreachable mid-cycle."""

    def get_open_trades(self):
        raise RuntimeError("supabase unreachable")


_CONFIG = {"symbol": "XAU/USD"}


def test_armed_cycle_without_database_still_reports() -> None:
    """A crash before the database is attached must not swallow the status."""
    telegram = _FakeTelegram()
    delivery = ra._HourlyStatusDelivery(telegram, _CONFIG)
    delivery.due = True  # armed, but database was never set

    delivery.flush()

    assert len(telegram.messages) == 1
    assert "Market Status" in telegram.messages[0]


def test_builder_failure_falls_back_to_minimal_note() -> None:
    """A failing message builder must degrade, not disappear."""
    telegram = _FakeTelegram()
    delivery = ra._HourlyStatusDelivery(telegram, _CONFIG)
    delivery.arm(
        decision={"decision": "WAIT", "symbol": "XAU/USD"},
        all_results={},
        database=_BrokenDatabase(),
    )

    delivery.flush()

    assert len(telegram.messages) == 1
    body = telegram.messages[0]
    assert "XAU/USD" in body
    assert "WAIT" in body
    assert "Full status unavailable" in body


def test_marked_sent_paths_do_not_double_report() -> None:
    """A path that already messaged the user must suppress the flush."""
    telegram = _FakeTelegram()
    delivery = ra._HourlyStatusDelivery(telegram, _CONFIG)
    delivery.arm(
        decision={"decision": "PENDING", "symbol": "XAU/USD"},
        all_results={},
        database=_BrokenDatabase(),
    )

    delivery.mark_sent()
    delivery.flush()

    assert telegram.messages == []


def test_flush_is_idempotent() -> None:
    """`finally` can run alongside an explicit flush; send exactly once."""
    telegram = _FakeTelegram()
    delivery = ra._HourlyStatusDelivery(telegram, _CONFIG)
    delivery.arm(
        decision={"decision": "WAIT", "symbol": "XAU/USD"},
        all_results={},
        database=_BrokenDatabase(),
    )

    delivery.flush()
    delivery.flush()

    assert len(telegram.messages) == 1


def test_data_outage_suppresses_the_generic_status(monkeypatch) -> None:
    """The outage note replaces the hourly status rather than preceding it."""
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("SEND_STATUS_ON_MANUAL", "true")

    telegram = _FakeTelegram()
    delivery = ra._HourlyStatusDelivery(telegram, _CONFIG)
    delivery.arm(
        decision={"decision": "PENDING", "symbol": "XAU/USD"},
        all_results={},
        database=_BrokenDatabase(),
    )

    ra._report_data_outage(
        telegram, _CONFIG, "XAU/USD",
        "No market data returned (provider quota, rate limit, or outage).",
        delivery=delivery,
    )
    delivery.flush()

    assert len(telegram.messages) == 1
    assert "Analysis skipped" in telegram.messages[0]


def test_data_outage_stays_silent_when_status_is_off(monkeypatch) -> None:
    """Silent cycles must remain silent: no outage spam every 5 minutes."""
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("SEND_STATUS_ON_MANUAL", "false")

    telegram = _FakeTelegram()
    ra._report_data_outage(telegram, _CONFIG, "XAU/USD", "provider outage")

    assert telegram.messages == []
