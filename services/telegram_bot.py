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
                # Telegram returns a JSON body with a useful "description" even on
                # 4xx errors; surface it instead of a bare "400 Bad Request".
                try:
                    result = response.json()
                except ValueError:
                    result = {}
                if response.status_code >= 400 or not result.get("ok", False):
                    description = result.get("description", "") if isinstance(result, dict) else ""
                    error_code = result.get("error_code", response.status_code) if isinstance(result, dict) else response.status_code
                    # A 400 caused by malformed HTML entities is recoverable:
                    # retry once as plain text so the message still gets delivered.
                    if (
                        response.status_code == 400
                        and payload.get("parse_mode")
                        and ("parse" in description.lower() or "ent\u200bities" in description.lower() or "entities" in description.lower() or "tag" in description.lower())
                    ):
                        self.logger.warning("Telegram HTML parse error (%s); retrying as plain text.", description)
                        plain = dict(payload)
                        plain.pop("parse_mode", None)
                        retry = self.session.post(url, json=plain, timeout=20)
                        if retry.ok and retry.json().get("ok", False):
                            return True
                    raise RuntimeError(f"Telegram API error {error_code}: {description or response.text[:300]}")
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
          * AGENT VOTES table (all five analysis agents)
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
        entry_price = entry.get("price", 0)
        current_price = decision.get("current_price", signal.get("current_price", entry.get("price", 0)))
        ai = decision.get("ai", {}) or {}

        # ── Smart entry execution (MARKET / LIMIT / STOP) ──────────────────
        order_type = str(signal.get("order_type", entry.get("order_type", f"{trade_type}_MARKET"))).upper()
        entry_kind = str(signal.get("entry_kind", entry.get("kind", "")) or "").upper()
        if not entry_kind:
            entry_kind = "MARKET" if order_type.endswith("MARKET") else order_type.split("_")[-1]
        entry_basis = self._clean_ai_field(entry.get("basis"))
        entry_dist = entry.get("distance_points") or 0
        # Human label + emoji for the order kind.
        kind_label = {
            "MARKET": "⚡ Market (immediate)",
            "LIMIT": "🎯 Limit (pullback)",
            "STOP": "🚀 Stop (breakout)",
        }.get(entry_kind, "⚡ Market (immediate)")

        # ── Header: time · session · run source ────────────────────────────
        header_bits: List[str] = [self._now_text()]
        session_info = decision.get("session_info", {}) or {}
        if session_info.get("current_session"):
            sq = str(session_info.get("session_quality", "UNKNOWN"))
            quality_emoji = {"BEST": "⭐⭐⭐", "HIGH": "⭐⭐", "MEDIUM": "⭐", "LOW": "⚠️"}.get(sq, "")
            header_bits.append(f"{html.escape(str(session_info.get('current_session')))} {quality_emoji}".strip())

        # Robust run_source handling (always produce clean English, never "unknown run")
        run_source = str(decision.get("run_source", "") or decision.get("operation_mode", "") or "").lower().strip()
        run_map = {
            "scheduled": "Scheduled run",
            "schedule": "Scheduled run",
            "manual": "Manual run",
            "workflow_dispatch": "Manual run",
            "observation": "Observation mode",
        }
        run_source_text = run_map.get(run_source, "Analysis run")
        header_bits.append(run_source_text)
        header_line = " · ".join(b for b in header_bits if b)

        # ── Price / confidence / quality (single line) ─────────────────────
        confidence = int(float(decision.get("confidence", 0) or 0))
        quality = decision.get("quality", {}) or {}
        snapshot_bits = [f"Price {format_price(current_price)}", f"Confidence {confidence}%"]
        if quality.get("grade"):
            snapshot_bits.append(f"Quality {html.escape(str(quality.get('grade')))} ({float(quality.get('score', 0)):.0f}%)")
        snapshot_line = " · ".join(snapshot_bits)

        # ── Targets: one line per TP, no R:R (per user preference) ──────────
        sl_points = (((decision.get("risk", {}) or {}).get("stop_loss", {}) or {}).get("distance_points"))
        sl_suffix = f"  ({float(sl_points):.0f} pts)" if sl_points else ""
        tp_lines = []
        for label, key in (("TP1", "tp1"), ("TP2", "tp2")):
            price = signal.get(key)
            if price in (None, 0, "", "0"):
                continue
            tp_lines.append(f"• <b>{label}:</b> {format_price(price)}")
        tp_block = "\n".join(tp_lines) if tp_lines else "• —"

        # ── Agent votes table (all five analysis agents) ─────────────────
        votes_block = self._format_agent_votes(decision, ai, trade_type, confidence)

        # ── WHY THIS TRADE — single merged, de-duplicated rationale ────────
        why_block = self._format_why_this_trade(decision, ai)

        # ── RISK NOTE / INVALIDATION / counter-trend (only if meaningful) ──
        extra_lines: List[str] = []
        risk_notes = self._clean_ai_field(ai.get("risk_notes"))
        if risk_notes:
            extra_lines.append(f"⚠️ <b>Risk note:</b> {risk_notes}")
        invalidation = self._clean_ai_field(ai.get("invalidation"))
        # Drop the invalidation line when it's just a restatement of the stop
        # loss. It only adds value when it
        # gives DIFFERENT information (a different price, or a candle-close
        # condition). We compare any number it contains to the SL price.
        if invalidation and not self._invalidation_is_just_stop(invalidation, signal.get("stop_loss")):
            extra_lines.append(f"🚫 <b>Invalidation:</b> {invalidation}")
        # Dynamic risk: only surface when it actually changes behaviour.
        # (Daily bias is already covered inside WHY THIS TRADE when it agrees,
        #  and as a counter-trend warning here only when it opposes the trade.)
        daily_bias = decision.get("daily_bias", {}) or {}
        bias = str(daily_bias.get("bias", "NEUTRAL")).upper()
        direction = str(decision.get("decision", "")).upper()
        opposes = (bias == "BULLISH" and direction == "SELL") or (bias == "BEARISH" and direction == "BUY")
        if opposes and daily_bias.get("confidence"):
            extra_lines.append(f"⚠️ <b>Daily bias:</b> counter-trend vs {html.escape(bias)} ({float(daily_bias.get('confidence', 0)):.0f}%)")
        dynamic_risk = decision.get("dynamic_risk", {}) or {}
        dr_level = str(dynamic_risk.get("level", "NORMAL")).upper()
        if dr_level and dr_level != "NORMAL":
            extra_lines.append(f"🛡️ <b>Dynamic risk:</b> {html.escape(dr_level)}")
        # Real newline join; rendered as its own RISK section when present.
        if extra_lines:
            risk_block = "🛡️ <b>RISK</b>\n" + "\n".join(extra_lines)
        else:
            risk_block = ""

        # ── Footer ─────────────────────────────────────────────────────────
        trading_mode = str(decision.get("trading_mode", signal.get("trading_mode", "paper"))).lower()
        paper_trading = bool(decision.get("paper_trading", signal.get("paper_trading", trading_mode == "paper")))
        mode_text = "Paper Trading" if paper_trading else "Live / Manual Tracking"
        decision_mode = decision.get("decision_mode", "")
        rule_text = str(decision_mode or "5-Agent Weighted Consensus")
        trade_id = decision.get("trade_id", signal.get("trade_id", "not saved yet"))

        # Assemble sections, dropping any empty ones so we never emit blank gaps.
        divider = "━━━━━━━━━━━━━━━━━━━━━"
        thin = "──────────────────"
        sections = [
            f"📊 <b>XAU/USD SIGNAL — {trade_type}</b> {emoji}",
            divider,
            f"🕒 {html.escape(header_line)}",
            f"📈 {snapshot_line}",
            thin,
            self._format_trade_plan(
                trade_type=trade_type,
                entry_kind=entry_kind,
                kind_label=kind_label,
                order_type=order_type,
                entry_price=entry_price,
                entry_low=entry_low,
                entry_high=entry_high,
                current_price=current_price,
                entry_basis=entry_basis,
                entry_dist=entry_dist,
                stop_loss=signal.get("stop_loss"),
                sl_suffix=sl_suffix,
                tp_block=tp_block,
                decision=decision,
            ),
            thin,
            votes_block,
            thin,
            why_block,
        ]
        if risk_block:
            sections.append(thin)
            sections.append(risk_block)
        sections.append(divider)
        sections.append(
            f"<i>Mode: {mode_text} · Decision: {html.escape(rule_text)}</i>\n"
            f"<i>Educational signal only — not financial advice.</i>\n"
            f"🆔 <code>{html.escape(str(trade_id))}</code>"
        )
        text = "\n".join(sections).strip()
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

    def _format_trade_plan(
        self,
        *,
        trade_type: str,
        entry_kind: str,
        kind_label: str,
        order_type: str,
        entry_price: Any,
        entry_low: Any,
        entry_high: Any,
        current_price: Any,
        entry_basis: str,
        entry_dist: Any,
        stop_loss: Any,
        sl_suffix: str,
        tp_block: str,
        decision: Dict[str, Any] | None = None,   # for nearest resistance etc.
    ) -> str:
        """Render the TRADE PLAN section with smart entry execution.

        For a MARKET order: show the immediate entry zone.
        For a LIMIT/STOP order: show the pending entry price AND the current
        market price, so the user knows it is a resting (pullback/breakout)
        order rather than an immediate fill.
        """
        try:
            dist_txt = f"  ({float(entry_dist):.0f} pts away)" if float(entry_dist or 0) > 0 else ""
        except (TypeError, ValueError):
            dist_txt = ""

        lines = ["🎯 <b>TRADE PLAN</b>"]
        # Order line - simplified as requested: "⚡ Sell Market" or "⚡ Buy Market"
        if entry_kind == "MARKET":
            if trade_type == "SELL":
                order_line = "• <b>Order:</b> ⚡ Sell Market"
            else:
                order_line = "• <b>Order:</b> ⚡ Buy Market"
        else:
            ot_pretty = order_type.replace("_", " ").title()
            order_line = f"• <b>Order:</b> {kind_label} — <code>{html.escape(ot_pretty)}</code>"
        lines.append(order_line)

        if entry_kind == "MARKET":
            # Market entry: single clean price only (no extra text)
            lines.append(f"• <b>Entry:</b> {format_price(entry_price)}")
        else:
            # Pending order: show the entry ZONE, the fill point inside it, and
            # the live market reference so it's clear it's a resting order.
            has_zone = entry_low not in (None, 0) and entry_high not in (None, 0) and float(entry_high) > float(entry_low)
            if has_zone:
                lines.append(f"• <b>Entry zone:</b> {format_price(entry_low)} – {format_price(entry_high)}")
                lines.append(f"• <b>Fill @</b> {format_price(entry_price)} (zone mid){dist_txt}")
            else:
                lines.append(f"• <b>Entry @</b> {format_price(entry_price)}{dist_txt}")
            lines.append(f"• <b>Market now:</b> {format_price(current_price)}")
            if entry_basis:
                lines.append(f"   <i>{entry_basis}</i>")

        lines.append(f"• <b>Stop loss:</b> {format_price(stop_loss)}{sl_suffix}")
        lines.append(self._format_management_line())

        # Nearest Resistance + distance (always try to show for better context)
        risk = decision.get("risk", {}) or {}
        levels = risk.get("key_levels", {}) or {}
        nearest_res = levels.get("nearest_resistance") or decision.get("nearest_resistance")
        if nearest_res:
            try:
                res_price = float(nearest_res)
                dist_pts = abs(res_price - float(current_price)) * 10.0
                lines.append(f"• <b>Nearest Resistance:</b> {format_price(res_price)}  ({dist_pts:.0f} pts away)")
            except (TypeError, ValueError):
                pass

        lines.append("• <b>Take profit:</b>")
        # tp_block already has its own bullet lines; indent them under the header.
        for tl in tp_block.split("\n"):
            lines.append(f"  {tl}")
        return "\n".join(lines)

    def _format_management_line(self) -> str:
        """One compact line explaining the automatic SL/trailing rules in the
        signal message itself, so the user knows how the trade will be managed
        before any later Telegram updates arrive.
        """
        ts = self.config.get("trailing_stop", {}) or {}
        schedule = self.config.get("schedule", {}) or {}
        be = float(ts.get("early_breakeven_points", 100.0) or 100.0)
        distance = float(ts.get("trailing_distance", 100.0) or 100.0)
        step = float(ts.get("trailing_step", 30.0) or 30.0)
        interval = int(schedule.get("trade_update_interval_minutes", 5) or 5)
        return (
            f"• <b>Management:</b> SL → entry after +{be:.0f} pts · "
            f"Trail gap {distance:.0f} pts / step {step:.0f} pts · check {interval}m"
        )

    @staticmethod
    def _status_text(old_status: Any, new_status: Any) -> str:
        """Render the status line. Show a transition 'A → B' only when it really
        changed; otherwise just 'A' (avoids noise like 'TP1_HIT → TP1_HIT')."""
        old = str(old_status or "OPEN")
        new = str(new_status or old)
        if old == new:
            return html.escape(new)
        return f"{html.escape(old)} → {html.escape(new)}"

    @staticmethod
    def _progress_to_tp1_text(progress: Any) -> str:
        """Render TP1 progress without ugly values above 100%.

        Once TP1 is reached, later trailing updates can naturally have progress
        >100%. Showing "130%" is noisy, so display a completed marker instead.
        """
        if progress is None:
            return ""
        try:
            pct = float(progress) * 100.0
        except (TypeError, ValueError):
            return ""
        if pct >= 100:
            return "📊 <b>TP1 Progress:</b> completed ✅"
        return f"📊 <b>Progress to TP1:</b> {max(pct, 0.0):.0f}%"

    @staticmethod
    def _clean_ai_field(value: Any) -> str:
        """Return a one-line, escaped text field, or '' for empty/placeholder text."""
        if value is None:
            return ""
        text = " ".join(str(value).split())
        if not text or text.upper() in {"N/A", "NONE", "NULL", "-"}:
            return ""
        return html.escape(text)

    @staticmethod
    def _invalidation_is_just_stop(invalidation: str, stop_loss: Any) -> bool:
        """True when the invalidation text is merely a restatement of the SL.

        Hides the redundant 'Invalidation: 4121.05' line when stop_loss is also
        4121.05. Keeps it when it carries different info: a *different* price, or
        a candle-close / structural condition (no comparable number, or a number
        far from the SL).
        """
        try:
            sl = float(stop_loss)
        except (TypeError, ValueError):
            return False
        if sl <= 0:
            return False
        import re as _re
        nums = _re.findall(r"\d+(?:\.\d+)?", str(invalidation).replace(",", ""))
        if not nums:
            # No price at all (e.g. "close below structure") -> keep it.
            return False
        # If EVERY number in the text is essentially the SL, it's redundant.
        # A "close above/below" condition at a different level is still useful.
        for n in nums:
            try:
                if abs(float(n) - sl) > 0.5:  # >5 points difference = different level
                    return False
            except ValueError:
                return False
        return True

    def _format_agent_votes(self, decision: Dict[str, Any], ai: Dict[str, Any], final_type: str, final_conf: int) -> str:
        """Build the AGENT VOTES table for all five analysis agents."""
        votes = decision.get("votes", {}) or {}
        # Flatten votes -> {agent_name: (signal, confidence)}
        per_agent: Dict[str, tuple] = {}
        for side in ("BUY", "SELL", "WAIT"):
            for v in votes.get(side, []) or []:
                name = str(v.get("agent", "")).lower()
                if name:
                    per_agent[name] = (side, v.get("confidence"))

        side_emoji = {"BUY": "🟢", "SELL": "🔴", "WAIT": "⚪"}

        def _row(label: str, side: str, conf: Any, suffix: str = "") -> str:
            side = str(side).upper()
            dot = side_emoji.get(side, "⚪")
            conf_txt = f"{int(float(conf))}%" if conf not in (None, "") else "—"
            # Pad label and side so the percentages line up in monospace clients.
            return f"{dot} {label:<15} {side:<4} {conf_txt:>4}{suffix}"

        lines = ["🧭 <b>AGENT VOTES</b>"]
        for key, label in self.VOTING_AGENTS:
            side, conf = per_agent.get(key, ("WAIT", None))
            lines.append(_row(label, side, conf))

        return "\n".join(lines)

    def _format_why_this_trade(self, decision: Dict[str, Any], ai: Dict[str, Any]) -> str:
        """Merge the multiple overlapping rationale sources into one section.

        Previously the message repeated essentially the same idea up to three
        times (classical reasoning + risk summary + supporting evidence). Here we collect candidate bullet points from the
        strongest sources, normalise them, drop near-duplicates, and cap the
        list so the section stays short.
        """
        candidates: List[str] = []

        # 1) Primary entry rationale.
        entry_reason = self._clean_ai_field(ai.get("entry_reason"))
        if entry_reason:
            candidates.append(entry_reason)

        # 2) Explicit supportive evidence bullets.
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

        # 5) Fallback to the classical reasoning summary if no details are present.
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
            return "💡 <b>WHY THIS TRADE</b>\n• No detailed rationale available"
        body = "\n".join(f"• {m}" for m in merged)
        return f"💡 <b>WHY THIS TRADE</b>\n{body}"

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
            "ORDER_FILLED": "🎯 Pending Order Filled",
        }
        title = event_titles.get(event_type, "🔄 Trade Update")
        pnl_emoji = "✅" if pnl_points > 0 else "➖" if pnl_points == 0 else "❌"
        old_status = evaluation.get("old_status", trade.get("status", "OPEN"))
        new_status = evaluation.get("new_status", old_status)
        progress = evaluation.get("progress_to_tp1")
        hours_open = evaluation.get("hours_open")
        note = self._trade_event_note(event_type, trade, current_price, evaluation)
        display_stop_loss = (evaluation.get("updates", {}) or {}).get("stop_loss", trade.get("stop_loss"))
        extra_lines = []
        progress_text = self._progress_to_tp1_text(progress)
        if progress_text:
            extra_lines.append(progress_text)
        if hours_open is not None:
            extra_lines.append(f"⏱ <b>Time open:</b> {float(hours_open):.1f}h")
        extra_text = "\n".join(extra_lines)

        text = f"""
{title} - <b>XAU/USD</b>
━━━━━━━━━━━━━━━━━━━━━

🆔 <b>ID:</b> <code>{html.escape(str(trade.get('id')))}</code>
📊 <b>Type:</b> {html.escape(str(trade.get('type')))}
📍 <b>Entry:</b> {format_price(trade.get('entry_price'))}
🛑 <b>Stop Loss:</b> {format_price(display_stop_loss)}
🎯 <b>TP1:</b> {format_price(trade.get('tp1'))}
🎯 <b>TP2:</b> {format_price(trade.get('tp2'))}
💰 <b>Current Price:</b> {format_price(current_price)}
📈 <b>Current PnL:</b> {pnl_points:+.1f} pts {pnl_emoji}
📌 <b>Status:</b> {self._status_text(old_status, new_status)}
{extra_text}

{note}

⚠️ Educational paper-trading update only. Not financial advice.
""".strip()
        return self.send_message(text, urgent=event_type in {"ORDER_FILLED", "MOVE_SL_TO_BE", "TRAILING_SL_UPDATED", "TP1_HIT", "TP2_HIT", "SL_HIT", "BE_HIT", "EXPIRED", "TRAILING_SL_HIT"})

    # Display priority: the most important event leads the combined message.
    _EVENT_PRIORITY = (
        "TP2_HIT", "SL_HIT", "TRAILING_SL_HIT", "BE_HIT", "TP1_HIT",
        # If a jump causes BE + trailing in the same cycle, lead with the final
        # actual stop movement, while TP/SL outcomes still outrank everything.
        "TRAILING_SL_UPDATED", "ORDER_FILLED", "MOVE_SL_TO_BE", "EXPIRED", "MANUAL_CLOSE",
        "EXIT_WARNING", "NEAR_TP1", "LONG_RUNNING",
    )

    def send_trade_events(
        self,
        trade: Dict[str, Any],
        events: List[str],
        current_price: float,
        pnl_points: float,
        evaluation: Dict[str, Any] | None = None,
    ) -> bool:
        """Send ONE combined message for all events fired this cycle on a trade.

        Previously the caller looped and sent a separate Telegram message per
        event, so a trade that triggered e.g. LONG_RUNNING + EXIT_WARNING in the
        same evaluation produced two near-identical messages at the same time.
        This consolidates them: one header (highest-priority event) plus a short
        "notes" list covering every event, with the trade snapshot shown once.
        """
        events = [e for e in (events or []) if e]
        if not events:
            return False
        if len(events) == 1:
            return self.send_trade_event(trade, events[0], current_price, pnl_points, evaluation)

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
            "ORDER_FILLED": "🎯 Pending Order Filled",
        }
        # Order events by priority; the first becomes the title.
        ordered = sorted(
            events,
            key=lambda e: self._EVENT_PRIORITY.index(e) if e in self._EVENT_PRIORITY else len(self._EVENT_PRIORITY),
        )
        title = event_titles.get(ordered[0], "🔄 Trade Update")

        pnl_emoji = "✅" if pnl_points > 0 else "➖" if pnl_points == 0 else "❌"
        old_status = evaluation.get("old_status", trade.get("status", "OPEN"))
        new_status = evaluation.get("new_status", old_status)
        progress = evaluation.get("progress_to_tp1")
        hours_open = evaluation.get("hours_open")
        display_stop_loss = (evaluation.get("updates", {}) or {}).get("stop_loss", trade.get("stop_loss"))

        extra_lines = []
        progress_text = self._progress_to_tp1_text(progress)
        if progress_text:
            extra_lines.append(progress_text)
        if hours_open is not None:
            extra_lines.append(f"⏱ <b>Time open:</b> {float(hours_open):.1f}h")
        extra_text = "\n".join(extra_lines)

        # One note line per event (deduplicated, in priority order).
        note_lines = []
        for ev in ordered:
            note = self._trade_event_note(ev, trade, current_price, evaluation)
            if note and note not in note_lines:
                note_lines.append(f"• {note}")
        notes_text = "\n".join(note_lines)

        text = f"""
{title} - <b>XAU/USD</b>
━━━━━━━━━━━━━━━━━━━━━

🆔 <b>ID:</b> <code>{html.escape(str(trade.get('id')))}</code>
📊 <b>Type:</b> {html.escape(str(trade.get('type')))}
📍 <b>Entry:</b> {format_price(trade.get('entry_price'))}
🛑 <b>Stop Loss:</b> {format_price(display_stop_loss)}
🎯 <b>TP1:</b> {format_price(trade.get('tp1'))}
🎯 <b>TP2:</b> {format_price(trade.get('tp2'))}
💰 <b>Current Price:</b> {format_price(current_price)}
📈 <b>Current PnL:</b> {pnl_points:+.1f} pts {pnl_emoji}
📌 <b>Status:</b> {self._status_text(old_status, new_status)}
{extra_text}

{notes_text}

⚠️ Educational paper-trading update only. Not financial advice.
""".strip()
        urgent = any(e in {"ORDER_FILLED", "MOVE_SL_TO_BE", "TRAILING_SL_UPDATED", "TP1_HIT", "TP2_HIT", "SL_HIT", "BE_HIT", "EXPIRED", "TRAILING_SL_HIT"} for e in ordered)
        return self.send_message(text, urgent=urgent)

    def send_trade_update(self, trade: Dict[str, Any], new_status: str, current_price: float, pnl_points: float) -> bool:
        """Backward-compatible wrapper for status-change updates."""
        return self.send_trade_event(trade, new_status, current_price, pnl_points, {"old_status": trade.get("status", "OPEN"), "new_status": new_status})

    @staticmethod
    def _locked_profit_text(trade: Dict[str, Any], evaluation: Dict[str, Any]) -> str:
        """Return a compact locked-profit text based on the updated stop."""
        try:
            entry = float(trade.get("entry_price"))
            new_sl = float((evaluation.get("updates", {}) or {}).get("stop_loss"))
        except (TypeError, ValueError):
            return ""
        trade_type = str(trade.get("type") or trade.get("side") or "BUY").upper()
        locked_pts = (new_sl - entry) * 10.0 if trade_type == "BUY" else (entry - new_sl) * 10.0
        if locked_pts > 0:
            return f"locking about +{locked_pts:.0f} pts"
        if abs(locked_pts) < 0.5:
            return "protected at breakeven"
        return ""

    def _trade_event_note(self, event_type: str, trade: Dict[str, Any], current_price: float, evaluation: Dict[str, Any]) -> str:
        """Return an English note for a trade-management event."""
        if event_type == "ORDER_FILLED":
            return f"🎯 Pending order filled at {format_price(trade.get('entry_price'))}. Position is now live and being managed."
        if event_type == "NEAR_TP1":
            return f"💡 Price reached about 80% of TP1 distance ({format_price(trade.get('tp1'))}). Monitor trade management."
        if event_type == "TP1_HIT":
            return "✅ TP1 reached. Partial-profit / breakeven protection is applied according to the trade plan."
        if event_type == "MOVE_SL_TO_BE":
            # If BE and trailing happen in the same cycle, the final displayed SL
            # may already be beyond entry. This note should still describe the
            # breakeven trigger itself, so use the entry price here.
            return f"💡 Stop Loss moved automatically to breakeven/entry {format_price(trade.get('entry_price'))} after the +100-point protection trigger."
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
            lock_text = self._locked_profit_text(trade, evaluation)
            lock_suffix = f", {lock_text}" if lock_text else ""
            return (
                f"📈 Trailing stop moved to {format_price(new_sl)}{lock_suffix}. "
                f"Rule: 100-point gap / 30-point step."
            )
        if event_type == "TRAILING_SL_HIT":
            return "🔒 Price pulled back to the trailed stop - the locked-in profit beyond breakeven has been secured."
        return "🔄 New trade update."

    def send_daily_report(self, report_text: str) -> bool:
        """Send daily report text."""
        return self.send_message(report_text, urgent=False)

    def send_error_alert(self, error_message: str) -> bool:
        """Send a compact error alert with GitHub Actions context.

        Values are escaped because Telegram messages use HTML parse mode.
        The extra context makes it immediately clear which workflow/job failed
        without exposing any secret values.
        """
        workflow = os.environ.get("GITHUB_WORKFLOW") or "local/manual"
        job = os.environ.get("GITHUB_JOB") or "local"
        event = os.environ.get("GITHUB_EVENT_NAME") or "local"
        run_id = os.environ.get("GITHUB_RUN_ID") or "local"
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT") or "1"
        ref = os.environ.get("GITHUB_REF_NAME") or os.environ.get("GITHUB_REF", "local")
        repo = os.environ.get("GITHUB_REPOSITORY") or "local"

        context_lines = [
            f"<b>Workflow:</b> {html.escape(str(workflow))}",
            f"<b>Job:</b> {html.escape(str(job))}",
            f"<b>Event:</b> {html.escape(str(event))}",
            f"<b>Run:</b> {html.escape(str(run_id))} (attempt {html.escape(str(attempt))})",
            f"<b>Repo/Ref:</b> {html.escape(str(repo))} / {html.escape(str(ref))}",
        ]
        text = (
            "🚨 <b>Gold AI Signals Error</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(context_lines)
            + "\n\n<code>"
            + html.escape(str(error_message)[:3000])
            + "</code>"
        )
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
