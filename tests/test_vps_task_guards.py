"""Fault-injection tests for the VPS single-instance guard.

The tick manager and the demo loop are persistent ONLOGON processes on the
VPS. If the guard silently lets a SECOND instance run, MT5 orders get managed
twice (double partial closes, fighting over the same ticket). These tests
must FAIL if the guard is removed or weakened.
"""
import os

import pytest

from utils.single_instance import acquire_single_instance, pid_alive


def test_pid_alive_self():
    assert pid_alive(os.getpid()) is True


def test_pid_alive_rejects_garbage():
    assert pid_alive(0) is False
    assert pid_alive(-5) is False
    # well above any plausible PID (Linux pid_max default 4194304)
    assert pid_alive(2 ** 30) is False


def test_acquire_fresh(tmp_path):
    pf = str(tmp_path / "loop.pid")
    assert acquire_single_instance(pf) is True
    with open(pf, encoding="utf-8") as fh:
        assert fh.read().strip() == str(os.getpid())


def test_acquire_takes_over_stale_pidfile(tmp_path):
    pf = str(tmp_path / "loop.pid")
    with open(pf, "w", encoding="utf-8") as fh:
        fh.write(str(2 ** 30))  # dead pid
    assert acquire_single_instance(pf) is True
    with open(pf, encoding="utf-8") as fh:
        assert fh.read().strip() == str(os.getpid())


def test_acquire_takes_over_corrupt_pidfile(tmp_path):
    pf = str(tmp_path / "loop.pid")
    with open(pf, "w", encoding="utf-8") as fh:
        fh.write("not-a-pid")
    assert acquire_single_instance(pf) is True


def test_acquire_refuses_when_live_instance_owns_pidfile(tmp_path):
    pf = str(tmp_path / "loop.pid")
    parent = os.getppid()  # alive and != this process
    assert parent != os.getpid()
    with open(pf, "w", encoding="utf-8") as fh:
        fh.write(str(parent))
    assert acquire_single_instance(pf) is False
    # pidfile must NOT be overwritten by the refused instance
    with open(pf, encoding="utf-8") as fh:
        assert fh.read().strip() == str(parent)


def test_same_pid_is_allowed(tmp_path):
    """A re-entry by the SAME pid (restart race) must not deadlock itself."""
    pf = str(tmp_path / "loop.pid")
    with open(pf, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    assert acquire_single_instance(pf) is True
