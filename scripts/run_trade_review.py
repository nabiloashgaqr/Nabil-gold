"""Run Groq AI reviews for recently closed losing trades."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import DatabaseService
from services.telegram_bot import TelegramService
from services.trade_review import format_trade_review_summary, run_trade_review
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("بدء AI Trade Review: %s", datetime.now(timezone.utc).isoformat())
    config = load_config()
    db = DatabaseService(config)
    telegram = TelegramService(config)
    try:
        if not config.get("ai_trade_review", {}).get("enabled", True):
            logger.info("AI Trade Review disabled")
            return
        result = run_trade_review(db, config)
        text = format_trade_review_summary(result)
        telegram.send_message(text)
        logger.info("AI Trade Review completed: %s reviewed, %s errors", len(result.get("reviewed", [])), len(result.get("errors", [])))
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI Trade Review failed")
        telegram.send_error_alert(f"AI Trade Review failed: {exc}")
        raise


if __name__ == "__main__":
    main()
