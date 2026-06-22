"""Telegram service for formatted signal, update and report messages.

Service uses Telegram Bot API directly via requests for simplicity in GitHub Actions.
Gracefully handles missing secrets for local testing.
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
        trade_type = decision.get("decision", signal.get("type", "WAIT"))
        emoji = "🟢" if trade_type == "BUY" else "🔴" if trade_type == "SELL" else "🟡"
        direction_text = "BUY" if trade_type == "BUY" else "SELL" if trade_type == "SELL" else "WAIT"
        
        entry = signal.get("entry", {})
        entry_low = entry.get("low", entry.get("price", 0))
        entry_high = entry.get("high", entry.get("price", 0))
        current_price = decision.get("current_price", signal.get("current_price", entry.get("price", 0)))
        
        # Core metrics
        confidence = int(decision.get("confidence", 0))
        quality = decision.get("quality", {}) or {}
        quality_grade = quality.get("grade", "N/A")
        quality_score = float(quality.get("score", 0))
        
        # Session info
        session_info = decision.get("session_info", {})
        session_name = session_info.get("current_session", "Unknown")
        session_quality = session_info.get("session_quality", "UNKNOWN")
        session_emoji = {"BEST": "⭐⭐⭐", "HIGH": "⭐⭐", "MEDIUM": "⭐", "LOW": "⚠️"}.get(session_quality, "")
        
        # Run info
        run_source = decision.get("run_source", "unknown")
        run_source_text = {
            "scheduled": "Scheduled run",
            "manual": "Manual",
            "workflow_dispatch": "Manual",
            "schedule": "Scheduled run"
        }.get(str(run_source), str(run_source))
        
        # Timestamp
        timestamp = self._now_text()
        
        # Build agent votes table (all 5 agents + Groq)
        agents_data = self._build_agent_votes(decision, all_results={})
        agent_votes_text = self._format_agent_votes(agents_data)
        
        # Why this trade (consolidated reasons, no repetition)
        why_trade = self._build_why_trade(decision)
        
        # Risk & Invalidation (one-liner each, no paragraphs)
        risk_note = self._build_risk_note(decision)
        invalidation = self._build_invalidation(decision)
        
        # Trade ID & Mode
        trade_id = decision.get("trade_id", signal.get("trade_id", "not saved yet"))
        trading_mode = str(decision.get("trading_mode", signal.get("trading_mode", "paper"))).lower()
        paper_trading = bool(decision.get("paper_trading", signal.get("paper_trading", trading_mode == "paper")))
        mode_text = "Paper Trading" if paper_trading else "Live/Manual Tracking"
        
        # Build final message
        text = f"""
📊 <b>XAU/USD SIGNAL — {direction_text}</b>
━━━━━━━━━━━━━━━━━━━━━
{timestamp} · {session_name} {session_emoji} · {run_source_text}
{format_price(current_price)} · Confidence {confidence}% · Quality {quality_grade} ({quality_score:.0f}%)

<b>ENTRY ZONE</b>   {format_price(entry_low)} – {format_price(entry_high)}
<b>STOP LOSS</b>    {format_price(signal.get('stop_loss'))}
<b>TAKE PROFIT</b>  {format_price(signal.get('tp1'))} (TP1, R:R {float(signal.get('rr_ratio', 0)):.2f}x) · {format_price(signal.get('tp2'))} (TP2)

<b>AGENT VOTES</b>
{agent_votes_text}

<b>WHY THIS TRADE</b>
{why_trade}

<b>RISK NOTE</b>   {risk_note}
<b>INVALIDATION</b>  {invalidation}

🔄 Mode: {mode_text} · 🆔 ID: <code>{html.escape(str(trade_id))}</code>
⚠️ Educational signal only — not financial advice.
""".strip()
        return self.send_message(text, urgent=True)

    def _build_agent_votes(self, decision: Dict[str, Any], all_results: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Extract votes from all 5 agents + Groq."""
        agents_data = {}
        
        # 5 main agents
        agent_names = ["Technical", "Classical", "SMC", "Price Action", "Multi-Timeframe"]
        for agent_name_display in agent_names:
            agent_key = agent_name_display.lower().replace(" ", "_").replace("-", "_")
            agent_result = all_results.get(agent_key, decision.get(agent_key, {}))
            
            vote = "WAIT"
            confidence = 0
            if isinstance(agent_result, dict):
                vote = str(agent_result.get("signal", "WAIT")).upper()
                confidence = int(agent_result.get("confidence", 0))
            
            agents_data[agent_name_display] = {"vote": vote, "confidence": confidence}
        
        # Groq final vote
        ai = decision.get("ai", {}) or {}
        if ai.get("available"):
            groq_vote = str(decision.get("decision", "WAIT")).upper()
            groq_confidence = int(decision.get("confidence", 0))
            agents_data["Groq (final)"] = {"vote": groq_vote, "confidence": groq_confidence}
        
        return agents_data

    def _format_agent_votes(self, agents_data: Dict[str, Dict[str, Any]]) -> str:
        """Format agent votes as a clean list."""
        lines = []
        for agent_name, data in agents_data.items():
            vote = data["vote"]
            confidence = data["confidence"]
            vote_emoji = "🟢" if vote == "BUY" else "🔴" if vote == "SELL" else "🟡"
            lines.append(f"• {agent_name:<20} {vote_emoji} {vote:<6} {confidence:>3}%")
        return "\n".join(lines)

    def _build_why_trade(self, decision: Dict[str, Any]) -> str:
        """Consolidated reasons section (no repetition from classical + Groq)."""
        reasons = decision.get("reasons", [])
        ai = decision.get("ai", {}) or {}
        
        # Collect unique reason bullets
        reason_bullets = []
        
        # Add top classical reasons (first 2)
        if isinstance(reasons, list):
            for r in reasons[:2]:
                reason_str = str(r).strip()
                if reason_str and reason_str not in reason_bullets:
                    reason_bullets.append(reason_str)
        
        # Add Groq entry reason (if different from classical)
        if ai.get("entry_reason"):
            groq_entry = str(ai.get("entry_reason")).strip()
            if groq_entry and groq_entry not in reason_bullets:
                reason_bullets.append(groq_entry)
        
        # Add key supportive evidence (max 2)
        supportive = ai.get("supportive_evidence") or []
        if isinstance(supportive, list):
            for item in supportive[:2]:
                item_str = str(item).strip()
                if item_str and item_str not in reason_bullets:
                    reason_bullets.append(item_str)
        
        # Format as bullets
        if reason_bullets:
            return "\n".join(f"• {bullet}" for bullet in reason_bullets[:4])
        return "• Multi-agent consensus on direction"

    def _build_risk_note(self, decision: Dict[str, Any]) -> str:
        """One-liner risk note, not a paragraph."""
        ai = decision.get("ai", {}) or {}
        risk_notes = ai.get("risk_notes")
        if risk_notes:
            return str(risk_notes).strip()[:150]
        
        signal = decision.get("signal", {})
        tp1 = signal.get("tp1")
        if tp1:
            return f"Resistance at {format_price(tp1)} is target, not entry trigger"
        return "Follow trade management plan"

    def _build_invalidation(self, decision: Dict[str, Any]) -> str:
        """One-liner invalidation, not a paragraph."""
        ai = decision.get("ai", {}) or {}
        invalidation = ai.get("invalidation")
        if invalidation:
            return str(invalidation).strip()[:150]
        
        signal = decision.get("signal", {})
        sl = signal.get("stop_loss")
        if sl:
            return f"Close below {format_price(sl)}"
        return "Follow stop loss rules"

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
            "TRAILING_SL_UPDATED": "📈 Trailing Stop Moved",
            "TRAILING_SL_HIT": "🔒 Trailing Stop Hit (Profit Locked)",
        }
        title = event_titles.get(event_type, "🔄 Trade Update")
        pnl_emoji = "✅" if pnl_points > 0 else "➖" if pnl_points == 0 else "❌"
        old_status = evaluation.get("old_status", trade.get("status", "OPEN"))
        new_status = evaluation.get("new_status", old_status)
        progress = evaluation.get("progress_to_tp1")
        hours_open = evaluation.get("hours_open")
        note = self._trade_event_note(event_type, trade, current_price, evaluation)
        extra_lines = []
        if progress is not None:
            extra_lines.append(f"📊 <b>Progress to TP1:</b> {float(progress) * 100:.0f}%")
        if hours_open is not None:
            extra_lines.append(f"⏱ <b>Duration:</b> {float(hours_open):.1f} hours")
        extra_text = "\n".join(extra_lines)

        text = f"""
{title} - <b>XAU/USD</b>
━━━━━━━━━━━━━━━━━━━━━

🆔 <b>ID:</b> <code>{html.escape(str(trade.get('id')))}</code>
📊 <b>Type:</b> {html.escape(str(trade.get('type')))}
📍 <b>Entry:</b> {format_price(trade.get('entry_price'))}
🛑 <b>Stop Loss:</b> {format_price(trade.get('stop_loss'))}
🎯 <b>TP1:</b> {format_price(trade.get('tp1'))}
🎯 <b>TP2:</b> {format_price(trade.get('tp2'))}
💰 <b>Current Price:</b> {format_price(current_price)}
📈 <b>Current PnL:</b> {pnl_points:+.1f} pts {pnl_emoji}
📌 <b>Status:</b> {html.escape(str(old_status))} → {html.escape(str(new_status))}
{extra_text}

{note}

⚠️ Educational paper-trading update only. Not financial advice.
""".strip()
        return self.send_message(text, urgent=event_type in {"TP1_HIT", "TP2_HIT", "SL_HIT", "BE_HIT", "EXPIRED", "TRAILING_SL_HIT"})

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
        if event_type == "TRAILING_SL_UPDATED":
            new_sl = evaluation.get("updates", {}).get("stop_loss")
            return f"📈 Trailing stop moved to {format_price(new_sl)} to lock in more profit as price advances."
        if event_type == "TRAILING_SL_HIT":
            return "🔒 Price pulled back to the trailed stop - the locked-in profit beyond breakeven has been secured."
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
