"""Import analyst labels from JSON or CSV into analyst_labels storage/table."""

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

    file_path = os.environ.get("ANALYST_LABELS_FILE") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not file_path:
        raise SystemExit("Usage: python scripts/import_analyst_labels.py <labels.json|labels.csv>")

    symbol = os.environ.get("ANALYST_LABELS_SYMBOL") or None
    analyst_name = os.environ.get("ANALYST_NAME") or None
    summary = service.import_labels_from_file(file_path, default_symbol=symbol, analyst_name=analyst_name)

    output_path = Path(__file__).resolve().parents[1] / "storage" / "analyst_import_summary.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Imported %s analyst labels from %s", summary.get("count"), file_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
