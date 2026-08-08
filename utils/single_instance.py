"""Single-instance guard for long-running VPS loops.

Two persistent processes (tick manager, demo loop) must NEVER run twice on
the same VPS — duplicate tick managers would double-apply partial closes and
fight over the same MT5 orders. Each loop owns a pidfile; a second instance
detects the live first instance and exits immediately.

Pure + unit-tested (tests/test_vps_task_guards.py). Windows-first (tasklist),
with a POSIX fallback so the tests run on the GitHub Linux runners.
"""
from __future__ import annotations

import os
import subprocess


def pid_alive(pid: int) -> bool:
    """True if a process with this PID currently exists."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=15,
            ).stdout or ""
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by someone else
    except Exception:
        return False
    return True


def acquire_single_instance(pidfile: str) -> bool:
    """Claim `pidfile` for THIS process.

    Returns True when this process may run (no live owner, or the pidfile is
    stale/corrupt — we take over). Returns False when another LIVE instance
    already owns the loop.
    """
    try:
        if os.path.exists(pidfile):
            with open(pidfile, "r", encoding="utf-8") as fh:
                old = int((fh.read() or "0").strip() or "0")
            if old and old != os.getpid() and pid_alive(old):
                return False
    except Exception:
        pass  # unreadable/corrupt pidfile -> take over
    try:
        with open(pidfile, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except Exception:
        pass
    return True
