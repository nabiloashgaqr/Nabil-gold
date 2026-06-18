"""Telegram service for formatted signal, update and report messages.

الخدمة تستخدم Telegram Bot API مباشرة عبر requests حتى تكون بسيطة داخل GitHub
Actions. عند غياب Secrets لا تفشل، بل تسجل الرسالة فقط لتسهيل الاختبار المحلي.
"""

from __future__ import annotations

import html
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

from utils.helpers import format_price, load_config


class TelegramService:
    """Send HTML-formatted Telegram messages with simple rate limiting."""

    API_BASE = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        telegram_config = self.config.get("telegram", {})
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or telegram_config.get("bot_token")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID") or telegram_config.get("chat_id") or telegram_config.get("signals_channel")
        if isinstance(self.bot_token, str) and self.bot_token.startswith("ENV:"):
            self.bot_token = os.environ.get(self.bot_token.replace("ENV:", "", 1))
        if isinstance(self.chat_id, str) and self.chat_id.startswith("ENV:"):
            self.chat_id = os.environ.get(self.chat_id.replace("ENV:", "", 1))
        self.logger = logging.getLogger(self.__class__.__name__)
        self.session = requests.Session()
        self._sent_timestamps: List[float] = []

    def send_message(self, text: str, chat_id: str | None = None, urgent: bool = False) -> bool:
        """Send a raw HTML message. Returns False if Telegram is not configured."""
        target_chat = chat_id or self.chat_id
        if not self.bot_token or not target_chat or str(self.bot_token).startswith("YOUR_"):
            self.logger.info("Telegram not configured. Message preview:\n%s", text)
            return False

        self._rate_limit()
        url = self.API_BASE.format(token=self.bot_token, method="sendMessage")
        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if urgent:
            payload["disable_notification"] = False

        for attempt in range(3):
            try:
                response = self.session.post(url, json=payload, timeout=20)
                response.raise_for_status()
                result = response.json()
                if not result.get("ok", False):
                    raise RuntimeError(str(result))
                return True
            except Exception as exc:  # noqa: BLE001
                wait = 2**attempt
                self.logger.warning("Telegram send attempt %s failed: %s", attempt + 1, exc)
                time.sleep(wait)
        return False

    def send_signal(self, decision: Dict[str, Any]) -> bool:
        """Format and send a new trade signal."""
        signal = decision.get("signal", {})
        trade_type = decision.get("decision", signal.get("type", "WAIT"))
        emoji = "🟢" if trade_type == "BUY" else "🔴"
        direction_ar = "شراء BUY" if trade_type == "BUY" else "بيع SELL"
        entry = signal.get("entry", {})
        entry_low = entry.get("low", entry.get("price", 0))
        entry_high = entry.get("high", entry.get("price", 0))
        reasons = decision.get("reasons", [])[:8]
        reasons_text = "\n".join(f"• {html.escape(str(reason))}" for reason in reasons) or "• لا توجد أسباب كافية"
        ai = decision.get("ai", {}) or {}
        ai_text = ""
        if ai.get("available"):
            quality_notes = ai.get("quality_notes") or []
            if isinstance(quality_notes, list):
                quality_notes_text = "\n".join(f"• {html.escape(str(note))}" for note in quality_notes[:3])
            else:
                quality_notes_text = f"• {html.escape(str(quality_notes))}" if quality_notes else ""
            ai_lines = [
                "🤖 <b>تحليل Groq:</b>",
                f"├ الاتجاه: {html.escape(str(ai.get('market_bias', 'غير محدد')))}",
                f"├ سبب الدخول: {html.escape(str(ai.get('entry_reason', ai.get('reasoning', ''))))}",
                f"├ خطر الاتجاه المعاكس: {html.escape(str(ai.get('opposite_risk', 'غير محدد')))}",
                f"├ ملاحظات المخاطر: {html.escape(str(ai.get('risk_notes', 'غير محدد')))}",
                f"└ الخطة: {html.escape(str(ai.get('action_plan', 'غير محدد')))}",
            ]
            if quality_notes_text:
                ai_lines.append("\n<b>نقاط Groq:</b>")
                ai_lines.append(quality_notes_text)
            ai_text = "\n".join(ai_lines) + "\n\n"
        trade_id = decision.get("trade_id", signal.get("trade_id", "غير محفوظ بعد"))
        current_price = decision.get("current_price", signal.get("current_price", entry.get("price", 0)))
        quality = decision.get("quality", {}) or {}
        quality_line = ""
        if quality:
            quality_line = f"⭐ <b>جودة الإشارة:</b> {html.escape(str(quality.get('grade', 'N/A')))} / {float(quality.get('score', 0)):.1f}% ({html.escape(str(quality.get('label', '')))} )\n"

        # Session info
        session_info = decision.get("session_info", {})
        session_text = ""
        if session_info.get("current_session"):
            quality = session_info.get("session_quality", "UNKNOWN")
            quality_emoji = {"BEST": "⭐⭐⭐", "HIGH": "⭐⭐", "MEDIUM": "⭐", "LOW": "⚠️"}.get(quality, "")
            session_text = f"\n🕐 <b>الجلسة:</b> {session_info.get('current_session')} {quality_emoji}"

        text = f"""
📊 <b>إشارة XAU/USD</b>
━━━━━━━━━━━━━━━━━━━━━

{emoji} <b>القرار:</b> {direction_ar}
⏰ <b>الوقت:</b> {self._now_text()}
💰 <b>السعر الحالي:</b> {format_price(current_price)}
{session_text}

📍 <b>منطقة الدخول:</b> {format_price(entry_low)} - {format_price(entry_high)}
🛑 <b>وقف الخسارة:</b> {format_price(signal.get('stop_loss'))}
🎯 <b>الهدف الأول:</b> {format_price(signal.get('tp1'))}
🎯 <b>الهدف الثاني:</b> {format_price(signal.get('tp2'))}
📊 <b>R:R =</b> 1:{float(signal.get('rr_ratio', 0)):.2f}
🔒 <b>الثقة:</b> {int(decision.get('confidence', 0))}%
{quality_line}
{ai_text}📋 <b>أسباب الإشارة:</b>
{reasons_text}

⚠️ <b>تحذير:</b> هذه الإشارة تعليمية/تجريبية وليست توصية مالية.
🆔 <b>معرف الصفقة:</b> <code>{html.escape(str(trade_id))}</code>
""".strip()
        return self.send_message(text, urgent=True)

    def send_trade_event(
        self,
        trade: Dict[str, Any],
        event_type: str,
        current_price: float,
        pnl_points: float,
        evaluation: Dict[str, Any] | None = None,
    ) -> bool:
        """Send a detailed trade management event message."""
        evaluation = evaluation or {}
        event_titles = {
            "NEAR_TP1": "🔄 اقتراب من الهدف الأول",
            "TP1_HIT": "✅ تحقق الهدف الأول",
            "MOVE_SL_TO_BE": "💡 اقتراح Break Even",
            "TP2_HIT": "🏆 تحقق الهدف الثاني",
            "SL_HIT": "❌ وقف خسارة",
            "BE_HIT": "➖ ضربت نقطة الدخول",
            "LONG_RUNNING": "⏱ صفقة مستمرة منذ فترة",
            "EXPIRED": "⌛ انتهاء صلاحية الصفقة",
            "MANUAL_CLOSE": "📌 إغلاق يدوي",
        }
        title = event_titles.get(event_type, "🔄 تحديث صفقة")
        pnl_emoji = "✅" if pnl_points > 0 else "➖" if pnl_points == 0 else "❌"
        old_status = evaluation.get("old_status", trade.get("status", "OPEN"))
        new_status = evaluation.get("new_status", old_status)
        progress = evaluation.get("progress_to_tp1")
        hours_open = evaluation.get("hours_open")
        note = self._trade_event_note(event_type, trade, current_price, evaluation)
        extra_lines = []
        if progress is not None:
            extra_lines.append(f"📊 <b>التقدم نحو TP1:</b> {float(progress) * 100:.0f}%")
        if hours_open is not None:
            extra_lines.append(f"⏱ <b>مدة الصفقة:</b> {float(hours_open):.1f} ساعة")
        extra_text = "\n".join(extra_lines)

        text = f"""
{title} - <b>XAU/USD</b>
━━━━━━━━━━━━━━━━━━━━━

🆔 <b>المعرف:</b> <code>{html.escape(str(trade.get('id')))}</code>
📊 <b>النوع:</b> {html.escape(str(trade.get('type')))}
📍 <b>الدخول:</b> {format_price(trade.get('entry_price'))}
🛑 <b>وقف الخسارة:</b> {format_price(trade.get('stop_loss'))}
🎯 <b>TP1:</b> {format_price(trade.get('tp1'))}
🎯 <b>TP2:</b> {format_price(trade.get('tp2'))}
💰 <b>السعر الحالي:</b> {format_price(current_price)}
📈 <b>النتيجة الحالية:</b> {pnl_points:+.1f} نقطة {pnl_emoji}
📌 <b>الحالة:</b> {html.escape(str(old_status))} → {html.escape(str(new_status))}
{extra_text}

{note}

⚠️ ليست توصية مالية.
""".strip()
        return self.send_message(text, urgent=event_type in {"TP1_HIT", "TP2_HIT", "SL_HIT", "BE_HIT", "EXPIRED"})

    def send_trade_update(self, trade: Dict[str, Any], new_status: str, current_price: float, pnl_points: float) -> bool:
        """Backward-compatible wrapper for status-change updates."""
        return self.send_trade_event(trade, new_status, current_price, pnl_points, {"old_status": trade.get("status", "OPEN"), "new_status": new_status})

    def _trade_event_note(self, event_type: str, trade: Dict[str, Any], current_price: float, evaluation: Dict[str, Any]) -> str:
        """Return an Arabic note for a trade-management event."""
        if event_type == "NEAR_TP1":
            return f"💡 السعر وصل إلى حوالي 80% من مسافة الهدف الأول {format_price(trade.get('tp1'))}. راقب إدارة الصفقة."
        if event_type == "TP1_HIT":
            return "✅ تحقق الهدف الأول. يمكن جني جزء من الربح ومراقبة الهدف الثاني."
        if event_type == "MOVE_SL_TO_BE":
            return f"💡 اقتراح: يمكن تحريك وقف الخسارة إلى نقطة الدخول {format_price(trade.get('entry_price'))} لحماية الصفقة."
        if event_type == "TP2_HIT":
            return "🏆 تحقق الهدف الثاني - نتيجة ممتازة."
        if event_type == "SL_HIT":
            return "❌ تم ضرب وقف الخسارة. التزم بالخطة وإدارة المخاطر."
        if event_type == "BE_HIT":
            return "➖ عاد السعر إلى نقطة الدخول بعد تحريك الوقف - تعادل/حماية رأس المال."
        if event_type == "LONG_RUNNING":
            return "⏱ الصفقة مفتوحة منذ فترة طويلة بدون حسم. راقب ضعف الزخم أو قرب أخبار."
        if event_type == "EXPIRED":
            return "⌛ انتهت صلاحية الصفقة حسب إعدادات إدارة الصفقات."
        return "🔄 تحديث جديد على الصفقة."

    def send_daily_report(self, report_text: str) -> bool:
        """Send daily report text."""
        return self.send_message(report_text, urgent=False)

    def send_error_alert(self, error_message: str) -> bool:
        """Send a compact error alert to Telegram."""
        text = f"""
🚨 <b>خطأ في Gold AI Signals</b>
━━━━━━━━━━━━━━━━━━━━━
<code>{html.escape(error_message[:3500])}</code>
""".strip()
        return self.send_message(text, urgent=True)

    def _rate_limit(self) -> None:
        """Limit to 20 messages/minute."""
        now = time.time()
        self._sent_timestamps = [ts for ts in self._sent_timestamps if now - ts < 60]
        if len(self._sent_timestamps) >= 20:
            sleep_for = 60 - (now - self._sent_timestamps[0])
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._sent_timestamps.append(time.time())

    def _now_text(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
