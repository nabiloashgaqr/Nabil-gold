"""Guards for analysis status/no-signal notifications.

cron-job.org triggers the workflow through workflow_dispatch. Those external
runs must not send WAIT/Market Status messages every 10 minutes; only actual
signals/errors should be delivered.
"""

from __future__ import annotations

import scripts.run_analysis as ra
from utils.helpers import load_config


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


def test_native_schedule_keeps_hourly_status_when_config_enables_it(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.delenv("SEND_STATUS_ON_MANUAL", raising=False)

    # The exact minute controls should_send_hourly_status, but blocked/no-signal
    # status visibility remains enabled when config explicitly enables it.
    assert ra.should_send_status(_CONFIG) is True


def test_repository_config_disables_internal_market_status(monkeypatch):
    """Market Status is now sent only by the dedicated cron-job.org dispatch
    with send_status=true, not by repository defaults.
    """
    config = load_config()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.delenv("SEND_STATUS_ON_MANUAL", raising=False)

    assert config["notifications"]["send_no_signal_updates"] is False
    assert config["notifications"]["hourly_status"] is False
    assert config["notifications"]["notify_on_blocked_signal"] is False
    assert ra.should_send_status(config) is False
    assert ra.should_send_hourly_status(config) is False
