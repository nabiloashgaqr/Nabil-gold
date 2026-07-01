"""Telegram service for signal and update messages."""

from __future__ import annotations

import html
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

from utils.helpers import format_price, load_config


class TelegramService:
    API_BASE = "https://api.telegram.org/bot{token}/{method}"

    EVENT_LABELS = {
        "ORDER_FILLED": "Order Filled",
        "NEAR_TP1": "Near TP1",
        "TP1_HIT": "Take Profit 1 Hit",
        "TP2_HIT": "Take Profit 2 Hit",
        "SL_HIT": "Stop Loss Hit",
        "TRAILING_SL_HIT": "Trailing Stop Hit",
        "BE_HIT": "Breakeven Hit",
        "MOVE_SL_TO_BE": "SL Moved to Breakeven",
        "TRAILING_SL_UPDATED": "Trailing Stop Updated",
        "LONG_RUNNING": "Long-running Trade",
        "EXIT_WARNING": "Exit / Risk Warning",
        "EXPIRED": "Trade Expired",
        "MANUAL_CLOSE": "Manual Close",
    }
    EVENT_PRIORITY = [
        "TP2_HIT", "TRAILING_SL_HIT", "SL_HIT", "BE_HIT", "TP1_HIT",
        "MOVE_SL_TO_BE", "TRAILING_SL_UPDATED", "ORDER_FILLED", "EXIT_WARNING",
        "LONG_RUNNING", "NEAR_TP1", "EXPIRED", "MANUAL_CLOSE",
    ]

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        c = self.config.get("telegram", {})
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or c.get("bot_token")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID") or c.get("chat_id")
        self.session = requests.Session()

    def send_message(self, text: str, urgent: bool = False, chat_id: str | None = None) -> bool:
        if not self.bot_token or not (chat_id or self.chat_id):
            return False
        url = self.API_BASE.format(token=self.bot_token, method="sendMessage")
        payload = {
            "chat_id": chat_id or self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = self.session.post(url, json=payload, timeout=20)
            return resp.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _money(value: Any, symbol: str = "XAU/USD") -> str:
        return format_price(value, symbol)

    @staticmethod
    def _clean_text(value: Any) -> str:
        text = html.escape(str(value or ""))
        return " ".join(text.split())

    @staticmethod
    def _friendly_signal_text(value: Any) -> str:
        text = str(value or "")
        text = text.replace(
            "Buy-side liquidity sweep detected (STRONG) - bearish after sweep",
            "Sweep above recent highs detected (STRONG) - bearish reversal context",
        )
        text = text.replace(
            "Sell-side liquidity sweep detected (STRONG) - bullish after sweep",
            "Sweep below recent lows detected (STRONG) - bullish reversal context",
        )
        return html.escape(text)

    @staticmethod
    def _numbers(text: Any) -> List[float]:
        return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(text or ""))]

    def _should_show_invalidation(self, invalidation: Any, stop_loss: Any) -> bool:
        text = str(invalidation or "").strip()
        if not text:
            return False
        nums = self._numbers(text)
        try:
            sl = float(stop_loss)
        except (TypeError, ValueError):
            sl = None
        # Hide invalidation only when it is just the SL price repeated.
        if sl is not None and nums and any(abs(n - sl) < 0.02 for n in nums):
            structural_words = {"structure", "block", "break", "close", "sweep"}
            lower = text.lower()
            if not any(word in lower for word in structural_words - {"close"}):
                return False
        return True

    def _votes_lines(self, decision: Dict[str, Any]) -> List[str]:
        votes = decision.get("votes") or {}
        agent_details = decision.get("agent_details") or {}
        if not votes and not agent_details:
            return []
        lines = ["🗳️ <b>AGENT VOTES</b>"]
        marker = {"BUY": "🟢", "SELL": "🔴", "WAIT": "⚪", "NEUTRAL": "⚪"}
        for direction in ("BUY", "SELL", "WAIT"):
            entries = votes.get(direction) or []
            if not entries:
                continue
            parts = []
            for item in entries:
                if isinstance(item, dict):
                    agent = item.get("agent", "agent")
                    conf = item.get("confidence")
                    parts.append(f"{agent}{f' {conf}%' if conf is not None else ''}")
                else:
                    parts.append(str(item))
            lines.append(f"{marker.get(direction, '⚪')} <b>{direction}:</b> {html.escape(', '.join(parts))}")
        for detail in agent_details.values():
            if not isinstance(detail, dict):
                continue
            signals = detail.get("signals") or []
            if not signals:
                continue
            label = detail.get("label") or "Agent"
            direction = str(detail.get("direction") or "WAIT").upper()
            prefix = marker.get(direction, "⚪")
            lines.append(f"{prefix} <b>{html.escape(str(label))} notes:</b>")
            for sig in signals[:3]:
                lines.append(f"  • {self._friendly_signal_text(sig)}")
        return lines

    def send_signal(self, decision: Dict[str, Any]) -> bool:
        symbol = str(decision.get("symbol", "XAU/USD"))
        signal = decision.get("signal", {}) or {}
        trade_type = str(decision.get("decision") or signal.get("type") or "WAIT").upper()
        emoji = "🟢" if trade_type == "BUY" else "🔴" if trade_type == "SELL" else "🟡"
        ai = decision.get("ai") or {}
        risk = decision.get("risk") or {}
        dynamic = decision.get("dynamic_risk") or {}
        quality = decision.get("quality") or {}
        entry = signal.get("entry", {}) or {}

        lines: List[str] = [
            f"{emoji} <b>{html.escape(symbol)} SIGNAL — {html.escape(trade_type)}</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"📈 Price {self._money(decision.get('current_price'), symbol)} · Confidence {decision.get('confidence')}%",
            f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        if quality:
            lines.append(f"🏅 Quality: {html.escape(str(quality.get('grade', '')))} {html.escape(str(quality.get('score', '')))}".rstrip())
        lines.extend([
            "──────────────────",
            "🎯 <b>TRADE PLAN</b>",
            f"• <b>Order:</b> {html.escape(trade_type)} Market",
            f"• <b>Entry:</b> {self._money(entry.get('price') or decision.get('current_price'), symbol)}",
            f"• <b>Stop Loss:</b> {self._money(signal.get('stop_loss'), symbol)}",
            f"• <b>TP1:</b> {self._money(signal.get('tp1'), symbol)}",
            f"• <b>TP2:</b> {self._money(signal.get('tp2'), symbol)}",
            "• <b>Management:</b> SL → entry after +100 pts · Trail gap 100 pts / step 30 pts · check 5m",
        ])

        vote_lines = self._votes_lines(decision)
        if vote_lines:
            lines.append("──────────────────")
            lines.extend(vote_lines)

        context_lines: List[str] = []
        if ai.get("available"):
            if ai.get("entry_reason"):
                context_lines.append(f"• AI reason: {self._clean_text(ai.get('entry_reason'))}")
            if ai.get("risk_notes"):
                context_lines.append(f"• Risk note: {self._clean_text(ai.get('risk_notes'))}")
            if self._should_show_invalidation(ai.get("invalidation"), signal.get("stop_loss")):
                context_lines.append(f"• Invalidation: {self._clean_text(ai.get('invalidation'))}")
        daily_bias = decision.get("daily_bias") or {}
        if daily_bias and str(daily_bias.get("bias", "NEUTRAL")).upper() != "NEUTRAL":
            bias_conf = daily_bias.get("confidence")
            bias_conf_text = f" ({bias_conf}%)" if bias_conf is not None else ""
            context_lines.append(
                f"• Daily bias: {html.escape(str(daily_bias.get('bias')))}{html.escape(bias_conf_text)}"
            )
        if dynamic and str(dynamic.get("level", "NORMAL")).upper() != "NORMAL":
            context_lines.append(f"• Dynamic risk: {html.escape(str(dynamic.get('level')))}")
        # Keep risk details compact and useful; skip empty optional risk block.
        sl_dist = ((risk.get("stop_loss") or {}) if isinstance(risk, dict) else {}).get("distance_points")
        if sl_dist:
            context_lines.append(f"• SL distance: {html.escape(str(sl_dist))} pts")
        if context_lines:
            lines.append("──────────────────")
            lines.append("⚠️ <b>RISK / CONTEXT</b>")
            lines.extend(context_lines)

        gemini = decision.get("gemini_review", {}) or {}
        if gemini.get("available"):
            lines.append("──────────────────")
            lines.append("🧠 <b>GEMINI INDEPENDENT REVIEW</b>")
            lines.append(f"• <b>Opinion:</b> {self._clean_text(gemini.get('verdict'))} - {self._clean_text(gemini.get('reason'))}")

        gemini_news = decision.get("gemini_news_review", {}) or {}
        if gemini_news.get("available"):
            risk_level = str(gemini_news.get("risk_level") or "LOW").upper()
            bullets = [str(x) for x in (gemini_news.get("summary_bullets") or []) if str(x).strip()]
            advice = str(gemini_news.get("trading_advice") or "").strip()
            if risk_level in {"MEDIUM", "HIGH", "EXTREME"} or bullets or advice:
                lines.append("──────────────────")
                lines.append("📰 <b>GEMINI NEWS CHECK</b>")
                lines.append(f"• <b>Risk:</b> {html.escape(risk_level)}")
                for bullet in bullets[:2]:
                    lines.append(f"• {self._clean_text(bullet)}")
                if advice:
                    lines.append(f"• Advice: {self._clean_text(advice)}")

        reasons = decision.get("reasons") or []
        lines.append("──────────────────")
        lines.append("💡 <b>WHY THIS TRADE</b>")
        for r in reasons[:4]:
            lines.append(f"• {self._friendly_signal_text(r)}")

        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━━",
            "<i>Paper trading signal; not financial advice.</i>",
            f"<i>ID: {html.escape(str(decision.get('trade_id') or 'N/A'))}</i>",
        ])
        text = "\n".join(line for line in lines if str(line).strip())
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
        return self.send_message(text)

    def _event_title(self, events: List[str]) -> str:
        for event in self.EVENT_PRIORITY:
            if event in events:
                return self.EVENT_LABELS.get(event, event.replace("_", " ").title())
        return self.EVENT_LABELS.get(events[0], events[0].replace("_", " ").title()) if events else "Trade Update"

    def _event_notes(self, events: List[str]) -> List[str]:
        notes = []
        if "EXIT_WARNING" in events:
            notes.append("Exit/risk warning: trade is moving adversely or risk conditions changed.")
        if "LONG_RUNNING" in events:
            notes.append("Trade has been open for a long time; monitor closely.")
        if "MOVE_SL_TO_BE" in events:
            notes.append("Stop loss moved to entry / breakeven protection.")
        if "TRAILING_SL_UPDATED" in events:
            notes.append("Trailing stop updated using 100-point gap / 30-point step.")
        if "TP1_HIT" in events:
            notes.append("TP1 reached; partial profit/protection rules applied.")
        if "TP2_HIT" in events:
            notes.append("Final target reached.")
        if "SL_HIT" in events:
            notes.append("Stop loss was hit.")
        if "TRAILING_SL_HIT" in events:
            notes.append("Trailing stop was hit.")
        if "BE_HIT" in events:
            notes.append("Breakeven stop was hit.")
        if "ORDER_FILLED" in events:
            notes.append("Pending order filled and is now active.")
        if "EXPIRED" in events:
            notes.append("Trade/order expired by time rule.")
        return notes

    @staticmethod
    def _fmt_points(value: Any) -> str:
        try:
            return f"{float(value):+.1f} pts"
        except (TypeError, ValueError):
            return "+0.0 pts"

    def send_trade_events(
        self,
        trade: Dict[str, Any],
        events: List[str],
        current_price: float,
        pnl_points: float,
        evaluation: Dict[str, Any],
    ) -> bool:
        if not events:
            return False
        title = self._event_title(events)
        side = str(trade.get("type") or trade.get("side") or "").upper()
        symbol = str(trade.get("symbol") or "XAU/USD")
        old_status = str(evaluation.get("old_status") or trade.get("status") or "OPEN")
        new_status = str(evaluation.get("new_status") or old_status)
        updates = evaluation.get("updates") or {}
        closing = any(e in events for e in {"TP2_HIT", "SL_HIT", "TRAILING_SL_HIT", "BE_HIT", "EXPIRED", "MANUAL_CLOSE"})

        lines = [
            f"🔔 <b>{html.escape(title)}</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"• <b>Trade:</b> {html.escape(str(trade.get('id', 'N/A')))}",
            f"• <b>Symbol:</b> {html.escape(symbol)}",
            f"• <b>Side:</b> {html.escape(side)}",
        ]
        if old_status != new_status:
            lines.append(f"• <b>Status:</b> {html.escape(old_status)} → {html.escape(new_status)}")
        else:
            lines.append(f"• <b>Status:</b> {html.escape(new_status)}")
        lines.extend([
            f"• <b>Entry:</b> {self._money(trade.get('entry_price'), symbol)}",
            f"• <b>Current Price:</b> {self._money(current_price, symbol)}",
        ])
        if closing:
            close_price = updates.get("close_price") or updates.get("stop_loss") or current_price
            actual = updates.get("final_pnl", pnl_points)
            lines.append(f"• <b>Exit Price:</b> {self._money(close_price, symbol)}")
            lines.append(f"• <b>Actual PnL:</b> {self._fmt_points(actual)}")
        else:
            lines.append(f"• <b>Current PnL:</b> {self._fmt_points(pnl_points)}")

        progress = evaluation.get("progress_to_tp1")
        if progress is not None:
            try:
                p = float(progress)
                progress_text = "completed" if p >= 1 else f"{max(0, min(p, 1)) * 100:.0f}%"
                lines.append(f"• <b>TP1 Progress:</b> {progress_text}")
            except (TypeError, ValueError):
                pass
        if "TRAILING_SL_UPDATED" in events:
            new_sl = updates.get("stop_loss")
            if new_sl is not None:
                try:
                    locked = abs(float(new_sl) - float(trade.get("entry_price", new_sl))) / 0.10
                    lines.append(f"• <b>New SL:</b> {self._money(new_sl, symbol)} — locking about +{locked:.0f} pts")
                except (TypeError, ValueError):
                    lines.append(f"• <b>New SL:</b> {self._money(new_sl, symbol)}")
            lines.append("• <b>Trailing rule:</b> 100-point gap / 30-point step")
        notes = self._event_notes(events)
        if notes:
            lines.append("──────────────────")
            for note in notes:
                lines.append(f"• {html.escape(note)}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        return self.send_message("\n".join(lines), urgent=True)

    def send_trade_event(
        self,
        trade: Dict[str, Any],
        event: str,
        current_price: float,
        pnl_points: float,
        evaluation: Dict[str, Any],
    ) -> bool:
        return self.send_trade_events(trade, [event], current_price, pnl_points, evaluation)

    def send_error_alert(self, message: str) -> bool:
        text = (
            "🚨 <b>SmartSignal Error</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"{html.escape(str(message))[:1200]}"
        )
        return self.send_message(text, urgent=True)

    def send_daily_report(self, report: str) -> bool:
        text = html.escape(str(report))
        # Preserve report line breaks after escaping.
        text = text.replace("\n", "\n")
        return self.send_message(text)
