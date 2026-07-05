import asyncio
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder
import config
from scheduler import check_expirations
from handlers.member_handler import chat_member_update
from handlers.admin_handler import admin_cmd
from handlers.callback_handler import callback_router

# إعداد السجلات
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def process_admin_updates(app):
    """
    يسحب التحديثات لمعالجة أوامر الإدارة فقط (مثل /admin أو تجديد الاشتراك).
    """
    logger.info("📥 Checking for Admin commands...")
    try:
        updates = await app.bot.get_updates(offset=0, timeout=20)
        if not updates:
            return

        for update in updates:
            # 1. تسجيل الأعضاء الجدد تلقائياً (أهم ميزة)
            if update.chat_member:
                await chat_member_update(update, None)
            
            # 2. معالجة أوامر المدير فقط
            if update.message and update.message.text == "/admin":
                class MockContext:
                    def __init__(self, bot): self.bot = bot
                await admin_cmd(update, MockContext(app.bot))

            # 3. معالجة أزرار الإدارة (تجديد، طرد، إلخ)
            if update.callback_query:
                class MockContext:
                    def __init__(self, bot): self.bot = bot
                await callback_router(update, MockContext(app.bot))

        # تأكيد الاستلام
        last_update_id = updates[-1].update_id
        await app.bot.get_updates(offset=last_update_id + 1, timeout=10)
        
    except Exception as e:
        logger.error("⚠️ Admin update processing error: %s", e)

async def run_maintenance():
    logger.info("🚀 Starting Daily Subscription Maintenance Cycle...")
    try:
        app = (ApplicationBuilder()
               .token(config.BOT_TOKEN)
               .build())

        # معالجة أوامر المدير وتسجيل الجدد
        await process_admin_updates(app)
        
        # فحص الاشتراكات (تنبيهات المدير والطرود)
        await check_expirations(app)
        
        logger.info("✅ All daily maintenance tasks completed successfully.")
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
