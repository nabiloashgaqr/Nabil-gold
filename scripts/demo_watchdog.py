"""Watchdog: alert + restart hint when the demo loop heartbeat goes stale."""
# --- VPS: load .env if present (real env vars ALWAYS win over .env) ---
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()  # override=False: task-wrapper vars take precedence
except Exception:
    pass

import json
import os

from datetime import datetime, timezone

MAX_AGE_SECONDS = 420
TICK_MAX_AGE_SECONDS = 120


def _revive(task: str) -> None:
    import subprocess
    try:
        subprocess.run(["schtasks", "/Run", "/TN", task],
                       capture_output=True, timeout=30)
    except Exception:  # noqa: BLE001
        pass


def _alert(msg: str) -> None:
    from services.telegram_bot import TelegramService
    from utils.helpers import load_config
    TelegramService(load_config()).send_error_alert(msg)


def main() -> None:
    if os.path.exists("heartbeat.json"):
        with open("heartbeat.json", encoding="utf-8") as fh:
            ts = datetime.fromisoformat(json.load(fh)["ts"])
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > MAX_AGE_SECONDS:
            _alert(f"🧪 DEMO watchdog: demo loop stale ({age:.0f}s) — reviving.")
            _revive("SS_DemoLoop")
    if os.path.exists("tick_heartbeat.json"):
        with open("tick_heartbeat.json", encoding="utf-8") as fh:
            ts = datetime.fromisoformat(json.load(fh)["ts"])
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > TICK_MAX_AGE_SECONDS:
            _alert(f"🧪 DEMO watchdog: tick manager stale ({age:.0f}s) — reviving.")
            _revive("SS_TickManager")


if __name__ == "__main__":
    main()
