"""Run tuning advisor against a final evaluation report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.tuning_advisor import TuningAdvisor
from utils.helpers import load_config, setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run tuning advisor from final evaluation report")
    parser.add_argument("--input", default="storage/final_evaluation.json")
    parser.add_argument("--output", default="storage/tuning_advice.json")
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    config = load_config()
    report_path = Path(args.input)
    if not report_path.exists():
        raise SystemExit(f"Final evaluation file not found: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    advice = TuningAdvisor(config).build_advice(report)
    output_path = TuningAdvisor(config).save(advice, args.output)
    print(json.dumps(advice, ensure_ascii=False, indent=2))
    print(f"Tuning advice saved to: {output_path}")


if __name__ == "__main__":
    main()
