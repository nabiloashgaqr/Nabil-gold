"""Watchdog: alert + revive long-running demo processes on stale heartbeats."""
# --- VPS: load .env if present (real env vars ALWAYS win over .env) ---
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()  # override=False: task-wrapper vars take precedence
except Exception:
    pass

import json
import os
import sys

from datetime import datetime, timezone

# `python scripts\demo_watchdog.py` otherwise exposes scripts\, not the repo
# root, and the first stale alert crashes with ModuleNotFoundError: services.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MAX_AGE_SECONDS = 420
TICK_MAX_AGE_SECONDS = 120


def _revive(task: str) -> None:
    import subprocess
    try:
        result = subprocess.run(
            ["schtasks", "/Run", "/TN", task],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            print(f"watchdog revive {task} failed rc={result.returncode}: "
                  f"{(result.stderr or result.stdout or '').strip()}")
    except Exception as exc:  # noqa: BLE001
        print(f"watchdog revive {task} crashed: {exc}")


def _alert(msg: str) -> None:
    from services.telegram_bot import TelegramService
    from utils.helpers import load_config
    TelegramService(load_config()).send_error_alert(msg)


def _heartbeat_age(path: str) -> tuple[float | None, str | None]:
    """Return (age_seconds, error); malformed/missing files never crash."""
    if not os.path.exists(path):
        return None, "missing"
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        ts = datetime.fromisoformat(str(payload["ts"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds(), None
    except Exception as exc:  # noqa: BLE001
        return None, f"invalid ({type(exc).__name__}: {exc})"


def _check(path: str, max_age: float, task: str, label: str) -> None:
    age, error = _heartbeat_age(path)
    if error:
        msg = f"🧪 DEMO watchdog: {label} heartbeat {error} — reviving."
        print(msg)
        _alert(msg)
        _revive(task)
        return
    if age is not None and age > max_age:
        msg = f"🧪 DEMO watchdog: {label} stale ({age:.0f}s) — reviving."
        print(msg)
        _alert(msg)
        _revive(task)


def main() -> None:
    _check("heartbeat.json", MAX_AGE_SECONDS, "SS_DemoLoop", "demo loop")
    _check("tick_heartbeat.json", TICK_MAX_AGE_SECONDS,
           "SS_TickManager", "tick manager")


if __name__ == "__main__":
    main()
