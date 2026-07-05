import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import get_db
import config

logger = logging.getLogger(__name__)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /start – mandatory activation button for new users only """
    user = update.effective_user
    if not user:
        return
    db = get_db()
    sub = None
    try:
        sub = db.get_subscriber_by_tid(user.id)
    except Exception:
        pass

    can_dm = bool(sub and sub.get("can_dm"))
    # If already activated – silent ✅ only
    if can_dm:
        try:
            await update.message.reply_text(config.MESSAGES["already_activated"])
        except Exception:
            pass
        return

    # New user – show mandatory activation button
    keyboard = [[InlineKeyboardButton(config.MESSAGES["activate_button"], callback_data=f"activate:{user.id}")]]
    try:
        await update.message.reply_text(
            config.MESSAGES["start_need_activate"],
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception:
        # fallback to simple ✅ per golden rule
        try:
            await update.message.reply_text(config.MESSAGES["start_ok"])
        except Exception:
            pass

async def activate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle mandatory activation button – allowed for everyone (not admin-only)"""
    query = update.callback_query
    if not query or not query.data.startswith("activate:"):
        return
    user = query.from_user
    try:
        # extract target id from callback to prevent others activating for someone else?
        # Format: activate:<telegram_id>
        parts = query.data.split(":")
        target_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else user.id
        # only allow self-activation or admin override
        if user.id != target_id and not config.is_admin(user.id):
            await query.answer("Not allowed", show_alert=False)
            return
        # update DB
        db = get_db()
        db.set_can_dm(target_id, True)
        await query.answer("Activated")
        await query.edit_message_text(config.MESSAGES["activated_ok"])
    except Exception as e:
        logger.exception("activate_callback failed: %s", e)
        try:
            await query.answer("Error", show_alert=True)
        except Exception:
            pass

async def silent_drop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Silent drop – complete ignore for non-admins – golden rule"""
    user = update.effective_user
    if not user:
        return
    if config.is_admin(user.id):
        return  # let admin handlers process
    # non-admin: complete silence – no reply, no log spam
    return

# Handlers export
# /start – must be private chat only
start_handler = CommandHandler("start", start_cmd, filters=filters.ChatType.PRIVATE)

# activation callback – MUST run before admin callback_handler (higher priority / lower group)
activate_callback_handler = CallbackQueryHandler(activate_callback, pattern=r"^activate:")

# silent drop – lowest priority
silent_message_handler = MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, silent_drop)
silent_command_handler = MessageHandler(filters.ChatType.PRIVATE & filters.COMMAND, silent_drop)
