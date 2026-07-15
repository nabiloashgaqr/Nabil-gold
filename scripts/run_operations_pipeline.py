"""Run the full operational pipeline in one command.

This is the recommended end-state operator workflow:
- fetch candles once
- run final evaluation
- run tuning advisor
- run release readiness
- save all three artifacts together
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
from services.operations_pipeline import OperationsPipeline
from services.release_readiness import ReleaseReadinessService
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SmartSignal operational pipeline")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--outputsize", type=int, default=420)
    parser.add_argument("--window", type=int, default=160)
    parser.add_argument("--step", type=int, default=12)
    parser.add_argument("--horizon", type=int, default=32)
    parser.add_argument("--max-trades", type=int, default=60)
    parser.add_argument("--output-root", default="storage/ops_pipeline")
    parser.add_argument("--send-telegram", action="store_true", default=False)
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    config = load_config()
    db = DatabaseService(config)
    payload = MarketDataService(config).get_ohlcv(timeframe=args.timeframe, outputsize=args.outputsize)
    candles = payload.get("data", []) if payload else []

    pipeline = OperationsPipeline(config, database=db)
    bundle = pipeline.run(
        candles,
        window=args.window,
        step=args.step,
        horizon=args.horizon,
        max_trades=args.max_trades,
    )
    paths = pipeline.save_bundle(bundle, root=args.output_root)

    readiness_text = ReleaseReadinessService(config, database=db).format_telegram(bundle["release_readiness"])
    print(readiness_text.replace("<b>", "").replace("</b>", ""))
    print("Saved artifacts:")
    for key, value in paths.items():
        print(f"- {key}: {value}")

    should_send = args.send_telegram or os.environ.get("GITHUB_ACTIONS") == "true"
    if should_send:
        TelegramService(config).send_message(readiness_text)


if __name__ == "__main__":
    main()
