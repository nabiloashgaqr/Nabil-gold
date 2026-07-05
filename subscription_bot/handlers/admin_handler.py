import logging
from datetime import date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
import config
from database import get_db
from services.kick_service import kick_member
from services.invite_service import create_one_use_invite

logger = logging.getLogger(__name__)

ADMIN_PANEL_TEXT = (
    "🛠 <b>Subscription Admin Panel</b>\n"
    "━━━━━━━━━━━━━━\n"
    "Nabil Gold – Private Channel Manager\n"
    f"Admin: {config.ADMIN_CONTACT}"
)

def build_admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Pending Duration", callback_data="admin_pending"),
            InlineKeyboardButton("✅ Active", callback_data="admin_active"),
        ],
        [
            InlineKeyboardButton("⏰ Expiring Soon", callback_data="admin_expiring"),
            InlineKeyboardButton("❌ Expired", callback_data="admin_expired"),
        ],
        [
            InlineKeyboardButton("🔄 Renew", callback_data="admin_renew_menu"),
            InlineKeyboardButton("✏️ Edit Expiry", callback_data="admin_edit_menu"),
        ],
        [
            InlineKeyboardButton("🗑 Delete", callback_data="admin_delete_menu"),
            InlineKeyboardButton("🚫 Kick Manual", callback_data="admin_kick_menu"),
        ],
        [
            InlineKeyboardButton("🔍 Search", callback_data="admin_search"),
            InlineKeyboardButton("📊 Report", callback_data="admin_report"),
        ],
    ])

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return  # silent per golden rule
    await update.message.reply_text(
        ADMIN_PANEL_TEXT,
        parse_mode="HTML",
        reply_markup=build_admin_keyboard()
    )

# Text commands – admin private only
async def admin_text_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not config.is_admin(user.id):
        return
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    db = get_db()

    # custom_<sid>_<days>
    if text.startswith("custom_"):
        try:
            _, sid, days_s = text.split("_", 2)
            days = int(days_s)
            exp = db.set_subscription(sid, days, "day")
            await update.message.reply_text(f"✅ Activated – expires {exp}", parse_mode="HTML")
            return
        except Exception as e:
            await update.message.reply_text(f"❌ Invalid format: {e}\nUse: custom_<subscriber_id>_<days>")
            return

    # renew_<subscriber_id>_<days>
    if text.startswith("renew_"):
        try:
            parts = text.split("_")
            sid = parts[1]
            days = int(parts[2]) if len(parts) > 2 else 30
            exp = db.set_subscription(sid, days, "day")
            sub = db.get_by_id(sid)
            link = None
            if sub:
                link = await create_one_use_invite(context.bot, sub.get("full_name","renew"))
            msg = f"✅ Renewed – expires {exp}"
            if link:
                msg += f"\n🔗 Invite: {link}"
            await update.message.reply_text(msg)
            return
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")
            return

    # edit_<subscriber_id>_YYYY-MM-DD
    if text.startswith("edit_"):
        try:
            _, sid, datestr = text.split("_", 2)
            new_exp = date.fromisoformat(datestr)
            ok = db.update_expiry(sid, new_exp)
            await update.message.reply_text("✅ Updated" if ok else "❌ Failed")
            return
        except Exception as e:
            await update.message.reply_text(f"❌ Format: edit_<id>_YYYY-MM-DD\n{e}")
            return

    # delete_<subscriber_id>
    if text.startswith("delete_"):
        try:
            sid = text.split("_",1)[1]
            ok = db.delete_subscriber(sid)
            await update.message.reply_text("🗑 Deleted" if ok else "❌ Failed")
            return
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")
            return

    # kick_<telegram_id>
    if text.startswith("kick_"):
        try:
            tid = int(text.split("_",1)[1])
            ok = await kick_member(context.bot, tid)
            await update.message.reply_text("🚫 Kicked" if ok else "❌ Kick failed")
            return
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")
            return

    # search ...
    if text.lower().startswith("search "):
        q = text[7:].strip()
        res = db.search_subscribers(q)
        if not res:
            await update.message.reply_text("No results.")
            return
        lines = [f"🔍 Results ({len(res)}):\n"]
        for r in res[:20]:
            lines.append(
                f"• {r.get('full_name')} @{r.get('telegram_username') or ''} – "
                f"{r.get('status')} – exp {r.get('expiry_date')}\n"
                f"ID: <code>{r.get('id')}</code>  TID: <code>{r.get('telegram_id')}</code>"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    # unknown admin text – silent ignore per golden rule for non-command? Actually admin – give hint
    # We keep silent to avoid noise – comment out
    return

admin_command_handler = CommandHandler("admin", admin_cmd, filters=filters.ChatType.PRIVATE)
admin_text_handler = MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & filters.User(user_id=config.ADMIN_IDS), admin_text_commands)
