"""Demo loop (demo/mt5 branch): 5-min bookkeeping cadence on the VPS + heartbeat.

Runs as ONE persistent process (SS_DemoLoop, ONLOGON). The pidfile guard
makes re-launches harmless instead of stacking duplicate loops.
"""
import json
import time
import traceback

from datetime import datetime, timezone

# --- VPS: load .env if present (real env vars ALWAYS win over .env) ---
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()  # override=False: task-wrapper vars take precedence
except Exception:
    pass

HEARTBEAT = "heartbeat.json"
PIDFILE = "demo_loop.pid"
CYCLE_SECONDS = 300
START_DELAY_SECONDS = 90  # let the MT5 terminal finish logging in first


def _beat() -> None:
    with open(HEARTBEAT, "w", encoding="utf-8") as fh:
        json.dump({"ts": datetime.now(timezone.utc).isoformat()}, fh)


def main() -> None:
    from utils.single_instance import acquire_single_instance
    if not acquire_single_instance(PIDFILE):
        print("demo loop already running; exiting duplicate instance.")
        return
    time.sleep(START_DELAY_SECONDS)
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
