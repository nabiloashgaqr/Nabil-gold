import logging
from telegram import Update
from telegram.ext import ContextTypes, ChatMemberHandler
from database import get_db
from services.notification_service import notify_admin_new_member
import config

logger = logging.getLogger(__name__)

async def chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يلتقط دخول عضو جديد للقناة/المجموعة المستهدفة"""
    if not update.chat_member:
        return
    cm = update.chat_member
    chat = cm.chat
    # فقط القناة المستهدفة
    if chat.id != config.TARGET_CHAT_ID:
        return

    old_status = cm.old_chat_member.status
    new_status = cm.new_chat_member.status
    user = cm.new_chat_member.user

    # عضو دخل: كان left/kicked/banned → أصبح member/administrator/restricted
    joined_statuses = {"member", "administrator", "restricted"}
    left_statuses = {"left", "kicked", "banned"}

    if new_status in joined_statuses and old_status in left_statuses:
        # تجاهل البوتات
        if user.is_bot:
            return
        full_name = (user.full_name or "").strip() or f"user_{user.id}"
        username = user.username
        tid = user.id
        logger.info("New member detected: %s (%s)", full_name, tid)
        db = get_db()
        sub = db.upsert_on_join(
            telegram_id=tid,
            full_name=full_name,
            username=username
        )
        # أرسل تنبيه للإدارة فقط
        try:
            # نحتاج تمرير bot – نستخدم context.bot
            await notify_admin_new_member(context.bot, sub if isinstance(sub, dict) else {
                "id": sub.get("id") if isinstance(sub, dict) else None,
                "full_name": full_name,
                "telegram_username": username,
                "telegram_id": tid
            })
        except Exception:
            logger.exception("notify admin new member failed")

# للـ Application
member_handler = ChatMemberHandler(chat_member_update, ChatMemberHandler.CHAT_MEMBER)
