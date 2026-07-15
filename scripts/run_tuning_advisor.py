"""Run tuning advisor against a final evaluation report.

If the report file does not exist, this script can optionally generate it first.
That makes manual GitHub workflow runs reliable even on a clean runner.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService
from services.final_evaluation import FinalEvaluationService
from services.market_data import MarketDataService
from services.tuning_advisor import TuningAdvisor
from utils.helpers import load_config, setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run tuning advisor from final evaluation report")
    parser.add_argument("--input", default="storage/final_evaluation.json")
    parser.add_argument("--output", default="storage/tuning_advice.json")
    parser.add_argument("--ensure-final-evaluation", action="store_true", default=False)
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--outputsize", type=int, default=420)
    parser.add_argument("--window", type=int, default=160)
    parser.add_argument("--step", type=int, default=12)
    parser.add_argument("--horizon", type=int, default=32)
    parser.add_argument("--max-trades", type=int, default=60)
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    config = load_config()
    report_path = Path(args.input)
    if not report_path.exists():
        if not args.ensure_final_evaluation:
            raise SystemExit(f"Final evaluation file not found: {report_path}")
        db = DatabaseService(config)
        payload = MarketDataService(config).get_ohlcv(timeframe=args.timeframe, outputsize=args.outputsize)
        candles = payload.get("data", []) if payload else []
        report = FinalEvaluationService(config, database=db).run(
            candles,
            window=args.window,
            step=args.step,
            horizon=args.horizon,
            max_trades=args.max_trades,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    advice = TuningAdvisor(config).build_advice(report)
    output_path = TuningAdvisor(config).save(advice, args.output)
    print(json.dumps(advice, ensure_ascii=False, indent=2))
    print(f"Tuning advice saved to: {output_path}")


if __name__ == "__main__":
    main()
