"""Guards for visibility when a directional signal is blocked downstream.

The hourly status message is emitted from the `elif decision_type == "WAIT"`
branch. A BUY/SELL cycle that is filtered later therefore sent nothing at all:
no signal and no status. These tests pin the recovered behaviour and the
hourly-cadence limit that keeps a 5-minute loop from flooding Telegram.
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


def _notify(telegram, *, send_hourly_now=True, stage="duplicate / re-entry filter",
            reason="Duplicate signal in the same price zone", side="SELL"):
    ra._notify_blocked_directional_signal(
        telegram=telegram,
        decision={"decision": side, "symbol": "XAU/USD", "current_price": 4040.0},
        all_results={"symbol": "XAU/USD", "current_price": 4040.0},
        database=_DB(),
        config={"symbol": "XAU/USD"},
        send_hourly_now=send_hourly_now,
        stage=stage,
        reason=reason,
    )


def test_blocked_directional_signal_is_reported() -> None:
    telegram = _Telegram()
    _notify(telegram)
    assert len(telegram.messages) == 1, "a blocked BUY/SELL cycle must not stay silent"


def test_message_names_side_stage_and_reason() -> None:
    telegram = _Telegram()
    _notify(telegram)
    body = telegram.messages[0]
    assert "SELL" in body
    assert "duplicate / re-entry filter" in body
    assert "Duplicate signal in the same price zone" in body


def test_respects_the_hourly_cadence() -> None:
    # Analysis runs every 5 minutes; without this the channel would be flooded.
    telegram = _Telegram()
    _notify(telegram, send_hourly_now=False)
    assert telegram.messages == []


def test_missing_reason_still_reports() -> None:
    telegram = _Telegram()
    _notify(telegram, reason=None)
    assert len(telegram.messages) == 1
    assert "no reason recorded" in telegram.messages[0]


def test_telegram_failure_does_not_break_the_cycle() -> None:
    class Broken:
        def send_message(self, *args: Any, **kwargs: Any):
            raise RuntimeError("telegram down")

    _notify(Broken())  # must not raise


def test_status_message_is_appended_after_the_header() -> None:
    telegram = _Telegram()
    _notify(telegram)
    body = telegram.messages[0]
    assert body.index("Signal generated then blocked") < body.index("Market Status")
