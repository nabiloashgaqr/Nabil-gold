import asyncio
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder
import config
from scheduler import check_expirations
from handlers.member_handler import chat_member_update
from handlers.silent_handler import start_cmd, activate_callback
from handlers.admin_handler import admin_cmd

# إعداد السجلات
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def process_pending_updates(app):
    """
    يسحب جميع التحديثات المتراكمة ويعالجها (الجدد + /start + /admin).
    """
    logger.info("📥 Fetching pending updates from Telegram...")
    try:
        updates = await app.bot.get_updates(offset=0, timeout=30)
        if not updates:
            logger.info("No pending updates to process.")
            return

        logger.info(f"Processing {len(updates)} pending updates...")
        
        for update in updates:
            # 1. معالجة انضمام الأعضاء
            if update.chat_member:
                await chat_member_update(update, None)
            
            # 2. معالجة الرسائل النصية (أوامر)
            if update.message and update.message.text:
                text = update.message.text.strip()
                
                # سياق وهمي بسيط لتمريره للدوال
                class MockContext:
                    def __init__(self, bot): self.bot = bot
                context = MockContext(app.bot)

                if text == "/start":
                    await start_cmd(update, context)
                elif text == "/admin":
                    await admin_cmd(update, context)

            # 3. معالجة ضغطات الأزرار (التفعيل)
            if update.callback_query and update.callback_query.data.startswith("activate:"):
                class MockContext:
                    def __init__(self, bot): self.bot = bot
                await activate_callback(update, MockContext(app.bot))

        # تأكيد استلام التحديثات
        last_update_id = updates[-1].update_id
        await app.bot.get_updates(offset=last_update_id + 1, timeout=10)
        
    except Exception as e:
        logger.error("⚠️ Error processing updates: %s", e)

async def run_maintenance():
    logger.info("🚀 Starting 6-Hour Subscription Maintenance Cycle...")
    try:
        app = (ApplicationBuilder()
               .token(config.BOT_TOKEN)
               .build())

        await process_pending_updates(app)
        await check_expirations(app)
        
        logger.info("✅ All maintenance tasks completed successfully.")
    except Exception as e:
        logger.exception("❌ Critical Maintenance failure: %s", e)
    finally:
        if 'app' in locals():
            await app.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(run_maintenance())
    except (KeyboardInterrupt, SystemExit):
        pass
