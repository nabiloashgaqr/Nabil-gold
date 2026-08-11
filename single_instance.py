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
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=15,
            ).stdout or ""
            return f'"{pid}",' in out  # exact PID column, no substring ghosts
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


def pid_matches_command(pid: int, marker: str) -> bool:
    """True only when PID's command line contains the expected script marker.

    Runtime pidfiles were accidentally uploaded with the VPS snapshot. Windows
    may later reuse that number for an unrelated process; PID existence alone
    would then permanently suppress the real tick manager.
    """
    if not marker:
        return True
    try:
        if os.name == "nt":
            cmd = (
                f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}';"
                f"if($p){{$p.CommandLine}}"
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, timeout=15,
            ).stdout or ""
        else:
            with open(f"/proc/{int(pid)}/cmdline", "rb") as fh:
                out = fh.read().replace(b"\0", b" ").decode(errors="replace")
        return str(marker).lower() in out.lower()
    except Exception:
        # If process identity cannot be inspected, be conservative and keep the
        # live owner rather than risk two executors.
        return True


def acquire_single_instance(pidfile: str, process_marker: str | None = None) -> bool:
    """Claim `pidfile` for THIS process.

    Returns True when this process may run (no live owner, or the pidfile is
    stale/corrupt/reused by a different command). Returns False only when
    another LIVE instance with the expected command owns the loop.
    """
    try:
        if os.path.exists(pidfile):
            with open(pidfile, "r", encoding="utf-8") as fh:
                old = int((fh.read() or "0").strip() or "0")
            if (old and old != os.getpid() and pid_alive(old)
                    and pid_matches_command(old, process_marker or "")):
                return False
    except Exception:
        pass  # unreadable/corrupt pidfile -> take over
    try:
        with open(pidfile, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except Exception:
        pass
    return True
