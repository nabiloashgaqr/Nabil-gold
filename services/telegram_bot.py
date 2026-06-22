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

    # Canonical order of the five voting analysis agents.
    VOTING_AGENTS = (
        ("technical", "Technical"),
        ("classical", "Classical"),
        ("smc", "SMC"),
        ("price_action", "Price Action"),
        ("multitimeframe", "Multi-Timeframe"),
    )

    def send_signal(self, decision: Dict[str, Any]) -> bool:
        """Format and send a new trade signal in clean, section-based English.

        The layout deliberately consolidates everything into a few labelled
        sections instead of a long stack of one-off rows:
          * header (instrument, direction, time, session, run source)
          * one price/confidence/quality line
          * ENTRY / STOP / TAKE PROFIT block (with per-TP R:R)
          * AGENT VOTES table (all five analysis agents + the Groq final gate)
          * WHY THIS TRADE (a single merged rationale, de-duplicated)
          * RISK NOTE / INVALIDATION (one line each, only if present)
          * compact footer (mode, decision rule, disclaimer, id)
        """
        signal = decision.get("signal", {}) or {}
        trade_type = str(decision.get("decision", signal.get("type", "WAIT"))).upper()
        emoji = "🟢" if trade_type == "BUY" else "🔴" if trade_type == "SELL" else "🟡"
        entry = signal.get("entry", {}) or {}
        entry_low = entry.get("low", entry.get("price", 0))
        entry_high = entry.get("high", entry.get("price", 0))
        current_price = decision.get("current_price", signal.get("current_price", entry.get("price", 0)))
        ai = decision.get("ai", {}) or {}

        # ── Header: time · session · run source ────────────────────────────
        header_bits: List[str] = [self._now_text()]
        session_info = decision.get("session_info", {}) or {}
        if session_info.get("current_session"):
            sq = str(session_info.get("session_quality", "UNKNOWN"))
            quality_emoji = {"BEST": "⭐⭐⭐", "HIGH": "⭐⭐", "MEDIUM": "⭐", "LOW": "⚠️"}.get(sq, "")
            header_bits.append(f"{html.escape(str(session_info.get('current_session')))} {quality_emoji}".strip())
        run_source = decision.get("run_source", "")
        run_source_text = {"scheduled": "Scheduled run", "manual": "Manual run", "workflow_dispatch": "Manual run", "schedule": "Scheduled run"}.get(str(run_source), "")
        if run_source_text:
            header_bits.append(run_source_text)
        header_line = " · ".join(b for b in header_bits if b)

        # ── Price / confidence / quality (single line) ─────────────────────
        confidence = int(float(decision.get("confidence", 0) or 0))
        quality = decision.get("quality", {}) or {}
        snapshot_bits = [f"Price {format_price(current_price)}", f"Confidence {confidence}%"]
        if quality.get("grade"):
            snapshot_bits.append(f"Quality {html.escape(str(quality.get('grade')))} ({float(quality.get('score', 0)):.0f}%)")
        snapshot_line = " · ".join(snapshot_bits)

        # ── Targets with per-TP R:R (read from the real risk payload) ───────
        tp = ((decision.get("risk", {}) or {}).get("take_profit", {}) or {})
        tp1_rr = (tp.get("tp1", {}) or {}).get("rr_ratio")
        tp2_rr = (tp.get("tp2", {}) or {}).get("rr_ratio")
        if tp1_rr is None and tp2_rr is None:
            tp2_rr = signal.get("rr_ratio")
        sl_points = (((decision.get("risk", {}) or {}).get("stop_loss", {}) or {}).get("distance_points"))
        sl_suffix = f"  ({float(sl_points):.0f} pts)" if sl_points else ""
        tp1_txt = self._tp_text(signal.get("tp1"), "TP1", tp1_rr)
        tp2_txt = self._tp_text(signal.get("tp2"), "TP2", tp2_rr)
        tp_line = " · ".join(t for t in (tp1_txt, tp2_txt) if t) or "—"

        # ── Agent votes table (all five + Groq final gate) ─────────────────
        votes_block = self._format_agent_votes(decision, ai, trade_type, confidence)

        # ── WHY THIS TRADE — single merged, de-duplicated rationale ────────
        why_block = self._format_why_this_trade(decision, ai)

        # ── RISK NOTE / INVALIDATION (one line each, only if meaningful) ───
        extra_lines: List[str] = []
        risk_notes = self._clean_ai_field(ai.get("risk_notes"))
        if risk_notes:
            extra_lines.append(f"<b>RISK NOTE</b>   {risk_notes}")
        invalidation = self._clean_ai_field(ai.get("invalidation"))
        if invalidation:
            extra_lines.append(f"<b>INVALIDATION</b>  {invalidation}")
        # Dynamic risk: only surface when it actually changes behaviour.
        # (Daily bias is already covered inside WHY THIS TRADE when it agrees,
        #  and as a counter-trend warning here only when it opposes the trade.)
        daily_bias = decision.get("daily_bias", {}) or {}
        bias = str(daily_bias.get("bias", "NEUTRAL")).upper()
        direction = str(decision.get("decision", "")).upper()
        opposes = (bias == "BULLISH" and direction == "SELL") or (bias == "BEARISH" and direction == "BUY")
        if opposes and daily_bias.get("confidence"):
            extra_lines.append(f"<b>DAILY BIAS</b>   ⚠️ Counter-trend vs {html.escape(bias)} ({float(daily_bias.get('confidence', 0)):.0f}%)")
        dynamic_risk = decision.get("dynamic_risk", {}) or {}
        dr_level = str(dynamic_risk.get("level", "NORMAL")).upper()
        if dr_level and dr_level != "NORMAL":
            extra_lines.append(f"<b>DYNAMIC RISK</b>  {html.escape(dr_level)}")
        extra_block = ("\n".join(extra_lines) + "\n\n") if extra_lines else ""  # trailing blank kept for footer spacing

        # ── Footer ─────────────────────────────────────────────────────────
        trading_mode = str(decision.get("trading_mode", signal.get("trading_mode", "paper"))).lower()
        paper_trading = bool(decision.get("paper_trading", signal.get("paper_trading", trading_mode == "paper")))
        mode_text = "Paper Trading" if paper_trading else "Live / Manual Tracking"
        decision_mode = decision.get("decision_mode", "")
        if decision.get("one_agent_groq_mode") or str(decision_mode) == "One-Agent + Groq":
            rule_text = "One-Agent + Groq"
        elif decision_mode:
            rule_text = str(decision_mode)
        else:
            rule_text = "Groq final gate"
        trade_id = decision.get("trade_id", signal.get("trade_id", "not saved yet"))

        text = f"""
📊 <b>XAU/USD SIGNAL — {trade_type}</b> {emoji}
━━━━━━━━━━━━━━━━━━━━━
{html.escape(header_line)}
{snapshot_line}

<b>ENTRY ZONE</b>   {format_price(entry_low)} – {format_price(entry_high)}
<b>STOP LOSS</b>    {format_price(signal.get('stop_loss'))}{sl_suffix}
<b>TAKE PROFIT</b>  {tp_line}

{votes_block}

{why_block}

{extra_block}<i>Mode: {mode_text} · Decision: {html.escape(rule_text)}</i>
<i>Educational signal only — not financial advice.</i>  ID: <code>{html.escape(str(trade_id))}</code>
""".strip()
        return self.send_message(text, urgent=True)

    # ------------------------------------------------------------------ #
    # send_signal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tp_text(price: Any, label: str, rr: Any) -> str:
        """Render a single take-profit entry, including R:R when known."""
        if price in (None, 0, "", "0"):
            return ""
        try:
            rr_val = float(rr) if rr not in (None, "", 0) else None
        except (TypeError, ValueError):
            rr_val = None
        rr_suffix = f", R:R {rr_val:.2f}" if rr_val else ""
        return f"{format_price(price)} ({label}{rr_suffix})"

    @staticmethod
    def _clean_ai_field(value: Any) -> str:
        """Return a one-line, escaped AI field, or '' for empty/placeholder text."""
        if value is None:
            return ""
        text = " ".join(str(value).split())
        if not text or text.upper() in {"N/A", "NONE", "NULL", "-"}:
            return ""
        return html.escape(text)

    def _format_agent_votes(self, decision: Dict[str, Any], ai: Dict[str, Any], final_type: str, final_conf: int) -> str:
        """Build the AGENT VOTES table for all five analysis agents + Groq.

        Reads the structured ``votes`` produced by DecisionAgent, where each
        agent appears under its decided direction with its confidence. Agents
        that produced no vote are shown as WAIT/—.
        """
        votes = decision.get("votes", {}) or {}
        # Flatten votes -> {agent_name: (signal, confidence)}
        per_agent: Dict[str, tuple] = {}
        for side in ("BUY", "SELL", "WAIT"):
            for v in votes.get(side, []) or []:
                name = str(v.get("agent", "")).lower()
                if name:
                    per_agent[name] = (side, v.get("confidence"))

        lines = ["<b>AGENT VOTES</b>"]
        for key, label in self.VOTING_AGENTS:
            side, conf = per_agent.get(key, ("WAIT", None))
            conf_txt = f"{int(float(conf))}%" if conf not in (None, "") else "—"
            lines.append(f"• {label:<16} {side:<4} {conf_txt}")

        if ai.get("available"):
            ai_signal = str(ai.get("signal", final_type) or final_type).upper()
            ai_conf = ai.get("confidence", final_conf)
            try:
                ai_conf_txt = f"{int(float(ai_conf))}%"
            except (TypeError, ValueError):
                ai_conf_txt = f"{final_conf}%"
            lines.append(f"• {'Groq (final)':<16} {ai_signal:<4} {ai_conf_txt}  ← decision gate")
        return "\n".join(lines)

    def _format_why_this_trade(self, decision: Dict[str, Any], ai: Dict[str, Any]) -> str:
        """Merge the multiple overlapping rationale sources into one section.

        Previously the message repeated essentially the same idea up to three
        times (classical reasoning + risk summary + Groq entry_reason + Groq
        supportive_evidence). Here we collect candidate bullet points from the
        strongest sources, normalise them, drop near-duplicates, and cap the
        list so the section stays short.
        """
        candidates: List[str] = []

        # 1) Groq's primary entry rationale (usually the richest single line).
        entry_reason = self._clean_ai_field(ai.get("entry_reason"))
        if entry_reason:
            candidates.append(entry_reason)

        # 2) Groq's explicit supportive evidence bullets.
        supportive = ai.get("supportive_evidence") or ai.get("evidence") or []
        if isinstance(supportive, (list, tuple)):
            for item in supportive:
                cleaned = self._clean_ai_field(item)
                if cleaned:
                    candidates.append(cleaned)
        elif supportive:
            cleaned = self._clean_ai_field(supportive)
            if cleaned:
                candidates.append(cleaned)

        # 3) Daily-bias alignment, only when it agrees with the trade.
        daily_bias = decision.get("daily_bias", {}) or {}
        bias = str(daily_bias.get("bias", "NEUTRAL")).upper()
        direction = str(decision.get("decision", "")).upper()
        aligned = (bias == "BULLISH" and direction == "BUY") or (bias == "BEARISH" and direction == "SELL")
        if aligned and daily_bias.get("confidence"):
            candidates.append(f"Daily bias aligned: {direction} ({float(daily_bias.get('confidence', 0)):.0f}%)")

        # 4) Agreement count among the five analysis agents.
        votes = decision.get("votes", {}) or {}
        side_votes = votes.get(direction, []) if direction in {"BUY", "SELL"} else []
        agree = len(side_votes)
        total = sum(len(votes.get(s, []) or []) for s in ("BUY", "SELL", "WAIT"))
        if direction in {"BUY", "SELL"} and agree and total:
            candidates.append(f"{agree} of {total} agents agree on direction")

        # 5) Fallback to the classical reasoning summary if Groq gave nothing.
        if not candidates:
            for reason in decision.get("reasons", []) or []:
                cleaned = self._clean_ai_field(reason)
                if cleaned:
                    candidates.append(cleaned)

        # De-duplicate (case-insensitive, ignoring trivial differences).
        seen: set = set()
        merged: List[str] = []
        for c in candidates:
            key = "".join(ch for ch in c.lower() if ch.isalnum())
            if not key or key in seen:
                continue
            # Skip a candidate that is a substring of one we already kept.
            if any(key in s or s in key for s in seen):
                continue
            seen.add(key)
            merged.append(c)
            if len(merged) >= 4:
                break

        if not merged:
            return "<b>WHY THIS TRADE</b>\n• No detailed rationale available"
        body = "\n".join(f"• {m}" for m in merged)
        return f"<b>WHY THIS TRADE</b>\n{body}"

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
            extra_lines.append(f"⏱ <b>Time open:</b> {float(hours_open):.1f}h")
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
