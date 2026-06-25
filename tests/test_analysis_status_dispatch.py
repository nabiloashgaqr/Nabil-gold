"""Guards for analysis status/no-signal notifications.

cron-job.org triggers the workflow through workflow_dispatch. Those external
runs must not send WAIT/Market Status messages every 10 minutes; only actual
signals/errors should be delivered.
"""

from __future__ import annotations

import scripts.run_analysis as ra


_CONFIG = {
    "notifications": {
        "send_no_signal_updates": True,
        "notify_on_blocked_signal": True,
        "hourly_status": True,
        "hourly_status_interval_minutes": 60,
    }
}


def test_workflow_dispatch_is_silent_by_default(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.delenv("SEND_STATUS_ON_MANUAL", raising=False)

    assert ra.should_send_status(_CONFIG) is False
    assert ra.should_send_hourly_status(_CONFIG) is False


def test_workflow_dispatch_can_opt_in_to_status(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("SEND_STATUS_ON_MANUAL", "true")

    assert ra.should_send_status(_CONFIG) is True
    assert ra.should_send_hourly_status(_CONFIG) is True


def test_native_schedule_keeps_hourly_status(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.delenv("SEND_STATUS_ON_MANUAL", raising=False)

    # The exact minute controls should_send_hourly_status, but blocked/no-signal
    # status visibility remains enabled for native schedule runs.
    assert ra.should_send_status(_CONFIG) is True
