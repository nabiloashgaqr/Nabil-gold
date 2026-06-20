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
        """Format and send a new trade signal in English."""
        signal = decision.get("signal", {})
        # Prefer explicit decision, then signal.side then signal.type
        trade_type = decision.get("decision", signal.get("side") or signal.get("type", "WAIT"))
        emoji = "🟢" if trade_type == "BUY" else "🔴" if trade_type == "SELL" else "🟡"
        direction_text = "BUY" if trade_type == "BUY" else "SELL" if trade_type == "SELL" else "WAIT"
        entry = signal.get("entry", {})
        entry_low = entry.get("low", entry.get("price", 0))
        entry_high = entry.get("high", entry.get("price", 0))
        reasons = decision.get("reasons", [])[:8]
        reasons_text = "\n".join(f"• {html.escape(str(reason))}" for reason in reasons) or "• No sufficient reasons available"

        ai = decision.get("ai", {}) or {}
        ai_text = ""
        if ai.get("available"):
            notes = ai.get("quality_notes") or []
            notes_text = "\n".join(f"• {html.escape(str(note))}" for note in notes[:3]) if isinstance(notes, list) else (f"• {html.escape(str(notes))}" if notes else "")
            supportive = ai.get("supportive_evidence") or ai.get("evidence") or []
            opposing = ai.get("opposing_evidence") or []
            supportive_text = "\n".join(f"• {html.escape(str(item))}" for item in supportive[:4]) if isinstance(supportive, list) else (f"• {html.escape(str(supportive))}" if supportive else "")
            opposing_text = "\n".join(f"• {html.escape(str(item))}" for item in opposing[:3]) if isinstance(opposing, list) else (f"• {html.escape(str(opposing))}" if opposing else "")
            ai_lines = [
                "🤖 <b>Groq Analysis:</b>",
                f"├ Bias: {html.escape(str(ai.get('market_bias', 'N/A')))}",
                f"├ Entry reason: {html.escape(str(ai.get('entry_reason', ai.get('reasoning', 'N/A'))))}",
                f"├ Opposite risk: {html.escape(str(ai.get('opposite_risk', 'N/A')))}",
                f"├ Risk notes: {html.escape(str(ai.get('risk_notes', 'N/A')))}",
                f"├ Invalidation: {html.escape(str(ai.get('invalidation', 'N/A')))}",
                f"├ Alternative: {html.escape(str(ai.get('alternative_scenario', 'N/A')))}",
                f"└ Plan: {html.escape(str(ai.get('action_plan', 'N/A')))}",
            ]
            if supportive_text:
                ai_lines.append("\n<b>Supportive evidence:</b>")
                ai_lines.append(supportive_text)
            if opposing_text:
                ai_lines.append("\n<b>Opposing evidence / risks:</b>")
                ai_lines.append(opposing_text)
            if notes_text:
                ai_lines.append("\n<b>Groq notes:</b>")
                ai_lines.append(notes_text)
            ai_text = "\n".join(ai_lines) + "\n\n"

        trade_id = decision.get("trade_id", signal.get("trade_id", "not saved yet"))
        current_price = decision.get("current_price", signal.get("current_price", entry.get("price", 0)))
        trading_mode = str(decision.get("trading_mode", signal.get("trading_mode", "paper"))).lower()
        paper_trading = bool(decision.get("paper_trading", signal.get("paper_trading", trading_mode == "paper")))
        mode_text = "🧪 <b>Mode:</b> Paper Trading - simulated, not executed\n" if paper_trading else "⚡ <b>Mode:</b> Live/Manual Tracking\n"
        run_source = decision.get("run_source", "unknown")
        run_source_text = {"scheduled": "Scheduled", "manual": "Manual", "workflow_dispatch": "Manual", "schedule": "Scheduled"}.get(str(run_source), str(run_source))
        decision_mode = decision.get("decision_mode", "Groq Observation" if ai.get("available") else "Unknown")
        requires_three = "Yes" if decision.get("requires_three_agents") else "No"
        agent_rule = "One agent + Groq approval" if decision.get("one_agent_groq_mode") or str(decision_mode) == "One-Agent + Groq" else f"Needs 3 agents? {requires_three}"
        run_line = f"🔄 <b>Run:</b> {html.escape(run_source_text)} | <b>Decision mode:</b> {html.escape(str(decision_mode))} | <b>Rule:</b> {html.escape(agent_rule)}\n"

        quality = decision.get("quality", {}) or {}
        quality_line = f"⭐ <b>Signal Quality:</b> {html.escape(str(quality.get('grade', 'N/A')))} / {float(quality.get('score', 0)):.1f}% ({html.escape(str(quality.get('label', '')))} )\n" if quality else ""
        risk_grade = ((decision.get("risk", {}) or {}).get("trade_grade", {}) or {})
        risk_grade_line = f"🛡️ <b>Risk Grade:</b> {html.escape(str(risk_grade.get('grade', 'N/A')))} / {float(risk_grade.get('score', 0)):.1f}% ({html.escape(str(risk_grade.get('label', '')))} )\n" if risk_grade else ""
        agent_context = decision.get("agent_context") or {}
        agent_context_line = ""
        if agent_context:
            agent_context_line = (
                f"🧩 <b>Agent context:</b> {html.escape(str(agent_context.get('agent', 'N/A')))} | "
                f"{html.escape(str(agent_context.get('signal', '')))} | "
                f"{float(agent_context.get('adjusted_confidence', agent_context.get('confidence', 0))):.1f}%\n"
            )
        groq_observation_line = "🤖 <b>Decision:</b> Groq Observation - final signal is from Groq\n" if str(decision.get("summary", "")).startswith("Groq Observation") else ""
        daily_bias = decision.get("daily_bias", {}) or {}
        bias_line = f"🧭 <b>Daily Bias:</b> {html.escape(str(daily_bias.get('bias', 'NEUTRAL')))} ({float(daily_bias.get('confidence', 0)):.1f}%)\n" if daily_bias else ""
        dynamic_risk = decision.get("dynamic_risk", {}) or {}
        dynamic_risk_line = f"🛡️ <b>Dynamic Risk:</b> {html.escape(str(dynamic_risk.get('level', 'NORMAL')))} | min conf {html.escape(str(dynamic_risk.get('min_confidence_required', '')))}% " if dynamic_risk else ""

        session_info = decision.get("session_info", {})
        session_text = ""
        if session_info.get("current_session"):
            sq = session_info.get("session_quality", "UNKNOWN")
            quality_emoji = {"BEST": "⭐⭐⭐", "HIGH": "⭐⭐", "MEDIUM": "⭐", "LOW": "⚠️"}.get(sq, "")
            session_text = f"\n🕐 <b>Session:</b> {html.escape(str(session_info.get('current_session')))} {quality_emoji}"

        order_type = signal.get('order_type', f'{trade_type}_MARKET')
        text = f"""
 📊 <b>XAU/USD Signal</b>
 ━━━━━━━━━━━━━━━━━━━━━
 
 {emoji} <b>Decision:</b> {direction_text}
 📌 <b>Order Type:</b> {html.escape(str(order_type))}
 ⏰ <b>Time:</b> {self._now_text()}
 💰 <b>Current Price:</b> {format_price(current_price)}
 {mode_text}{run_line}{session_text}
 
 📍 <b>Entry Zone:</b> {format_price(entry_low)} - {format_price(entry_high)}
 🛑 <b>Stop Loss:</b> {format_price(signal.get('stop_loss'))}
 🎯 <b>Take Profit 1:</b> {format_price(signal.get('tp1'))}
 🎯 <b>Take Profit 2:</b> {format_price(signal.get('tp2'))}
 📊 <b>R:R =</b> 1:{float(signal.get('rr_ratio', 0)):.2f}
 🔒 <b>Confidence:</b> {int(decision.get('confidence', 0))}%
 {quality_line}{risk_grade_line}{agent_context_line}{groq_observation_line}{bias_line}{dynamic_risk_line}
 {ai_text}📋 <b>Reasons:</b>
 {reasons_text}
 
 ⚠️ <b>Disclaimer:</b> Educational paper-trading signal only. Not financial advice. No automated execution.
 🆔 <b>Trade ID:</b> <code>{html.escape(str(trade_id))}</code>
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
            "NEAR_TP1": "🔄 Near Take Profit 1",
            "TP1_HIT": "✅ Take Profit 1 Hit",
            "MOVE_SL_TO_BE": "💡 Move Stop Loss to Break-even",
            "TP2_HIT": "🏆 Take Profit 2 Hit",
            "SL_HIT": "❌ Stop Loss Hit",
            "BE_HIT": "➖ Break-even Hit",
            "LONG_RUNNING": "⏱ Long-running Trade",
            "EXIT_WARNING": "⚠️ Exit / Risk Warning",
            "EXPIRED": "⌛ Trade Expired",
            "MANUAL_CLOSE": "📌 Manual Close",
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

        # Friendly display for trade type/side with fallbacks
        display_type = trade.get('side') or trade.get('trade_type') or trade.get('type') or ""

        text = f"""
 {title} - <b>XAU/USD</b>
 ━━━━━━━━━━━━━━━━━━━━━
 
 🆔 <b>ID:</b> <code>{html.escape(str(trade.get('id')))}</code>
 📊 <b>Type:</b> {html.escape(str(display_type))}
 📍 <b>Entry:</b> {format_price(trade.get('entry_price'))}
 🛑 <b>Stop Loss:</b> {format_price(trade.get('stop_loss'))}
 🎯 <b>TP1:</b> {format_price(trade.get('tp1'))}
 🎯 <b>TP2:</b> {format_price(trade.get('tp2'))}
 💰 <b>Current Price:</b> {format_price(current_price)}
 📈 <b>Current PnL:</b> {pnl_points:+.1f} نقطة {pnl_emoji}
 📌 <b>Status:</b> {html.escape(str(old_status))} → {html.escape(str(new_status))}
 {extra_text}
 
 {note}
 
 ⚠️ Educational paper-trading update only. Not financial advice.
 """.strip()
        return self.send_message(text, urgent=event_type in {"TP1_HIT", "TP2_HIT", "SL_HIT", "BE_HIT", "EXPIRED"})

    def send_trade_update(self, trade: Dict[str, Any], new_status: str, current_price: float, pnl_points: float) -> bool:
        """Backward-compatible wrapper for status-change updates."""
        return self.send_trade_event(trade, new_status, current_price, pnl_points, {"old_status": trade.get("status", "OPEN"), "new_status": new_status})

    def _trade_event_note(self, event_type: str, trade: Dict[str, Any], current_price: float, evaluation: Dict[str, Any]) -> str:
        """Return an English note for a trade-management event."""
        if event_type == "NEAR_TP1":
            return f"💡 Price reached about 80% of TP1 distance ({format_price(trade.get('tp1'))}). Monitor trade management."
        if event_type == "TP1_HIT":
            return "✅ TP1 reached. Consider partial profit and monitor TP2."
        if event_type == "MOVE_SL_TO_BE":
            return f"💡 Suggested: move SL to entry {format_price(trade.get('entry_price'))} to protect the trade."
        if event_type == "TP2_HIT":
            return "🏆 TP2 reached. Trade completed successfully."
        if event_type == "SL_HIT":
            return "❌ Stop Loss was hit. Follow the plan and review the setup."
        if event_type == "BE_HIT":
            return "➖ Trade returned to break-even after SL protection."
        if event_type == "LONG_RUNNING":
            return "⏱ Trade has been open for a long time. Monitor momentum and news risk."
        if event_type == "EXIT_WARNING":
            return "⚠️ Exit/risk warning: trade is near a danger zone or adverse move is deep."
        if event_type == "EXPIRED":
            return "⌛ Trade expired according to trade-management rules."
        return "🔄 New trade update."

    def send_daily_report(self, report_text: str) -> bool:
        """Send daily report text."""
        return self.send_message(report_text, urgent=False)

    def send_error_alert(self, error_message: str) -> bool:
        """Send a compact error alert to Telegram."""
        text = f"""
 🚨 <b>Gold AI Signals Error</b>
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
