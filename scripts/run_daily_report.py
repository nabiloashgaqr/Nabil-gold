"""سكريبت التقرير اليومي.

يعمل يومياً عبر GitHub Actions الساعة 23:00 UTC، يجمع صفقات اليوم من Supabase
ويرسل تقرير الأداء إلى Telegram.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.daily_report_agent import DailyReportAgent
from services.database import DatabaseService
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    """Generate and send daily report."""
    logger.info("بدء التقرير اليومي: %s", datetime.now(timezone.utc).isoformat())
    config = load_config()
    telegram = TelegramService(config)
    try:
        database = DatabaseService(config)
        trades = database.get_today_trades()
        report = DailyReportAgent(config).generate(trades)
        telegram.send_daily_report(report["text"])
        logger.info("تم إرسال التقرير اليومي. عدد الصفقات: %s", len(trades))
    except Exception as exc:  # noqa: BLE001
        logger.exception("خطأ في التقرير اليومي")
        telegram.send_error_alert(str(exc))


if __name__ == "__main__":
    main()
