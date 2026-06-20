"""Weekly AI Performance Report — entry point.

Runs every Sunday at 23:30 Asia/Hebron via GitHub Actions.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ai_service import get_ai_service
from services.database import DatabaseService
from services.telegram_bot import TelegramService
from services.weekly_report import WeeklyReportService
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def _should_run_now(config: dict) -> bool:
    """Run if today matches configured day_of_week (default Sunday = 6)."""
    wr = config.get("weekly_report") or {}
    target_day = int(wr.get("day_of_week", 6))  # 0=Mon ... 6=Sun
    tz_name = str(wr.get("timezone") or config.get("schedule", {}).get("timezone") or "Asia/Hebron")
    try:
        local_now = datetime.now(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001
        local_now = datetime.now()
    # Python: Monday=0 ... Sunday=6. Config uses same convention.
    return local_now.weekday() == target_day


async def main_async() -> int:
    config = load_config()
    wr_cfg = config.get("weekly_report") or {}
    if not bool(wr_cfg.get("enabled", False)):
        logger.info("weekly_report.enabled=false — exiting.")
        return 0
    if not _should_run_now(config):
        logger.info("Not the configured day_of_week — exiting.")
        return 0

    telegram = TelegramService(config)
    database = DatabaseService(config)

    ai_service = None
    try:
        ai_service = get_ai_service(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI service init failed: %s (continuing without Groq)", exc)

    service = WeeklyReportService(
        config=config,
        database=database,
        ai_service=ai_service,
        telegram=telegram,
    )

    try:
        result = await service.generate_report()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Weekly report generation failed")
        telegram.send_error_alert(f"Weekly report failed: {exc}")
        return 1

    logger.info(
        "Weekly report status=%s, chars=%d, recs=%d, tokens=%s",
        result.get("status"),
        len(result.get("report_text", "")),
        len(result.get("recommendations", [])),
        result.get("tokens_used", "n/a"),
    )

    if wr_cfg.get("send_telegram", True):
        service.send_to_telegram(result.get("report_text", ""))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
