import logging
from datetime import datetime, timedelta
from telegram import Bot
import config

logger = logging.getLogger(__name__)

async def create_one_use_invite(bot: Bot, name: str = None) -> str | None:
    """ينشئ رابط دعوة لمرة واحدة صالح 24 ساعة"""
    try:
        chat_id = config.TARGET_CHAT_ID
        expire = datetime.utcnow() + timedelta(hours=24)
        link = await bot.create_chat_invite_link(
            chat_id=chat_id,
            expire_date=expire,
            member_limit=1,
            creates_join_request=False,
            name=(name or "renew")[:32]
        )
        return link.invite_link
    except Exception as e:
        logger.exception("create_one_use_invite failed: %s", e)
        return None
