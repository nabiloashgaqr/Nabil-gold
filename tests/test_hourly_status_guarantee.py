"""The hourly status must survive every exit path of an analysis cycle.

It used to be emitted from the `elif decision_type == "WAIT"` branch at the end
of the function, so any of the earlier `return` statements dropped it silently
while the workflow still reported success. Delivery is now armed early and
flushed from a `finally` block.
"""
from __future__ import annotations

from typing import Any, Dict, List

import scripts.run_analysis as ra


class _Telegram:
    def __init__(self) -> None:
        self.messages: List[str] = []

    def send_message(self, message: str, *args: Any, **kwargs: Any) -> bool:
        self.messages.append(str(message))
        return True


class _DB:
    def get_open_trades(self) -> List[Dict[str, Any]]:
        return []


def _delivery(telegram=None):
    return ra._HourlyStatusDelivery(telegram or _Telegram(), {"symbol": "XAU/USD"})


def _arm(delivery, note=None):
    delivery.arm(
        decision={"decision": "WAIT", "symbol": "XAU/USD", "current_price": 4040.0},
        all_results={"symbol": "XAU/USD", "current_price": 4040.0},
        database=_DB(),
        note=note,
    )


def test_armed_status_is_flushed() -> None:
    telegram = _Telegram()
    delivery = _delivery(telegram)
    _arm(delivery)
    delivery.flush()
    assert len(telegram.messages) == 1


def test_flush_is_idempotent() -> None:
    # `finally` may run after an explicit flush; the user must not get duplicates.
    telegram = _Telegram()
    delivery = _delivery(telegram)
    _arm(delivery)
    delivery.flush()
    delivery.flush()
    assert len(telegram.messages) == 1


def test_unarmed_cycle_sends_nothing() -> None:
    telegram = _Telegram()
    delivery = _delivery(telegram)
    delivery.flush()
    assert telegram.messages == []


def test_mark_sent_suppresses_the_status() -> None:
    # A real trade alert already carries this cycle's context.
    telegram = _Telegram()
    delivery = _delivery(telegram)
    _arm(delivery)
    delivery.mark_sent()
    delivery.flush()
    assert telegram.messages == []


def test_block_note_is_prefixed_to_the_status() -> None:
    telegram = _Telegram()
    delivery = _delivery(telegram)
    _arm(delivery, note="🚫 SELL signal blocked at duplicate filter — same zone")
    delivery.flush()
    body = telegram.messages[0]
    assert "blocked at duplicate filter" in body
    assert "Market Status" in body


def test_telegram_failure_does_not_propagate() -> None:
    class Broken:
        def send_message(self, *args: Any, **kwargs: Any):
            raise RuntimeError("telegram down")

    delivery = ra._HourlyStatusDelivery(Broken(), {"symbol": "XAU/USD"})
    _arm(delivery)
    delivery.flush()  # must not raise


def test_data_outage_is_reported(monkeypatch) -> None:
    telegram = _Telegram()
    monkeypatch.setattr(ra, "should_send_hourly_status", lambda cfg: True)
    ra._report_data_outage(telegram, {}, "XAU/USD", "No market data returned")
    assert len(telegram.messages) == 1
    assert "Analysis skipped" in telegram.messages[0]
    assert "No market data returned" in telegram.messages[0]


def test_data_outage_respects_hourly_cadence(monkeypatch) -> None:
    telegram = _Telegram()
    monkeypatch.setattr(ra, "should_send_hourly_status", lambda cfg: False)
    ra._report_data_outage(telegram, {}, "XAU/USD", "No market data returned")
    assert telegram.messages == []
