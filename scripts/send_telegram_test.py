"""Send a Telegram smoke-test message from GitHub Actions.

Run via: Actions → 📱 Telegram Smoke Test → Run workflow
"""

from __future__ import annotations

from datetime import datetime, timezone

from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging


def main() -> None:
    setup_logging()
    config = load_config()
    telegram = TelegramService(config)
    text = (
        "✅ <b>اختبار Telegram ناجح</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 البوت متصل بالمحادثة بشكل صحيح.\n"
        f"⏰ الوقت: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    sent = telegram.send_message(text, urgent=True)
    if not sent:
        raise SystemExit(
            "Telegram test message was not sent. Check TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "
            "and make sure the bot is added to the chat/channel."
        )
    print("✅ Telegram test message sent successfully")


if __name__ == "__main__":
    main()
