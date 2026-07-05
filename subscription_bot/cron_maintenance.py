import asyncio
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder
import config
from scheduler import check_expirations
from handlers.member_handler import chat_member_update
from handlers.silent_handler import start_cmd, activate_callback

# إعداد السجلات
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def process_pending_updates(app):
    """
    يسحب جميع التحديثات المتراكمة (انضمام أعضاء، أوامر /start) 
    ويعالجها دفعة واحدة مع زيادة وقت الانتظار لتجنب الـ Timeout.
    """
    logger.info("📥 Fetching pending updates from Telegram...")
    try:
        # زيادة الـ timeout إلى 30 ثانية بدلاً من 10 لتجنب httpx.ReadTimeout
        updates = await app.bot.get_updates(offset=0, timeout=30)
        if not updates:
            logger.info("No pending updates to process.")
            return

        logger.info(f"Processing {len(updates)} pending updates...")
        
        for update in updates:
            # 1. معالجة انضمام الأعضاء (Chat Member Update)
            if update.chat_member:
                await chat_member_update(update, None)
            
            # 2. معالجة أمر /start (Message Update)
            if update.message and update.message.text == "/start":
                class MockContext:
                    def __init__(self, bot): self.bot = bot
                await start_cmd(update, MockContext(app.bot))

            # 3. معالجة ضغطات الأزرار (Callback Query)
            if update.callback_query and update.callback_query.data.startswith("activate:"):
                class MockContext:
                    def __init__(self, bot): self.bot = bot
                await activate_callback(update, MockContext(app.bot))

        # تأكيد استلام التحديثات لكي لا يتم سحبها مرة أخرى
        last_update_id = updates[-1].update_id
        await app.bot.get_updates(offset=last_update_id + 1, timeout=10)
        
    except Exception as e:
        # تسجيل الخطأ ولكن الاستمرار في تنفيذ مهام الصيانة الأخرى
        logger.error("⚠️ Could not fetch updates due to timeout or network error: %s", e)

async def run_maintenance():
    """
    العملية الشاملة: معالجة التحديثات -> فحص الاشتراكات -> إغلاق.
    """
    logger.info("🚀 Starting 6-Hour Subscription Maintenance Cycle...")
    
    try:
        # بناء التطبيق
        app = (ApplicationBuilder()
               .token(config.BOT_TOKEN)
               .build())

        # الخطوة 1: معالجة كل ما فاتنا (الجدد + /start)
        await process_pending_updates(app)
        
        # الخطوة 2: فحص الاشتراكات (الطرود والتنبيهات)
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
