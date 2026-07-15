"""Run batch analyst-vs-bot setup comparison and write a summary artifact."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.analyst_distillation import AnalystDistillationService
from services.database import DatabaseService
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    config = load_config()
    db = DatabaseService(config)
    service = AnalystDistillationService(db, config)

    symbol = os.environ.get("COMPARE_SYMBOL") or config.get("symbol") or "XAU/USD"
    limit = int(os.environ.get("COMPARE_LIMIT", "50") or 50)
    summary = service.compare_recent(symbol=symbol, limit=limit)

    output_path = Path(__file__).resolve().parents[1] / "storage" / "analyst_comparison_summary.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "Analyst comparison complete for %s: labels=%s matched=%s partial=%s missed=%s extra=%s",
        symbol,
        summary.get("labels_considered", 0),
        summary.get("matched_labels", 0),
        summary.get("partial_matches", 0),
        summary.get("missed_labels", 0),
        summary.get("extra_bot_setups", 0),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
