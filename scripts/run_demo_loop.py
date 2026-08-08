"""Demo loop (demo/mt5 branch): 5-min cadence on the VPS + heartbeat."""
import json
import time
import traceback

from datetime import datetime, timezone

HEARTBEAT = "heartbeat.json"
CYCLE_SECONDS = 300


def _beat() -> None:
    with open(HEARTBEAT, "w", encoding="utf-8") as fh:
        json.dump({"ts": datetime.now(timezone.utc).isoformat()}, fh)


def main() -> None:
    from scripts.run_trade_updates import main as updates_main
    while True:
        try:
            updates_main()
        except Exception:
            traceback.print_exc()
        _beat()
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
