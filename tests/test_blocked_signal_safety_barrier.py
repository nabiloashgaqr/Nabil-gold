"""A blocked signal must never reach execution, whatever reporting is added.

Every filter in the directional branch ends in `return`. Those statements look
like flow control for the status message, but they are the only thing standing
between a rejected setup and a live order: the code immediately after them
calls `new_trade_id()`, `send_signal()` and `save_trade()`.

A plausible-looking refactor -- replacing `return` with a `blocked_reason`
variable so the reason can be reported at the end of the function -- silently
turns every one of those filters into a no-op. This module pins the invariant
so that refactor cannot land unnoticed.
"""

from __future__ import annotations

import inspect
import re

import scripts.run_analysis as ra


def _analysis_source() -> str:
    return inspect.getsource(ra._run_analysis_for_config)


def test_every_blocked_signal_notice_is_followed_by_return() -> None:
    """Reporting a block must not become an alternative to stopping."""
    lines = _analysis_source().split("\n")
    call_lines = [
        i for i, line in enumerate(lines)
        if "_notify_blocked_directional_signal(" in line
    ]

    assert call_lines, "no blocked-signal notifications found; test is stale"

    for index in call_lines:
        window = [line.strip() for line in lines[index:index + 10]]
        assert "return" in window, (
            f"blocked-signal notice at offset {index} is not followed by a "
            "return; the filter would report the block and then execute the trade"
        )


def test_execution_calls_are_unreachable_from_a_blocked_filter() -> None:
    """The order-creating calls must sit after every filter's return."""
    source = _analysis_source()

    first_execution = source.find("database.new_trade_id()")
    assert first_execution != -1, "new_trade_id call not found; test is stale"

    last_filter = source.rfind(
        "_notify_blocked_directional_signal(", 0, first_execution
    )
    assert last_filter != -1, "no filter precedes execution; test is stale"

    between = source[last_filter:first_execution]
    assert re.search(r"\n\s+return\b", between), (
        "no return between the last filter and new_trade_id(); a blocked "
        "signal could fall through into execution"
    )


def test_directional_branch_still_has_its_filters() -> None:
    """Guard against the filters being deleted rather than bypassed."""
    source = _analysis_source()
    for stage in (
        "adaptive execution — kept pending",
        "adaptive execution — missed move",
        "day-map sanity",
        "cross-path distance",
        "pending governor",
        "duplicate / re-entry filter",
    ):
        assert stage in source, f"safety filter '{stage}' disappeared"


def test_status_delivery_never_creates_orders() -> None:
    """The status tracker may only send messages, never touch the database."""
    source = inspect.getsource(ra._HourlyStatusDelivery)
    for forbidden in ("new_trade_id", "save_trade", "send_signal", "cancel_pending_orders"):
        assert forbidden not in source, (
            f"_HourlyStatusDelivery references {forbidden}; status reporting "
            "must stay strictly read-only"
        )
