"""Run the final release-readiness pass.

This orchestrates:
1) final evaluation
2) tuning advisor
3) release readiness decision
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService
from services.market_data import MarketDataService
from services.release_readiness import ReleaseReadinessService
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run release readiness pass")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--outputsize", type=int, default=420)
    parser.add_argument("--window", type=int, default=160)
    parser.add_argument("--step", type=int, default=12)
    parser.add_argument("--horizon", type=int, default=32)
    parser.add_argument("--max-trades", type=int, default=60)
    parser.add_argument("--output", default="storage/release_readiness.json")
    parser.add_argument("--send-telegram", action="store_true", default=False)
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    config = load_config()
    db = DatabaseService(config)
    payload = MarketDataService(config).get_ohlcv(timeframe=args.timeframe, outputsize=args.outputsize)
    candles = payload.get("data", []) if payload else []
    service = ReleaseReadinessService(config, database=db)
    report = service.run(
        candles,
        window=args.window,
        step=args.step,
        horizon=args.horizon,
        max_trades=args.max_trades,
    )
    path = service.save(report, args.output)
    text = service.format_telegram(report)
    print(text.replace("<b>", "").replace("</b>", ""))
    print(f"Release readiness saved to: {path}")

    should_send = args.send_telegram or os.environ.get("GITHUB_ACTIONS") == "true"
    if should_send:
        TelegramService(config).send_message(text)


if __name__ == "__main__":
    main()
