import logging
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, Defaults
import config
from handlers.member_handler import member_handler
from handlers.admin_handler import admin_command_handler, admin_text_handler
from handlers.callback_handler import callback_handler
from handlers.silent_handler import start_handler, silent_message_handler, silent_command_handler, activate_callback_handler
from scheduler import setup_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.INFO)

async def post_init(app):
    scheduler = setup_scheduler(app)
    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    logger.info("✅ Nabil Gold – Subscription Bot started")
    logger.info("Target chat: %s", config.TARGET_CHAT_ID)
    logger.info("Admin contact: %s", config.ADMIN_CONTACT)
    logger.info("Admins: %s", config.ADMIN_IDS)
    # verify bot admin rights in target channel
    try:
        if config.TARGET_CHAT_ID:
            chat = await app.bot.get_chat(config.TARGET_CHAT_ID)
            me = await app.bot.get_chat_member(config.TARGET_CHAT_ID, app.bot.id)
            logger.info("Target: %s (%s) – bot status: %s – can_restrict=%s",
                        getattr(chat, "title", config.TARGET_CHAT_ID),
                        chat.type,
                        me.status,
                        getattr(me, "can_restrict_members", False))
    except Exception as e:
        logger.warning("Could not verify target chat admin rights: %s", e)

async def post_shutdown(app):
    scheduler = app.bot_data.get("scheduler")
    if scheduler:
        scheduler.shutdown(wait=False)

def main():
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN / TELEGRAM_BOT_TOKEN missing in .env")
    if not config.ADMIN_IDS:
        logger.warning("ADMIN_IDS empty – /admin will not work")
    if not config.TARGET_CHAT_ID:
        raise RuntimeError("TARGET_CHAT_ID / TELEGRAM_CHAT_ID missing")

    defaults = Defaults(
        parse_mode="HTML",
        disable_web_page_preview=True,
        allow_sending_without_reply=True,
        block=False
    )
    app = (ApplicationBuilder()
           .token(config.BOT_TOKEN)
           .defaults(defaults)
           .post_init(post_init)
           .post_shutdown(post_shutdown)
           .build())

    # Priority order – lower group = higher priority
    # 0 – chat_member join
    app.add_handler(member_handler, group=0)

    # 1 – activation callback – must run BEFORE admin callback (so non-admin can activate)
    app.add_handler(activate_callback_handler, group=1)

    # 2 – admin commands / callbacks
    app.add_handler(admin_command_handler, group=2)
    app.add_handler(admin_text_handler, group=2)
    app.add_handler(callback_handler, group=2)

    # 3 – /start
    app.add_handler(start_handler, group=3)

    # 10 – silent drop – catch-all at end
    app.add_handler(silent_command_handler, group=10)
    app.add_handler(silent_message_handler, group=10)

    logger.info("Starting polling… Nabil Gold Sub Bot – @Smart_Pro2026")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
