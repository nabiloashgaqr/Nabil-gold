import logging
from datetime import date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from database import get_db
import config
from services.invite_service import create_one_use_invite
from handlers.admin_handler import build_admin_keyboard, ADMIN_PANEL_TEXT

logger = logging.getLogger(__name__)

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    user = query.from_user
    # admin only – activation callbacks are handled separately
    if not config.is_admin(user.id):
        try:
            await query.answer()
        except Exception:
            pass
        return

    data = query.data or ""
    await query.answer()
    db = get_db()

    try:
        if data.startswith("dur:"):
            # dur:<subscriber_id>:<amount>:<type>
            _, sid, amount_s, dtype = data.split(":", 3)
            amount = int(amount_s)
            expiry = db.set_subscription(sid, amount, dtype)
            sub = db.get_by_id(sid)
            name = sub.get("full_name") if sub else "Subscriber"
            exp_str = expiry.isoformat() if expiry else "?"
            await query.edit_message_text(
                f"✅ Subscription activated\n👤 {name}\n📅 Expires: {exp_str}\n⏱ Duration: {amount} {dtype}",
                parse_mode="HTML"
            )
            return

        if data.startswith("custom:"):
            sid = data.split(":",1)[1]
            context.user_data["await_custom_for"] = sid
            await query.edit_message_text(
                "✏️ Send custom duration now:\n\n"
                f"<code>custom_{sid}_30</code>\n\n"
                "30 = days.\nExample: <code>custom_{sid}_45</code> = 45 days".replace("{sid}", sid),
                parse_mode="HTML"
            )
            return

        if data.startswith("renew:"):
            sid = data.split(":",1)[1]
            keyboard = [
                [
                    InlineKeyboardButton("1 Month", callback_data=f"dur:{sid}:1:month"),
                    InlineKeyboardButton("3 Months", callback_data=f"dur:{sid}:3:month"),
                ],
                [
                    InlineKeyboardButton("6 Months", callback_data=f"dur:{sid}:6:month"),
                    InlineKeyboardButton("1 Year", callback_data=f"dur:{sid}:1:year"),
                ],
                [InlineKeyboardButton("Custom", callback_data=f"custom:{sid}")]
            ]
            await query.edit_message_text(
                "🔄 Choose renewal duration:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        if data.startswith("ignore:"):
            await query.edit_message_text("⏭ Ignored.")
            return

        # Admin panel routing
        if data == "admin_pending":
            subs = db.get_pending()
            if not subs:
                await query.edit_message_text("✅ No pending duration subscribers.")
                return
            lines = ["📋 <b>Pending Duration</b>\n"]
            kb = []
            for s in subs[:15]:
                name = s.get("full_name")
                tid = s.get("telegram_id")
                sid = s.get("id")
                lines.append(f"• {name} – <code>{tid}</code>")
                kb.append([InlineKeyboardButton(f"Set duration – {name[:20]}", callback_data=f"renew:{sid}")])
            await query.edit_message_text("\n".join(lines), parse_mode="HTML",
                                          reply_markup=InlineKeyboardMarkup(kb) if kb else None)
            return

        if data == "admin_active":
            subs = db.get_active()
            lines = [f"✅ <b>Active ({len(subs)})</b>\n"]
            for s in subs[:25]:
                lines.append(f"• {s.get('full_name')} – expires {s.get('expiry_date')}")
            if len(subs) > 25:
                lines.append(f"... and {len(subs)-25} more")
            await query.edit_message_text("\n".join(lines), parse_mode="HTML")
            return

        if data == "admin_expiring":
            soon = []
            for d in [1,2,3]:
                soon.extend(db.get_expiring_in_days(d))
            uniq = {s["id"]: s for s in soon}.values()
            uniq = sorted(uniq, key=lambda x: x.get("expiry_date") or "")
            if not uniq:
                await query.edit_message_text("✅ No one expiring within 3 days.")
                return
            lines = ["⏰ <b>Expiring soon</b>\n"]
            kb = []
            for s in list(uniq)[:20]:
                lines.append(f"• {s.get('full_name')} – {s.get('expiry_date')}")
                kb.append([InlineKeyboardButton(f"Renew {s.get('full_name')[:18]}", callback_data=f"renew:{s.get('id')}")])
            await query.edit_message_text("\n".join(lines), parse_mode="HTML",
                                          reply_markup=InlineKeyboardMarkup(kb) if kb else None)
            return

        if data == "admin_expired":
            subs = db.get_expired()
            lines = [f"❌ <b>Expired ({len(subs)})</b>\n"]
            for s in subs[:25]:
                lines.append(f"• {s.get('full_name')} – expired {s.get('expiry_date')}")
            await query.edit_message_text("\n".join(lines), parse_mode="HTML")
            return

        if data == "admin_renew_menu":
            await query.edit_message_text("🔄 Send:\n<code>renew_&lt;subscriber_id&gt;_&lt;days&gt;</code>", parse_mode="HTML")
            return
        if data == "admin_edit_menu":
            await query.edit_message_text("✏️ Send:\n<code>edit_&lt;subscriber_id&gt;_YYYY-MM-DD</code>", parse_mode="HTML")
            return
        if data == "admin_delete_menu":
            await query.edit_message_text("🗑 Send:\n<code>delete_&lt;subscriber_id&gt;</code>", parse_mode="HTML")
            return
        if data == "admin_kick_menu":
            await query.edit_message_text("🚫 Send:\n<code>kick_&lt;telegram_id&gt;</code>", parse_mode="HTML")
            return
        if data == "admin_search":
            await query.edit_message_text("🔍 Send:\n<code>search name or @username or ID</code>", parse_mode="HTML")
            return
        if data == "admin_report":
            stats = db.get_stats()
            txt = (
                "📊 <b>Subscription Report</b>\n"
                "━━━━━━━━━━━━\n"
                f"Total: {stats['total']}\n"
                f"✅ Active: {stats['active']}\n"
                f"📋 Pending duration: {stats['pending']}\n"
                f"⏰ Expiring ≤3 days: {stats['expiring_soon']}\n"
                f"❌ Expired: {stats['expired']}\n"
                f"🚫 Cancelled: {stats['cancelled']}"
            )
            await query.edit_message_text(txt, parse_mode="HTML")
            return

        if data == "admin_back":
            from handlers.admin_handler import build_admin_keyboard, ADMIN_PANEL_TEXT
            await query.edit_message_text(ADMIN_PANEL_TEXT, parse_mode="HTML",
                                          reply_markup=build_admin_keyboard())
            return

        # renew & re-invite after kick
        if data.startswith("reinvite:"):
            sid = data.split(":",1)[1]
            sub = db.get_by_id(sid)
            if not sub:
                await query.answer("Subscriber not found", show_alert=True)
                return
            link = await create_one_use_invite(context.bot, name=sub.get("full_name","renew")[:20])
            if link:
                await query.edit_message_text(
                    f"🔗 One-use invite (24h):\n{link}\n\n"
                    f"Member: {sub.get('full_name')}  <code>{sub.get('telegram_id')}</code>",
                    parse_mode="HTML"
                )
            else:
                await query.answer("Failed to create invite – check bot admin rights", show_alert=True)
            return

        await query.answer()
    except Exception as e:
        logger.exception("callback_router error: %s", e)
        try:
            await query.edit_message_text("❌ Error – check logs")
        except Exception:
            pass

# admin callbacks only
callback_handler = CallbackQueryHandler(callback_router, pattern=r"^(dur:|custom:|renew:|ignore:|admin_|reinvite:)")
