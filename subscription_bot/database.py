import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from supabase import create_client, Client
import config

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        if not config.SUPABASE_URL or not config.SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY مفقودة في .env")
        self.client: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

    # ---------- subscribers ----------
    def get_subscriber_by_tid(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        try:
            res = self.client.table("subscribers").select("*").eq("telegram_id", telegram_id).limit(1).execute()
            return res.data[0] if res.data else None
        except Exception as e:
            logger.exception("get_subscriber_by_tid failed: %s", e)
            return None

    def upsert_on_join(self, telegram_id: int, full_name: str, username: Optional[str]) -> Dict[str, Any]:
        """عند دخول عضو جديد – تفعيل تلقائي لمدة 30 يوم"""
        existing = self.get_subscriber_by_tid(telegram_id)
        today = date.today().isoformat()
        
        # المدة الافتراضية: 30 يوم
        DEFAULT_DAYS = 30
        expiry_date = (date.today() + timedelta(days=DEFAULT_DAYS)).isoformat()

        if existing:
            try:
                self.client.table("subscribers").update({
                    "full_name": full_name,
                    "telegram_username": username,
                    "join_date": today,
                    "subscription_duration": DEFAULT_DAYS,
                    "duration_type": "day",
                    "expiry_date": expiry_date,
                    "status": "active",
                    "kicked": False,
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("telegram_id", telegram_id).execute()
            except Exception:
                logger.exception("update on re-join failed")
            return self.get_subscriber_by_tid(telegram_id) or existing
        
        payload = {
            "full_name": full_name,
            "telegram_username": username,
            "telegram_id": telegram_id,
            "join_date": today,
            "subscription_duration": DEFAULT_DAYS,
            "duration_type": "day",
            "expiry_date": expiry_date,
            "status": "active",
            "can_dm": False
        }
        try:
            res = self.client.table("subscribers").insert(payload).execute()
            return res.data[0] if res.data else payload
        except Exception:
            logger.exception("insert subscriber failed")
            return payload

    def set_can_dm(self, telegram_id: int, can_dm: bool = True):
        try:
            self.client.table("subscribers").update({"can_dm": can_dm, "updated_at": datetime.utcnow().isoformat()}).eq("telegram_id", telegram_id).execute()
        except Exception:
            logger.exception("set_can_dm failed")

    def set_subscription(self, subscriber_id: str, duration: int, duration_type: str) -> Optional[date]:
        """يحسب expiry_date ويحدث الحالة إلى active"""
        try:
            today = date.today()
            if duration_type == "day":
                expiry = today + timedelta(days=duration)
            elif duration_type == "week":
                expiry = today + timedelta(weeks=duration)
            elif duration_type == "month":
                # تقريب 30 يوم للشهر
                expiry = today + timedelta(days=30*duration)
            elif duration_type == "year":
                expiry = today + timedelta(days=365*duration)
            else:
                expiry = today + timedelta(days=duration)

            self.client.table("subscribers").update({
                "subscription_duration": duration,
                "duration_type": duration_type,
                "expiry_date": expiry.isoformat(),
                "status": "active",
                "kicked": False,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", subscriber_id).execute()
            return expiry
        except Exception:
            logger.exception("set_subscription failed")
            return None

    def list_by_status(self, status: str, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            res = self.client.table("subscribers").select("*").eq("status", status).order("expiry_date", desc=False).limit(limit).execute()
            return res.data or []
        except Exception:
            logger.exception("list_by_status failed")
            return []

    def get_pending(self) -> List[Dict[str, Any]]:
        return self.list_by_status("pending_duration", 100)

    def get_active(self) -> List[Dict[str, Any]]:
        return self.list_by_status("active", 200)

    def get_expired(self) -> List[Dict[str, Any]]:
        return self.list_by_status("expired", 200)

    def search_subscribers(self, q: str) -> List[Dict[str, Any]]:
        q = q.strip()
        if not q:
            return []
        try:
            # بحث بسيط: نجلب الكل ونفلتر محلياً (Supabase free يدعم ilike لكن نبسّط)
            res = self.client.table("subscribers").select("*").limit(200).execute()
            out = []
            ql = q.lower()
            for r in res.data or []:
                if (ql in str(r.get("full_name","")).lower() or
                    ql in str(r.get("telegram_username","")).lower() or
                    ql == str(r.get("telegram_id",""))):
                    out.append(r)
            return out[:30]
        except Exception:
            logger.exception("search failed")
            return []

    def get_expiring_in_days(self, days: int) -> List[Dict[str, Any]]:
        target = (date.today() + timedelta(days=days)).isoformat()
        try:
            res = self.client.table("subscribers").select("*").eq("status", "active").eq("expiry_date", target).execute()
            return res.data or []
        except Exception:
            logger.exception("get_expiring_in_days failed")
            return []

    def get_expired_today_or_before(self) -> List[Dict[str, Any]]:
        today = date.today().isoformat()
        try:
            res = self.client.table("subscribers").select("*").eq("status", "active").lte("expiry_date", today).execute()
            return res.data or []
        except Exception:
            logger.exception("get_expired_today failed")
            return []

    def mark_expired_kicked(self, subscriber_id: str):
        try:
            self.client.table("subscribers").update({
                "status": "expired",
                "kicked": True,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", subscriber_id).execute()
        except Exception:
            logger.exception("mark_expired_kicked failed")

    def cancel_subscriber(self, subscriber_id: str):
        try:
            self.client.table("subscribers").update({
                "status": "cancelled",
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", subscriber_id).execute()
            return True
        except Exception:
            logger.exception("cancel failed")
            return False

    def delete_subscriber(self, subscriber_id: str):
        try:
            self.client.table("subscribers").delete().eq("id", subscriber_id).execute()
            return True
        except Exception:
            logger.exception("delete failed")
            return False

    def update_expiry(self, subscriber_id: str, new_expiry: date):
        try:
            self.client.table("subscribers").update({
                "expiry_date": new_expiry.isoformat(),
                "status": "active",
                "kicked": False,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", subscriber_id).execute()
            return True
        except Exception:
            logger.exception("update_expiry failed")
            return False

    def get_by_id(self, subscriber_id: str) -> Optional[Dict[str, Any]]:
        try:
            res = self.client.table("subscribers").select("*").eq("id", subscriber_id).limit(1).execute()
            return res.data[0] if res.data else None
        except Exception:
            return None

    # ---------- notifications_log ----------
    def has_notification(self, subscriber_id: str, notification_type: str, unique_day: Optional[str] = None) -> bool:
        """منع التكرار"""
        try:
            q = self.client.table("notifications_log").select("id").eq("subscriber_id", subscriber_id).eq("notification_type", notification_type)
            res = q.execute()
            if not res.data:
                return False
            if unique_day:
                # تحقق إضافي بالتاريخ (اختياري مبسط)
                return True
            return True
        except Exception:
            return False

    def log_notification(self, subscriber_id: str, notification_type: str, sent_to: int, message: str):
        try:
            self.client.table("notifications_log").insert({
                "subscriber_id": subscriber_id,
                "notification_type": notification_type,
                "sent_to": sent_to,
                "message": message[:2000]
            }).execute()
        except Exception:
            logger.exception("log_notification failed")

    # ---------- settings / stats ----------
    def get_stats(self) -> Dict[str, int]:
        try:
            def count_status(s):
                r = self.client.table("subscribers").select("id", count="exact").eq("status", s).execute()
                return r.count or 0
            pending = count_status("pending_duration")
            active = count_status("active")
            expired = count_status("expired")
            cancelled = count_status("cancelled")
            # ينتهون خلال 3 أيام
            soon = 0
            for d in [1,2,3]:
                soon += len(self.get_expiring_in_days(d))
            return {
                "total": pending+active+expired+cancelled,
                "pending": pending,
                "active": active,
                "expired": expired,
                "cancelled": cancelled,
                "expiring_soon": soon
            }
        except Exception:
            logger.exception("get_stats failed")
            return {"total":0,"pending":0,"active":0,"expired":0,"cancelled":0,"expiring_soon":0}

# singleton
_db_instance: Optional[Database] = None
def get_db() -> Database:
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance
