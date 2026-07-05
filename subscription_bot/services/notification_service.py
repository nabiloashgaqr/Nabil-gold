import logging
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
import config
from database import get_db

logger = logging.getLogger(__name__)

async def notify_admin_new_member(bot: Bot, subscriber: dict):
    """Admin notification – new member joined, needs duration"""
    try:
        chat_id = config.admin_destination()
        name = subscriber.get("full_name", "-")
        username = subscriber.get("telegram_username") or "no username"
        if username != "no username" and not username.startswith("@"):
            username = "@" + username
        tid = subscriber.get("telegram_id")
        sid = subscriber.get("id")
        text = (
            "🆕 <b>New member joined</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"👤 Name: {name}\n"
            f"🔗 Username: {username}\n"
            f"🆔 ID: <code>{tid}</code>\n\n"
            "<b>Set subscription duration:</b>"
        )
        keyboard = [
            [
                InlineKeyboardButton("1 Week", callback_data=f"dur:{sid}:7:day"),
                InlineKeyboardButton("1 Month", callback_data=f"dur:{sid}:1:month"),
            ],
            [
                InlineKeyboardButton("3 Months", callback_data=f"dur:{sid}:3:month"),
                InlineKeyboardButton("6 Months", callback_data=f"dur:{sid}:6:month"),
            ],
            [
                InlineKeyboardButton("1 Year", callback_data=f"dur:{sid}:1:year"),
                InlineKeyboardButton("Custom", callback_data=f"custom:{sid}"),
            ]
        ]
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(keyboard),
                               disable_web_page_preview=True)
        get_db().log_notification(sid, "new_member", chat_id, text)
    except Exception as e:
        logger.exception("notify_admin_new_member failed: %s", e)


async def notify_admin_expiring_3d(bot: Bot, sub: dict):
    chat_id = config.admin_destination()
    name = sub.get("full_name")
    username = sub.get("telegram_username") or ""
    if username and not username.startswith("@"):
        username = "@" + username
    expiry = sub.get("expiry_date")
    sid = sub.get("id")
    text = (
        "⏰ <b>Subscription expires in 3 days</b>\n"
        f"👤 {name} {username}\n"
        f"📅 Expiry: {expiry}\n"
        f"🆔 <code>{sub.get('telegram_id')}</code>"
    )
    keyboard = [[
        InlineKeyboardButton("🔄 Renew", callback_data=f"renew:{sid}"),
        InlineKeyboardButton("⏭ Ignore", callback_data=f"ignore:{sid}")
    ]]
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(keyboard))
        get_db().log_notification(sid, "before_3_days_admin", chat_id, text)
    except Exception:
        logger.exception("notify_admin_expiring_3d failed")


async def notify_admin_expiring_1d(bot: Bot, sub: dict):
    chat_id = config.admin_destination()
    text = (
        "⚠️ <b>URGENT – Subscription expires tomorrow!</b>\n"
        f"👤 {sub.get('full_name')} @{sub.get('telegram_username') or ''}\n"
        f"🆔 <code>{sub.get('telegram_id')}</code>\n"
        f"📅 {sub.get('expiry_date')}"
    )
    sid = sub.get("id")
    keyboard = [[
        InlineKeyboardButton("🔄 Renew now", callback_data=f"renew:{sid}"),
        InlineKeyboardButton("🚫 Auto-kick", callback_data=f"ignore:{sid}")
    ]]
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(keyboard))
        get_db().log_notification(sid, "before_1_day_admin", chat_id, text)
    except Exception:
        logger.exception("notify_admin_expiring_1d failed")


async def notify_admin_expired_kicked(bot: Bot, sub: dict):
    chat_id = config.admin_destination()
    sid = sub.get("id")
    text = (
        "❌ <b>Member auto-kicked – subscription expired</b>\n"
        f"👤 {sub.get('full_name')}\n"
        f"🆔 <code>{sub.get('telegram_id')}</code>\n"
        "Reason: subscription expired"
    )
    keyboard = [[InlineKeyboardButton("🔄 Renew & Re-invite", callback_data=f"reinvite:{sid}")]]
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(keyboard))
        get_db().log_notification(sid, "expired_admin", chat_id, text)
    except Exception:
        logger.exception("notify_admin_expired_kicked failed")


async def dm_subscriber(bot: Bot, telegram_id: int, text: str) -> bool:
    """Send DM to subscriber – returns True if delivered"""
    try:
        await bot.send_message(chat_id=telegram_id, text=text)
        return True
    except Exception as e:
        logger.warning("dm_subscriber failed %s -> %s", telegram_id, e)
        return False


async def notify_subscriber_3days(bot: Bot, sub: dict) -> bool:
    """Subscriber message #1 in lifetime – 3 days reminder – ENGLISH ONLY"""
    tid = int(sub.get("telegram_id"))
    expiry = sub.get("expiry_date")
    text = config.MESSAGES["sub_remind_3d"].format(expiry=expiry, admin=config.ADMIN_CONTACT)
    ok = await dm_subscriber(bot, tid, text)
    db = get_db()
    if ok:
        db.log_notification(sub["id"], "before_3_days_subscriber", tid, text)
    else:
        db.log_notification(sub["id"], "before_3_days_subscriber_failed", tid, "failed")
        # notify admin about delivery failure
        try:
            await bot.send_message(
                chat_id=config.admin_destination(),
                text=f"⚠️ Failed to send 3-day reminder to {sub.get('full_name')} (<code>{tid}</code>)\nUser has not activated bot (/start). Notify manually.\nContact: {config.ADMIN_CONTACT}",
                parse_mode="HTML"
            )
        except Exception:
            pass
    return ok


async def notify_subscriber_expired(bot: Bot, sub: Dict) -> bool:
    """Subscriber message #2 in lifetime – expired kicked – ENGLISH ONLY"""
    tid = int(sub.get("telegram_id"))
    text = config.MESSAGES["sub_expired_kicked"].format(admin=config.ADMIN_CONTACT)
    ok = await dm_subscriber(bot, tid, text)
    db = get_db()
    if ok:
        db.log_notification(sub["id"], "expired_subscriber", tid, text)
    else:
        db.log_notification(sub["id"], "expired_subscriber_failed", tid, "failed")
        try:
            await bot.send_message(
                chat_id=config.admin_destination(),
                text=f"⚠️ Could not notify kicked subscriber {sub.get('full_name')} (<code>{tid}</code>) about expiry.",
                parse_mode="HTML"
            )
        except Exception:
            pass
    return ok
