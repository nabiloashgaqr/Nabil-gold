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


def _quiet_mode() -> bool:
    """In the consolidated end-of-day digest, don't send a standalone message."""
    return os.environ.get("EOD_QUIET", "").lower() in {"1", "true", "yes"}


def _write_eod_section(name: str, text: str) -> None:
    """Persist a section so the consolidated daily report can merge it."""
    try:
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "storage"
        root.mkdir(parents=True, exist_ok=True)
        (root / f"eod_{name}.txt").write_text(text or "", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not persist EOD section %s: %s", name, exc)


def main() -> str | None:
    """Run the AI trade review. Returns the summary text (for the digest)."""
    logger.info("بدء AI Trade Review: %s", datetime.now(timezone.utc).isoformat())
    config = load_config()
    db = DatabaseService(config)
    telegram = TelegramService(config)
    try:
        if not config.get("ai_trade_review", {}).get("enabled", True):
            logger.info("AI Trade Review disabled")
            return None
        result = run_trade_review(db, config)
        text = format_trade_review_summary(result)
        if _quiet_mode():
            logger.info("🔇 EOD_QUIET: AI review message merged into daily report")
            _write_eod_section("review", text)
        else:
            telegram.send_message(text)
        logger.info("AI Trade Review completed: %s reviewed, %s errors", len(result.get("reviewed", [])), len(result.get("errors", [])))
        return text
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI Trade Review failed")
        if not _quiet_mode():
            telegram.send_error_alert(f"AI Trade Review failed: {exc}")
        if not _quiet_mode():
            raise
        return None


if __name__ == "__main__":
    main()
