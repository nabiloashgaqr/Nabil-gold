"""Poll Telegram for user commands and reply (runs on a schedule).

Because the project runs on GitHub Actions (no always-on server), this script
pulls pending updates via getUpdates and answers them. Run it frequently (e.g.
every 1-2 minutes) for near-real-time replies.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import DatabaseService
from services.telegram_bot import TelegramService
from services.telegram_commands import poll_and_handle
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("بدء معالجة أوامر Telegram: %s", datetime.now(timezone.utc).isoformat())
    config = load_config()
    telegram = TelegramService(config)
    database = DatabaseService(config)
    try:
        handled = poll_and_handle(telegram, database, config)
        logger.info("تمت معالجة %s أمر/أوامر", handled)
    except Exception as exc:  # noqa: BLE001
        logger.exception("فشل معالجة الأوامر: %s", exc)


if __name__ == "__main__":
    main()
