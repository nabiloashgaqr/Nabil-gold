import logging
from telegram import Bot
import config

logger = logging.getLogger(__name__)

async def kick_member(bot: Bot, user_id: int) -> bool:
    """
    يطرد العضو من القناة/المجموعة المستهدفة
    نستخدم ban ثم unban فوراً حتى يستطيع العودة بدعوة جديدة
    """
    chat_id = config.TARGET_CHAT_ID
    if not chat_id:
        logger.error("TARGET_CHAT_ID غير مضبوط")
        return False
    try:
        # ban
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        # unban فوراً للسماح بالعودة لاحقاً
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
        logger.info("kicked %s from %s (ban+unban)", user_id, chat_id)
        return True
    except Exception as e:
        logger.exception("kick_member failed %s: %s", user_id, e)
        return False
