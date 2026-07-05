import os
from typing import List
from dotenv import load_dotenv

load_dotenv()

# Nabil Gold unified – support both naming schemes
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_SERVICE_KEY", "")

_admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: List[int] = []
for x in _admin_ids_raw.split(","):
    x = x.strip()
    if x.lstrip("-").isdigit():
        try:
            ADMIN_IDS.append(int(x))
        except ValueError:
            pass

# TARGET_CHAT_ID – prefer Nabil Gold TELEGRAM_CHAT_ID
_target_raw = os.getenv("TARGET_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or "0"
try:
    TARGET_CHAT_ID = int(_target_raw)
except ValueError:
    TARGET_CHAT_ID = 0

_admin_group_raw = os.getenv("ADMIN_GROUP_ID", "").strip()
ADMIN_GROUP_ID = int(_admin_group_raw) if _admin_group_raw and _admin_group_raw.lstrip("-").isdigit() else None

# Nabil Gold admin contact
ADMIN_CONTACT = os.getenv("ADMIN_CONTACT", "@Smart_Pro2026")

TIMEZONE = os.getenv("TIMEZONE", "Asia/Hebron")

def admin_destination() -> int:
    """Return chat_id for admin alerts"""
    if ADMIN_GROUP_ID:
        return ADMIN_GROUP_ID
    if ADMIN_IDS:
        return ADMIN_IDS[0]
    raise RuntimeError("ADMIN_IDS not configured")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# English messages – per requirements – subscriber sees only 2 messages in lifetime
MESSAGES = {
    "start_ok": "✅",
    "start_need_activate": "👋 Welcome to Nabil Gold Private!\n\nTo receive subscription alerts, please activate notifications:",
    "activate_button": "🔔 Activate Alerts",
    "activated_ok": "✅ Activation successful – You will now receive subscription alerts.",
    "already_activated": "✅ Activated – You will receive subscription alerts.",
    # subscriber – 3 days reminder – ONLY message #1 in lifetime
    "sub_remind_3d": "⏰ Your subscription expires in 3 days on {expiry}\n\nTo renew, contact admin: {admin}",
    # subscriber – expired kicked – ONLY message #2 in lifetime
    "sub_expired_kicked": "❌ Your subscription has expired and you have been removed from the channel\n\nTo renew, contact admin: {admin}",
}
