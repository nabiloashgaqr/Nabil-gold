"""
One-shot scheduler runner for GitHub Actions / cron.
Runs a single expiration check cycle then exits.
Useful for serverless / GitHub Actions every 6 hours.
"""
import asyncio
import logging
from telegram.ext import ApplicationBuilder, Defaults

import sys
import os
# allow imports when run as module or script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# also allow running from inside subscription_bot folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import config
    from scheduler import check_expirations
except ModuleNotFoundError:
    # fallback when run as `python -m subscription_bot.scheduler_once` from repo root
    from subscription_bot import config
    from subscription_bot.scheduler import check_expirations

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def main():
    if not config.BOT_TOKEN:
        raise RuntimeError("SUBSCRIPTION_BOT_TOKEN / BOT_TOKEN / TELEGRAM_BOT_TOKEN missing")
    defaults = Defaults(parse_mode="HTML", disable_web_page_preview=True)
    app = ApplicationBuilder().token(config.BOT_TOKEN).defaults(defaults).build()
    await app.initialize()
    try:
        await check_expirations(app)
        logger.info("✅ subscription check completed")
    finally:
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
