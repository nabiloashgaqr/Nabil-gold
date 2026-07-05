import asyncio
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder
import config
from scheduler import check_expirations
from handlers.member_handler import chat_member_update
from handlers.silent_handler import start_cmd
from handlers.admin_handler import admin_cmd
from database import get_db

# إعداد السجلات
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def process_fast_activation(bot, text):
    """
    يعالج الصيغة المختصرة جداً: @username 1 أو @username 3
    """
    parts = text.split()
    if len(parts) != 2:
        return False
    
    identifier = parts[0].strip()
    duration_code = parts[1].strip()
    
    days_map = {"1": 30, "3": 90}
    if duration_code not in days_map:
        return False
    
    days = days_map[duration_code]
    db = get_db()
    
    # البحث عن المشترك باليوزرنيم
    sub = db.get_subscriber_by_username(identifier)
    if not sub:
        # محاولة البحث بالـ ID إذا كان المدخل رقماً
        if identifier.isdigit():
            sub = db.get_by_id(identifier)
        else:
            return False
            
    if sub:
        expiry = db.set_subscription(sub["id"], days, "day")
        await bot.send_message(
            chat_id=config.ADMIN_IDS[0], 
            text=f"✅ <b>تم التفعيل السريع بنجاح</b>\n👤 {sub.get('full_name')}\n📅 ينتهي في: {expiry}\n⏱ المدة: {days} يوم",
            parse_mode="HTML"
        )
        return True
    return False

async def process_admin_updates(app):
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
            # 1. تسجيل الأعضاء الجدد
            if update.chat_member:
                await chat_member_update(update, context)
            
            # 2. معالجة الرسائل
            if update.message and update.message.text:
                text = update.message.text.strip()
                
                if text == "/start":
                    await start_cmd(update, context)
                elif text == "/admin":
                    await admin_cmd(update, context)
                else:
                    # أولاً: محاولة التفعيل السريع (@username 1)
                    if not await process_fast_activation(app.bot, text):
                        # ثانياً: إذا لم يكن تفعيلاً سريعاً، جرب أوامر الإدارة العادية (edit_, renew_ إلخ)
                        from handlers.admin_handler import admin_text_commands
                        await admin_text_commands(update, context)

            # 3. معالجة ضغطات الأزرار (تجاهل الأخطاء القديمة)
            if update.callback_query:
                try:
                    from handlers.callback_handler import callback_router
                    await callback_router(update, context)
                except Exception as e:
                    if "too old" in str(e).lower():
                        pass # تجاهل تنبيهات تليجرام عن الأزرار القديمة
                    else:
                        logger.error(f"Callback error: {e}")

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
