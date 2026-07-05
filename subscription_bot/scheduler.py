import logging
from datetime import datetime
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application
from database import get_db
from services.notification_service import (
    notify_admin_expiring_3d, notify_subscriber_3days,
    notify_admin_expiring_1d, notify_admin_expired_kicked,
    notify_subscriber_expired
)
from services.kick_service import kick_member
import config

logger = logging.getLogger(__name__)

async def check_expirations(app: Application):
    """يشيك: المنتهون → قبل يوم → قبل 3 أيام"""
    db = get_db()
    bot = app.bot
    logger.info("Scheduler running expiration check...")

    # 1) المنتهون اليوم أو قبل – اطرد فوراً
    expired_list = db.get_expired_today_or_before()
    for sub in expired_list:
        sid = sub["id"]
        tid = int(sub["telegram_id"])
        # منع التكرار
        if db.has_notification(sid, "kicked"):
            continue
        # طرد
        kicked = await kick_member(bot, tid)
        if kicked:
            db.mark_expired_kicked(sid)
            db.log_notification(sid, "kicked", tid, "auto kicked on expiry")
            # notify admin + subscriber
            await notify_admin_expired_kicked(bot, sub)
            await notify_subscriber_expired(bot, sub)
        else:
            logger.warning("failed to kick %s", tid)

    # 2) قبل يوم واحد
    one_day = db.get_expiring_in_days(1)
    for sub in one_day:
        sid = sub["id"]
        if db.has_notification(sid, "before_1_day_admin"):
            continue
        await notify_admin_expiring_1d(bot, sub)

    # 3) قبل 3 أيام
    three_day = db.get_expiring_in_days(3)
    for sub in three_day:
        sid = sub["id"]
        # admin
        if not db.has_notification(sid, "before_3_days_admin"):
            await notify_admin_expiring_3d(bot, sub)
        # subscriber – مرة واحدة فقط
        if not db.has_notification(sid, "before_3_days_subscriber") and not db.has_notification(sid, "before_3_days_subscriber_failed"):
            await notify_subscriber_3days(bot, sub)

    logger.info("Scheduler check done. expired=%d, 1d=%d, 3d=%d",
                len(expired_list), len(one_day), len(three_day))


def setup_scheduler(app: Application) -> AsyncIOScheduler:
    tz = pytz.timezone(config.TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    # كل 6 ساعات
    scheduler.add_job(
        check_expirations,
        "interval",
        hours=6,
        args=[app],
        id="expiry_check",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(tz)
    )
    logger.info("Scheduler configured – every 6 hours – tz %s", config.TIMEZONE)
    return scheduler
