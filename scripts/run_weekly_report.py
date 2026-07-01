"""Weekly Performance Report — entry point.

Runs every Saturday at 10:00 local time (Asia/Hebron / Asia-Jerusalem) via
GitHub Actions.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import DatabaseService
from services.llm_review import get_gemini_review_service
from services.telegram_bot import TelegramService
from services.weekly_report import WeeklyReportService
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def _should_run_now(config: dict) -> bool:
    """Run if today matches configured day_of_week (default Saturday = 5)."""
    wr = config.get("weekly_report") or {}
    target_day = int(wr.get("day_of_week", 5))  # 0=Mon ... 5=Sat ... 6=Sun
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

    service = WeeklyReportService(
        config=config,
        database=database,
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

    final_report_text = result.get("report_text", "")
    try:
        gemini = get_gemini_review_service(config)
        if not gemini.enabled:
            logger.info("🧠 Gemini weekly review skipped: API key not configured")
        else:
            weekly_review = gemini.summarize_weekly_report({
                "period": result.get("period") or result.get("week_range") or "weekly",
                "headline": result.get("headline") or "Weekly performance review",
                "stats": result.get("stats") or {},
                "recommendations": result.get("recommendations") or [],
                "report_excerpt": result.get("report_text", ""),
                "time_of_week_breakdown": result.get("time_of_week_breakdown") or {},
                "rr_distribution": result.get("rr_distribution") or {},
                "closed_trades_sample": result.get("closed_trades_sample") or [],
                "environment_fit": result.get("environment_fit") or {},
            })
            if weekly_review.get("available"):
                lines = [final_report_text, "", "🧠 <b>Gemini Independent Weekly Strategic Review</b>"]
                
                if weekly_review.get("edge_efficiency"):
                    lines.append(f"• Edge Efficiency: {weekly_review.get('edge_efficiency')}")
                if weekly_review.get("market_regime"):
                    lines.append(f"• Market Regime: {weekly_review.get('market_regime')}")
                
                # Short bullets only
                strategic_points = weekly_review.get("strategic_points") or []
                for p in strategic_points[:3]:
                    lines.append(f"• {p}")
                    
                if weekly_review.get("strategic_pivot"):
                    lines.append(f"• Strategic Pivot: {weekly_review.get('strategic_pivot')}")
                    
                final_report_text = "\n".join(lines)
                result["report_text"] = final_report_text
                result["gemini_weekly_review"] = weekly_review
                service._save(result)
                service.save_to_database(result)
                logger.info(
                    "🧠 Gemini weekly review added and persisted: points=%d quality=%s",
                    len(strategic_points), weekly_review.get("quality", "ok")
                )
            elif weekly_review.get("suppressed"):
                logger.info("🧠 Gemini weekly review suppressed: %s", weekly_review.get("suppress_reason", "generic"))
            else:
                logger.warning("🧠 Gemini weekly review unavailable: %s", weekly_review.get("summary") or weekly_review.get("reason"))
    except Exception as gemini_exc:
        logger.warning("Gemini weekly report skipped: %s", gemini_exc)

    if wr_cfg.get("send_telegram", True):
        service.send_to_telegram(final_report_text)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
