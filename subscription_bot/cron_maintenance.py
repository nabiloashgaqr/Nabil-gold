import asyncio
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder
import config
from scheduler import check_expirations
from handlers.member_handler import chat_member_update
from handlers.silent_handler import start_cmd
from handlers.admin_handler import admin_cmd

# إعداد السجلات
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def process_admin_updates(app):
    """
    يسحب التحديثات ويعالج أوامر الإدارة وتسجيل الجدد.
    """
    logger.info("📥 Checking for Admin commands and new members...")
    try:
        updates = await app.bot.get_updates(
            offset=0, 
            timeout=30, 
            allowed_updates=["message", "callback_query", "chat_member"]
        )
        
        if not updates:
            logger.info("No pending updates to process.")
            return

        logger.info(f"Processing {len(updates)} pending updates...")
        
        class MockContext:
            def __init__(self, bot): self.bot = bot
        context = MockContext(app.bot)

        for update in updates:
            # 1. معالجة انضمام الأعضاء
            if update.chat_member:
                # إضافة سطر للفحص: طباعة معرف القناة التي وصل منها التحديث
                logger.info(f"Detected chat_member update from Chat ID: {update.chat_member.chat.id}")
                logger.info(f"Target Chat ID in config: {config.TARGET_CHAT_ID}")
                
                await chat_member_update(update, context)
            
            # 2. معالجة الرسائل النصية
            if update.message and update.message.text:
                text = update.message.text.strip()
                if text == "/start":
                    await start_cmd(update, context)
                elif text == "/admin":
                    await admin_cmd(update, context)
                else:
                    from handlers.admin_handler import admin_text_commands
                    await admin_text_commands(update, context)

            # 3. معالجة ضغطات الأزرار
            if update.callback_query:
                from handlers.callback_handler import callback_router
                await callback_router(update, context)

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

        await process_admin_updates(app)
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
