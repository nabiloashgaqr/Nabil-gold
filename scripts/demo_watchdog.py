"""Watchdog: alert + restart hint when the demo loop heartbeat goes stale."""
import json
import os

from datetime import datetime, timezone

MAX_AGE_SECONDS = 420


def main() -> None:
    if not os.path.exists("heartbeat.json"):
        return
    with open("heartbeat.json", encoding="utf-8") as fh:
        ts = datetime.fromisoformat(json.load(fh)["ts"])
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > MAX_AGE_SECONDS:
        from services.telegram_bot import TelegramService
        from utils.helpers import load_config
        TelegramService(load_config()).send_error_alert(
            f"🧪 DEMO watchdog: heartbeat stale ({age:.0f}s). Restart the demo loop.")


if __name__ == "__main__":
    main()
