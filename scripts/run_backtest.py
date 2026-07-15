"""Run a lightweight XAU/USD backtest and send/report results.

Default mode uses Twelve Data for live prices, otherwise synthetic demo
candles.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.backtesting import BacktestEngine, benchmark_backtests, format_backtest_telegram, save_backtest_report, save_backtest_csv
from services.market_data import MarketDataService
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Gold AI Signals backtest")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--outputsize", type=int, default=420)
    parser.add_argument("--window", type=int, default=160)
    parser.add_argument("--step", type=int, default=12)
    parser.add_argument("--horizon", type=int, default=32)
    parser.add_argument("--max-trades", type=int, default=60)
    parser.add_argument("--send-telegram", action="store_true", default=False)
    parser.add_argument("--benchmark", action="store_true", default=False, help="Run current engine vs baseline benchmark")
    parser.add_argument("--output", default="storage/backtest_report.json")
    parser.add_argument("--csv-output", default="storage/backtest_trades.csv")
    parser.add_argument("--benchmark-output", default="storage/backtest_benchmark.json")
    return parser.parse_args()


def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    args = parse_args()
    config = load_config()

    logger.info("Fetching candles for backtest: timeframe=%s outputsize=%s", args.timeframe, args.outputsize)
    market_data = MarketDataService(config)
    payload = market_data.get_ohlcv(timeframe=args.timeframe, outputsize=args.outputsize)
    candles = payload.get("data", []) if payload else []
    logger.info("Backtest candles loaded: %s | source=%s", len(candles), payload.get("source") if payload else "none")

    if args.benchmark:
        report = benchmark_backtests(
            config,
            candles,
            window=args.window,
            step=args.step,
            horizon=args.horizon,
            max_trades=args.max_trades,
        )
        report_path = save_backtest_report(report, args.benchmark_output)
        csv_path = Path(args.csv_output)
    else:
        engine = BacktestEngine(config, candles)
        report = engine.run(window=args.window, step=args.step, horizon=args.horizon, max_trades=args.max_trades)
        report_path = save_backtest_report(report, args.output)
        csv_path = save_backtest_csv(report, args.csv_output)
    summary_text = format_backtest_telegram(report)

    print(summary_text.replace("<b>", "").replace("</b>", ""))
    print(f"Report saved to: {report_path}")
    if not args.benchmark:
        print(f"CSV saved to: {csv_path}")

    should_send = args.send_telegram or os.environ.get("GITHUB_ACTIONS") == "true"
    if should_send:
        TelegramService(config).send_message(summary_text)


if __name__ == "__main__":
    main()
