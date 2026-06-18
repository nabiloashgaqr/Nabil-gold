"""Send a Telegram smoke-test message from GitHub Actions with clear diagnostics.

Run via: Actions → 📱 Telegram Smoke Test → Run workflow
This script never prints secret values.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    return value.strip()


def _mask_chat_id(chat_id: str) -> str:
    if not chat_id:
        return "EMPTY"
    if len(chat_id) <= 6:
        return "***"
    return f"{chat_id[:3]}***{chat_id[-3:]}"


def _telegram_api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _post_send_message(token: str, chat_id: str, text: str) -> dict:
    response = requests.post(
        _telegram_api(token, "sendMessage"),
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=25,
    )
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        payload = {"ok": False, "description": response.text[:500]}
    payload["http_status"] = response.status_code
    return payload


def main() -> None:
    token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")

    print("🔎 Telegram smoke test diagnostics")
    print(f"- TELEGRAM_BOT_TOKEN present: {bool(token)}")
    print(f"- TELEGRAM_CHAT_ID present: {bool(chat_id)}")
    print(f"- TELEGRAM_CHAT_ID masked: {_mask_chat_id(chat_id)}")

    if not token:
        raise SystemExit("❌ TELEGRAM_BOT_TOKEN is missing or empty in GitHub Secrets")
    if not chat_id:
        raise SystemExit("❌ TELEGRAM_CHAT_ID is missing or empty in GitHub Secrets")

    # 1) Validate token with getMe.
    print("🔎 Checking bot token with getMe...")
    try:
        get_me_resp = requests.get(_telegram_api(token, "getMe"), timeout=25)
        get_me = get_me_resp.json()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"❌ Telegram getMe request failed: {exc}") from exc

    if not get_me.get("ok"):
        description = get_me.get("description", "Unknown Telegram error")
        raise SystemExit(f"❌ Bot token rejected by Telegram: {description}")

    bot = get_me.get("result", {})
    print(f"✅ Bot token is valid. Bot username: @{bot.get('username', 'unknown')}")

    # 2) Try to send message.
    text = (
        "✅ <b>اختبار Telegram ناجح</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 البوت متصل بالمحادثة بشكل صحيح.\n"
        f"⏰ الوقت: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    print("📨 Sending test message...")
    result = _post_send_message(token, chat_id, text)

    if result.get("ok"):
        message_id = result.get("result", {}).get("message_id")
        print(f"✅ Telegram test message sent successfully. message_id={message_id}")
        return

    description = str(result.get("description", "Unknown Telegram error"))
    error_code = result.get("error_code", result.get("http_status"))
    print(f"❌ Telegram sendMessage failed. error_code={error_code}")
    print(f"❌ Telegram description: {description}")

    hints = []
    desc_lower = description.lower()
    if "chat not found" in desc_lower:
        hints.extend([
            "Chat ID is wrong, or the bot has not been added to that chat/channel.",
            "For a private chat: open the bot in Telegram and send /start, then use your numeric user chat_id.",
            "For a channel: add the bot as Admin and use a channel chat_id that usually starts with -100.",
        ])
    elif "bot was blocked" in desc_lower or "forbidden" in desc_lower:
        hints.extend([
            "The bot is blocked by the user, or it is not allowed to post in the group/channel.",
            "Unblock the bot or add it as Admin to the channel/group.",
        ])
    elif "bad request" in desc_lower:
        hints.append("Check TELEGRAM_CHAT_ID formatting. Do not add spaces or quotes.")
    elif "unauthorized" in desc_lower:
        hints.append("TELEGRAM_BOT_TOKEN is invalid or revoked. Create a new token with BotFather.")

    if hints:
        print("💡 Hints:")
        for hint in hints:
            print(f"- {hint}")

    raise SystemExit(1)


if __name__ == "__main__":
    main()
