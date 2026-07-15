"""Run the final evaluation pass: benchmark + analyst overlap + recommendations."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService
from services.final_evaluation import FinalEvaluationService
from services.market_data import MarketDataService
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run final evaluation pass")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--outputsize", type=int, default=420)
    parser.add_argument("--window", type=int, default=160)
    parser.add_argument("--step", type=int, default=12)
    parser.add_argument("--horizon", type=int, default=32)
    parser.add_argument("--max-trades", type=int, default=60)
    parser.add_argument("--output", default="storage/final_evaluation.json")
    parser.add_argument("--send-telegram", action="store_true", default=False)
    return parser.parse_args()


def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    args = parse_args()
    config = load_config()
    db = DatabaseService(config)

    logger.info("Fetching candles for final evaluation: timeframe=%s outputsize=%s", args.timeframe, args.outputsize)
    market_data = MarketDataService(config)
    payload = market_data.get_ohlcv(timeframe=args.timeframe, outputsize=args.outputsize)
    candles = payload.get("data", []) if payload else []
    logger.info("Final evaluation candles loaded: %s | source=%s", len(candles), payload.get("source") if payload else "none")

    service = FinalEvaluationService(config, database=db)
    report = service.run(
        candles,
        window=args.window,
        step=args.step,
        horizon=args.horizon,
        max_trades=args.max_trades,
    )
    output_path = service.save(report, args.output)
    summary_text = service.format_telegram(report)

    print(summary_text.replace("<b>", "").replace("</b>", ""))
    print(f"Final evaluation saved to: {output_path}")

    should_send = args.send_telegram or os.environ.get("GITHUB_ACTIONS") == "true"
    if should_send:
        TelegramService(config).send_message(summary_text)


if __name__ == "__main__":
    main()
