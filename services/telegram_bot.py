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
        agent_details = decision.get("agent_details") or {}
        if not agent_details:
            return []
        lines = ["🗳️ <b>AGENT VOTES</b>"]
        # Emoji by direction: green=BUY, red=SELL, yellow=WAIT/neutral
        marker = {"BUY": "🟢", "SELL": "🔴"}
        # Render order: core 5 agents first, then any extras
        core_order = ["technical", "classical", "smc", "price_action", "multitimeframe", "macro_fundamental"]
        ordered_keys = [k for k in core_order if k in agent_details]
        for key in agent_details:
            if key not in ordered_keys:
                ordered_keys.append(key)
        for key in ordered_keys:
            detail = agent_details.get(key)
            if not isinstance(detail, dict):
                continue
            label = str(detail.get("label") or key).strip()
            direction = str(detail.get("direction") or "WAIT").upper()
            if direction in {"NEUTRAL", "HOLD", "NO_TRADE", ""}:
                direction = "WAIT"
            confidence = detail.get("confidence")
            emoji = marker.get(direction, "🟡")
            conf_text = f" {confidence}%" if confidence is not None else ""
            lines.append(f"{emoji} <b>{html.escape(label)}</b>{html.escape(conf_text)}")
            signals = detail.get("signals") or []
            for sig in signals[:3]:
                text = self._friendly_signal_text(sig)
                lines.append(f"  • {text}")
        return lines

    def _signal_strength_line(self, decision: Dict[str, Any]) -> str | None:
        classic = decision.get("classic") or {}
        two_agent = classic.get("two_agent")
        signal = str(decision.get("decision") or "").upper()
        selected = ((classic.get("consensus") or {}).get("selected") or {}) if isinstance(classic, dict) else {}
        support = int(selected.get("support_count") or classic.get("buy_count" if signal == "BUY" else "sell_count", 0) or 0)
        opposition = int(selected.get("opposition_count") or 0)
        total_core = 5
        # For Path 2 entries, pull count from two_agent info
        if isinstance(two_agent, dict) and support <= 0:
            support = int(two_agent.get("support_count", 0) or 0)
            opposition = int(two_agent.get("opposition_count", 0) or 0)
        if support <= 0:
            return None
        if support >= 4 and opposition == 0:
            label = "Excellent"
        elif support >= 3:
            label = "Strong"
        elif support >= 2 and opposition == 0:
            label = "Good (dual-agent)"
        elif opposition == 0:
            label = "Good"
        else:
            label = "Cautious"
        opp_text = "no opposition" if opposition == 0 else f"{opposition} opposing"
        return f"💪 Strength: {label} — {support}/{total_core} qualified agents, {opp_text}"

    def _macro_block(self, decision: Dict[str, Any]) -> List[str]:
        """Rich macro block for signal messages — July 2026 macro-aware upgrade."""
        attr = decision.get("entry_attribution") or {}
        market_context = decision.get("market_context") or {}
        news_context = decision.get("news_context") or {}
        macro_agent = news_context.get("macro", {}) if isinstance(news_context, dict) else {}
        # try full agent result too
        if not macro_agent:
            macro_agent = market_context if isinstance(market_context, dict) else {}
        macro = {}
        macro_full = {}
        for candidate in (
            attr.get("macro_direction") if isinstance(attr, dict) else None,
            market_context.get("macro_direction") if isinstance(market_context, dict) else None,
            (macro_agent.get("macro_direction") if isinstance(macro_agent, dict) else None),
        ):
            if isinstance(candidate, dict) and candidate:
                macro = candidate
                break
        # try to get full agent for drivers/breakdown
        for full_candidate in (
            news_context.get("macro") if isinstance(news_context, dict) else None,
            decision.get("macro_agent"),
            market_context,
        ):
            if isinstance(full_candidate, dict) and full_candidate.get("macro_direction"):
                macro_full = full_candidate
                break
        if not macro or (str(macro.get("bias", "NEUTRAL")).upper() == "NEUTRAL" and not macro.get("confidence") and not macro.get("drivers")):
            # Check if data was collected but just flat/neutral
            data_quality = macro.get("data_quality", {}) if isinstance(macro, dict) else {}
            if data_quality.get("inputs", 0) > 0 or (isinstance(macro, dict) and macro.get("drivers")):
                drivers = macro.get("drivers", [])
                driver_text = "; ".join(drivers[:2]) if drivers else "all indicators flat"
                return [f"• Macro: Neutral — {html.escape(driver_text)}"]
            return ["• Macro: Collecting hourly data"]
        bias = str(macro.get("bias") or "NEUTRAL").replace("_", " ").title()
        conf = macro.get("confidence")
        score = macro.get("score")
        drivers = macro.get("drivers") or []
        breakdown = macro.get("confidence_breakdown") or {}
        invalidations = macro.get("invalidations") or []
        lines = [f"• Macro: {html.escape(bias)}" + (f" ({conf}%)" if conf not in {None, ""} else "") + (f" · score {score}" if score not in {None, ""} else "")]
        if drivers:
            lines.append(f"  ↳ {html.escape('; '.join(drivers[:2]))}")
        if breakdown:
            # show top 2 components
            top = sorted(breakdown.items(), key=lambda kv: abs(float(kv[1] or 0)), reverse=True)[:2]
            bd_txt = ", ".join(f"{k}:{v:+.1f}" for k, v in top)
            lines.append(f"  ↳ {html.escape(bd_txt)}")
        if invalidations:
            lines.append(f"  ⚠ Invalidation: {html.escape(str(invalidations[0])[:90])}")
        return lines

    def _macro_line(self, decision: Dict[str, Any]) -> str:
        # backward compat: first line of block
        block = self._macro_block(decision)
        return block[0] if block else "• Macro: Collecting hourly data"

    def _attribution_lines(self, decision: Dict[str, Any]) -> List[str]:
        attr = decision.get("entry_attribution") or {}
        if not isinstance(attr, dict) or not attr:
            return []
        lines: List[str] = []
        primary = attr.get("primary_entry_driver")
        timing = attr.get("timing_state")
        permission = attr.get("entry_permission")
        if primary:
            lines.append(f"• Primary driver: {html.escape(str(primary).replace('_', ' ').title())}")
        compact = []
        if timing:
            compact.append(f"Timing {timing}")
        if permission:
            compact.append(f"Permission {permission}")
        if compact:
            lines.append(f"• {' · '.join(html.escape(str(x)) for x in compact)}")
        return lines[:2]

    def _technical_caution_lines(self, decision: Dict[str, Any]) -> List[str]:
        details = decision.get("agent_details") or {}
        tech = details.get("technical") if isinstance(details, dict) else {}
        signals = (tech or {}).get("signals") if isinstance(tech, dict) else []
        cautions = []
        for sig in signals or []:
            lower = str(sig).lower()
            if any(word in lower for word in ("bearish", "weakening", "divergence", "overbought", "oversold")):
                cautions.append(f"• Technical caution: {self._friendly_signal_text(sig)}")
        return cautions[:1]

    def _independent_review_lines(self, decision: Dict[str, Any]) -> List[str]:
        """Render Gemini state every time — macro-aware July 2026."""
        review = decision.get("gemini_review")
        lines = ["🧠 <b>GEMINI INDEPENDENT REVIEW</b>"]
        if isinstance(review, dict) and review.get("available"):
            verdict = review.get("verdict") or review.get("signal") or review.get("opinion") or "REVIEWED"
            reason = review.get("reason") or review.get("summary") or "Independent check completed."
            lines.append(f"• <b>Opinion:</b> {self._clean_text(verdict)} - {self._clean_text(reason)}")
            confidence = review.get("confidence")
            if confidence not in {None, ""}:
                lines.append(f"• <b>Review confidence:</b> {html.escape(str(confidence))}%")
            macro_align = review.get("macro_alignment")
            if macro_align:
                emoji = {"ALIGNED": "✅", "CONFLICT": "⚠️", "NEUTRAL": "➖"}.get(str(macro_align).upper(), "•")
                lines.append(f"{emoji} <b>Macro alignment:</b> {html.escape(str(macro_align))}")
            risk_level = review.get("risk_level")
            if risk_level:
                lines.append(f"• <b>Risk:</b> {html.escape(str(risk_level))}")
            invalidation = review.get("invalidation")
            if invalidation:
                lines.append(f"• <b>Invalidation:</b> {self._clean_text(invalidation)}")
            # also show macro context used
            macro_ctx = (decision.get("market_context", {}) or {}).get("macro_direction") or (decision.get("entry_attribution", {}) or {}).get("macro_direction")
            if isinstance(macro_ctx, dict) and macro_ctx.get("bias"):
                mbias = str(macro_ctx.get("bias")).replace("_", " ").title()
                mconf = macro_ctx.get("confidence")
                lines.append(f"• <b>Macro input:</b> {html.escape(mbias)}" + (f" ({mconf}%)" if mconf else ""))
            return lines

        if isinstance(review, dict) and review.get("suppressed"):
            lines.append("• <b>Status:</b> Skipped — no useful extra insight")
            return lines

        if isinstance(review, dict):
            summary = str(review.get("summary") or review.get("reason") or "").lower()
            if any(token in summary for token in ("api key", "not configured", "disabled", "credential")):
                lines.append("• <b>Status:</b> Offline this run")
            else:
                lines.append("• <b>Status:</b> Not available this run")
            return lines

        lines.append("• <b>Status:</b> Offline this run")
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

        entry_mode = str(decision.get('entry_mode', '')).lower()
        entry_path_num = int(decision.get('entry_path', 1) or 1)
        confirm_source = str(decision.get('confirm_source', ''))
        confirm_conf = decision.get('confirm_confidence')

        # Distinctive header per entry path
        if entry_path_num == 2:
            if 'macro' in entry_mode:
                path_badge = '⚡ DUAL-AGENT + MACRO'
                confirm_line = f'📊 Macro confirms {trade_type} ({confirm_conf:.0f}% confidence ≥ 55%)' if confirm_conf else None
            else:
                path_badge = '🤖 DUAL-AGENT + GEMINI'
                confirm_line = f'🧠 Gemini confirms {trade_type} ({confirm_conf:.0f}% confidence ≥ 70%)' if confirm_conf else None
        else:
            path_badge = '🏆 3-AGENT CONSENSUS'
            confirm_line = None

        lines: List[str] = [
            f"{emoji} <b>{html.escape(symbol)} — {html.escape(trade_type)}</b>",
            f"<b>{html.escape(path_badge)}</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"📈 Price {self._money(decision.get('current_price'), symbol)} · Confidence {decision.get('confidence')}%",
            f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        if confirm_line:
            lines.append(f"✅ {html.escape(confirm_line)}")
        if quality:
            lines.append(f"🏅 Quality: {html.escape(str(quality.get('grade', '')))} {html.escape(str(quality.get('score', '')))}".rstrip())
        strength_line = self._signal_strength_line(decision)
        if strength_line:
            lines.append(strength_line)
        order_kind = str(signal.get("entry_kind") or signal.get("order_type") or entry.get("kind") or "MARKET").upper()
        rr = signal.get("rr_ratio") or signal.get("tp2_rr") or decision.get("planned_rr")
        lines.extend([
            "──────────────────",
            "🎯 <b>TRADE PLAN</b>",
            f"• <b>Order:</b> {html.escape(trade_type)} {html.escape(order_kind)}",
            f"• <b>Entry:</b> {self._money(entry.get('price') or decision.get('current_price'), symbol)}",
            f"• <b>Stop Loss:</b> {self._money(signal.get('stop_loss'), symbol)}",
            f"• <b>TP1:</b> {self._money(signal.get('tp1'), symbol)}",
            f"• <b>TP2:</b> {self._money(signal.get('tp2'), symbol)}",
        ])
        if rr:
            lines.append(f"• <b>Planned RR:</b> {html.escape(str(rr))}R")
        lines.append("• <b>Protection:</b> SL → entry after +150 pts before TP1")
        lines.append("• <b>Management:</b> Trail gap 150 pts / step 40 pts · check 5m")

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
        if daily_bias:
            bias_conf = daily_bias.get("confidence")
            bias_conf_text = f" ({bias_conf}%)" if bias_conf is not None else ""
            context_lines.append(
                f"• Daily bias: {html.escape(str(daily_bias.get('bias', 'NEUTRAL')))}{html.escape(bias_conf_text)}"
            )
        # Macro block — rich macro-aware July 2026
        context_lines.extend(self._macro_block(decision))
        session_info = decision.get("session_info") or {}
        if session_info.get("current_session"):
            session_quality = session_info.get("session_quality") or session_info.get("quality")
            suffix = f" · {session_quality}" if session_quality else ""
            # current_session is now classified (e.g. "Asia Morning")
            # instead of the raw config name (e.g. "Main Trading Session")
            context_lines.append(f"• Session: {html.escape(str(session_info.get('current_session')))}{html.escape(suffix)}")
        news_context = decision.get("news_context") or {}
        news_rule = news_context.get("rule_based", {}) if isinstance(news_context, dict) else {}
        if news_rule.get("market_status") or news_rule.get("risk_level"):
            no_block = " — no hard block" if news_rule.get("can_trade", True) is not False else " — blocked"
            context_lines.append(
                f"• News: {html.escape(str(news_rule.get('market_status') or 'OK'))}"
                f" / {html.escape(str(news_rule.get('risk_level') or 'LOW'))}{html.escape(no_block)}"
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

        independent_review = self._independent_review_lines(decision)
        if independent_review:
            lines.append("──────────────────")
            lines.extend(independent_review)

        # Macro independent review — July 2026
        gemini_macro = decision.get("gemini_macro_review", {}) or {}
        if gemini_macro.get("available"):
            lines.append("──────────────────")
            lines.append("🌍 <b>GEMINI MACRO REVIEW</b>")
            mv = str(gemini_macro.get("macro_verdict", "NEUTRAL"))
            mconf = gemini_macro.get("confidence", "")
            mdriver = gemini_macro.get("primary_driver", "")
            mreason = gemini_macro.get("reason", "")
            mtbias = gemini_macro.get("trade_bias", "")
            lines.append(f"• <b>Verdict:</b> {html.escape(mv)}" + (f" ({mconf}%)" if mconf not in {None, ""} else ""))
            if mdriver:
                lines.append(f"• <b>Driver:</b> {html.escape(str(mdriver))}")
            if mreason:
                lines.append(f"• {self._clean_text(mreason)}")
            if mtbias:
                lines.append(f"• <b>Trade bias:</b> {html.escape(str(mtbias))}")
            inval = gemini_macro.get("invalidation")
            if inval:
                lines.append(f"• <b>Invalidation:</b> {self._clean_text(inval)}")

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
            notes.append("Trailing stop updated using 150-point gap / 40-point step.")
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
            lines.append("• <b>Trailing rule:</b> 150-point gap / 40-point step")
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

    def send_post_news_analysis(self, analysis: Dict[str, Any], event_name: str, symbol: str) -> bool:
        """Send a post-news analysis alert — NOT an entry signal.

        English-only message. Sent after a major economic event releases its numbers.
        Includes: event details, surprise factor, gold impact, DXY impact, recommendation.
        """
        if not analysis.get("available") or analysis.get("suppressed"):
            return False

        event = html.escape(str(analysis.get("event") or event_name))
        surprise = str(analysis.get("surprise", "N/A")).upper()
        gold_impact = str(analysis.get("gold_impact", "N/A")).upper()
        dxy_impact = str(analysis.get("dxy_impact", "N/A")).upper()
        recommendation = html.escape(str(analysis.get("recommendation") or ""))
        confidence = analysis.get("confidence", 0)
        key_insight = html.escape(str(analysis.get("key_insight") or ""))

        # Emoji mapping
        surprise_emoji = {"BETTER": "📈", "WORSE": "📉", "IN_LINE": "➖"}.get(surprise, "❓")
        gold_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(gold_impact, "⚪")
        dxy_emoji = {"STRENGTHENING": "💪", "WEAKENING": "🔻", "NEUTRAL": "➖"}.get(dxy_impact, "⚪")

        message = "\n".join([
            f"📰 <b>Post-News Analysis — {html.escape(symbol)}</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"🔔 <b>Event:</b> {event}",
            f"{surprise_emoji} <b>Surprise:</b> {surprise}",
            "",
            f"{gold_emoji} <b>Gold Impact:</b> {gold_impact}",
            f"{dxy_emoji} <b>DXY Impact:</b> {dxy_impact}",
            f"📊 <b>Confidence:</b> {confidence}%",
            "",
            f"💡 <b>Recommendation:</b> {recommendation}",
            "",
            f"🔑 <b>Key Insight:</b> {key_insight}",
            "",
            "━━━ ⚠️ NOTICE ━━━",
            "This is an <b>observation</b>, NOT an entry signal.",
            "Wait for full 3-agent consensus for any trade.",
            "━━━━━━━━━━━━━━━━━━━━━",
        ])
        return self.send_message(message)

    @staticmethod
    def _format_reason_item(item: Any) -> str:
        """Format a reason/evidence item for display — handles dicts cleanly.

        Converts raw dict evidence like {'name': 'weighted_bias', 'value': None, 'bias': 'NEUTRAL'}
        into a human-readable string like "Weighted Bias: NEUTRAL".
        """
        if isinstance(item, dict):
            name = str(item.get("name", "")).replace("_", " ").title()
            value = item.get("value")
            bias = str(item.get("bias", "")).upper()
            # Skip items with no useful information
            if not name and bias in {"", "NEUTRAL"} and value is None:
                return ""
            parts: list[str] = []
            if name:
                parts.append(name)
            if value is not None:
                parts.append(str(value))
            if bias and bias != "NEUTRAL":
                parts.append(f"({bias})")
            elif bias == "NEUTRAL" and not value and name:
                parts.append("Neutral")
            result = ": ".join([parts[0], " ".join(parts[1:])]) if len(parts) > 1 else parts[0] if parts else ""
            return result if result else str(item)
        return str(item)

    def send_partial_consensus(self, decision: Dict[str, Any], all_results: Dict[str, Any], config: Dict[str, Any], database: Any = None) -> bool:
        """Send a partial-consensus alert (2 agents agree, not enough for entry).

        Shows the direction clearly (BUY/SELL) and only sent when price is
        meaningfully different from the last alert for the same direction:
        - BUY: only at a lower price (≥100 pts below last BUY alert)
        - SELL: only at a higher price (≥100 pts above last SELL alert)
        """
        symbol = str(decision.get("symbol") or "XAU/USD")
        current_price = float(decision.get("current_price") or all_results.get("current_price", 0))

        classic = decision.get("classic") or {}
        consensus = classic.get("consensus") or {}
        buy_metrics = consensus.get("BUY") or {}
        sell_metrics = consensus.get("SELL") or {}
        best_side = "BUY" if buy_metrics.get("support_count", 0) >= sell_metrics.get("support_count", 0) else "SELL"
        best_metrics = buy_metrics if best_side == "BUY" else sell_metrics
        confidence = best_metrics.get("confidence", 0)
        support_count = int(best_metrics.get("support_count", 0) or 0)

        # ── Guard: must have at least 1 directional vote ──
        if support_count < 1:
            return False

        # ── Price-diff gate: BUY only at lower price, SELL only at higher price ──
        pca = config.get("partial_consensus_alert") or {}
        min_diff = float(pca.get("min_price_diff_points", 100))
        max_age_hours = float(pca.get("max_age_hours", 8))
        reset_on_session = bool(pca.get("reset_on_session_change", True))
        min_agents_agree = int((config.get("signal_requirements") or {}).get("min_agents_agree", 3))
        if not _partial_alert_price_ok(symbol, best_side, current_price, min_diff,
                                       max_age_hours=max_age_hours,
                                       reset_on_session_change=reset_on_session,
                                       database=database):
            return False

        # ── Direction label and emoji ──
        side_emoji = "🟢" if best_side == "BUY" else "🔴"
        side_label = html.escape(best_side)

        # ── Calculate next alert price threshold ──
        from utils.instruments import points_to_price
        try:
            price_diff = points_to_price(min_diff, symbol=symbol)
        except Exception:
            price_diff = min_diff / 10.0
        if best_side == "BUY":
            next_threshold = current_price - price_diff
        else:
            next_threshold = current_price + price_diff

        # ── Agent analysis section ──
        vote_emojis = {"BUY": "🟢", "SELL": "🔴"}
        min_agent_conf = int((config.get("signal_requirements") or {}).get("agent_min_confidence", 70))
        agent_names = ["technical", "classical", "smc", "price_action", "multitimeframe"]
        analysis_lines = []
        for name in agent_names:
            result = all_results.get(name, {}) or {}
            # Unify signal reading: signal first, then direction (same as DecisionAgent._collect_votes)
            agent_signal = str(result.get("signal") or result.get("direction") or "WAIT").upper()
            if agent_signal in {"NEUTRAL", "HOLD", "NO_TRADE", "NONE", ""}:
                agent_signal = "WAIT"
            agent_conf = float(result.get("confidence", 0) or 0)
            emoji = vote_emojis.get(agent_signal, "🟡")
            if agent_conf < min_agent_conf and agent_signal not in vote_emojis:
                emoji = "⚪"
            display = _AGENT_DISPLAY.get(name, name.title())
            analysis_lines.append(f"{emoji} <b>{display}</b> — {html.escape(agent_signal)} {agent_conf:.0f}%")
            reasons = result.get("reasons") or result.get("evidence") or []
            if isinstance(reasons, (list, tuple)):
                for r in reasons[:2]:
                    formatted = self._format_reason_item(r)
                    if formatted:
                        analysis_lines.append(f"  • {html.escape(formatted[:80])}")
            elif reasons:
                formatted = self._format_reason_item(reasons)
                if formatted:
                    analysis_lines.append(f"  • {html.escape(formatted[:80])}")
        analysis_block = "\n".join(analysis_lines)

        # ── Build message ──
        message = (
            f"👀 <b>Partial {side_label} Consensus — {html.escape(symbol)}</b> {side_emoji}\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 Price: {current_price:.2f} | Confidence: {confidence:.0f}%\n"
            f"📊 {support_count}/{min_agents_agree} agents agree (need {min_agents_agree} for entry)\n"
            "\n"
            f"{analysis_block}\n"
            "\n"
            "━━━ ⚠️ NOTICE ━━━\n"
            "This is <b>NOT</b> an entry signal.\n"
            f"Wait for {min_agents_agree}-agent consensus and 72%+ net confidence.\n"
            f"⏱ Alert expires in {max_age_hours:.0f}h or on session change.\n"
            f"📌 Next {side_label} alert only at price {'≤' if best_side == 'BUY' else '≥'} {next_threshold:.2f} ({'−' if best_side == 'BUY' else '+'}{min_diff:.0f} pts).\n"
            "━━━━━━━━━━━━━━━━━━━━━"
        )
        sent = self.send_message(message)
        if sent:
            _partial_alert_record(symbol, best_side, current_price, database=database)
        return sent


# ── Friendly agent names ──
_AGENT_DISPLAY = {
    "technical": "Technical",
    "classical": "Classical",
    "smc": "SMC",
    "price_action": "Price Action",
    "multitimeframe": "Multi-TF",
}


# ── Partial alert price tracker ──
import json as _json
from pathlib import Path as _Path

_PARTIAL_ALERT_FILE = _Path("storage/partial_alert_tracker.json")
_POST_NEWS_TRACKER_FILE = _Path("storage/post_news_tracker.json")


def _partial_alert_tracker_load() -> dict:
    """Load the last-alert prices per symbol+direction."""
    try:
        if _PARTIAL_ALERT_FILE.exists():
            return _json.loads(_PARTIAL_ALERT_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _partial_alert_tracker_save(data: dict) -> None:
    """Persist the tracker."""
    try:
        _PARTIAL_ALERT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PARTIAL_ALERT_FILE.write_text(_json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _partial_alert_price_ok(symbol: str, side: str, price: float, min_diff_points: float,
                            max_age_hours: float = 8.0, reset_on_session_change: bool = True,
                            database: Any = None) -> bool:
    """Check if this alert should be sent based on price difference, age, and session.

    Uses Supabase (via database service) as primary tracker, with local JSON fallback.
    BUY: only send if price is min_diff_points BELOW last BUY alert.
    SELL: only send if price is min_diff_points ABOVE last SELL alert.
    First alert for a direction always passes.

    Auto-reset conditions (tracker entry is cleared, so next alert is free):
    - reset_on_session_change: if current session differs from the recorded session.
    - max_age_hours: if more than max_age_hours have passed since the last alert.
    """
    # Load entry from Supabase (primary) or local file (fallback)
    entry = None
    if database is not None:
        try:
            entry = database.get_partial_alert_tracker(symbol, side)
        except Exception:
            pass
    if entry is None:
        # Fallback to local file
        tracker = _partial_alert_tracker_load()
        key = f"{symbol}_{side}"
        raw = tracker.get(key)
        if isinstance(raw, dict):
            entry = raw

    if entry is None:
        return True  # First alert for this direction

    # ── Age-based reset ──
    from datetime import datetime, timezone
    last_ts = entry.get("timestamp") if isinstance(entry, dict) else None
    should_reset = False
    if last_ts:
        try:
            last_dt = datetime.fromisoformat(str(last_ts).replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if age_hours > max_age_hours:
                should_reset = True
        except Exception:
            pass  # If timestamp parsing fails, continue to price check

    # ── Session-based reset ──
    if reset_on_session_change and not should_reset:
        from utils.sessions import session_label_from_utc
        current_session = session_label_from_utc(datetime.now(timezone.utc))
        recorded_session = entry.get("session") if isinstance(entry, dict) else None
        if recorded_session and current_session != recorded_session:
            should_reset = True

    if should_reset:
        # Reset tracker entry so next alert passes freely
        if database is not None:
            try:
                key = f"{symbol}_{side}"
                # Save empty/None to effectively clear
                database.save_partial_alert_tracker(symbol, side, {
                    "price": None, "timestamp": None, "session": None,
                })
            except Exception:
                pass
        tracker = _partial_alert_tracker_load()
        tracker.pop(f"{symbol}_{side}", None)
        _partial_alert_tracker_save(tracker)
        return True

    # ── Price-diff check (within same session, within age limit) ──
    last_price = entry.get("price") if isinstance(entry, dict) else entry
    if last_price is None:
        return True
    from utils.instruments import price_to_points
    if side == "BUY":
        diff = price_to_points(last_price - price, symbol=symbol)
    else:
        diff = price_to_points(price - last_price, symbol=symbol)
    return diff >= min_diff_points


def _partial_alert_record(symbol: str, side: str, price: float, database: Any = None) -> None:
    """Record that an alert was sent at this price, with timestamp and session.

    Saves to Supabase (via database service) as primary, and local file as backup.
    """
    from datetime import datetime, timezone
    from utils.sessions import session_label_from_utc
    data = {
        "price": price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session": session_label_from_utc(datetime.now(timezone.utc)),
    }
    # Save to Supabase primary
    if database is not None:
        try:
            database.save_partial_alert_tracker(symbol, side, data)
        except Exception:
            pass
    # Always save to local file as backup
    tracker = _partial_alert_tracker_load()
    key = f"{symbol}_{side}"
    tracker[key] = data
    _partial_alert_tracker_save(tracker)


# ── Post-news tracker ──

def _post_news_tracker_load() -> dict:
    """Load the post-news alert tracker."""
    try:
        if _POST_NEWS_TRACKER_FILE.exists():
            return _json.loads(_POST_NEWS_TRACKER_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _post_news_tracker_save(data: dict) -> None:
    """Persist the post-news tracker."""
    try:
        _POST_NEWS_TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        _POST_NEWS_TRACKER_FILE.write_text(_json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass

def post_news_alert_sent(event_key: str, database: Any = None) -> bool:
    """Check if a post-news alert was already sent for this event.

    Checks Supabase (primary) then local file (fallback).
    """
    if database is not None:
        try:
            if database.use_supabase and database.client:
                response = (
                    database.client.table("post_news_tracker")
                    .select("event_key")
                    .eq("event_key", event_key)
                    .limit(1)
                    .execute()
                )
                if list(response.data or []):
                    return True
        except Exception:
            pass
    tracker = _post_news_tracker_load()
    return event_key in tracker

def post_news_alert_record(event_key: str, database: Any = None) -> None:
    """Record that a post-news alert was sent for this event.

    Saves to Supabase (primary) and local file (backup).
    """
    from datetime import datetime, timezone
    # Save to Supabase
    if database is not None:
        try:
            if database.use_supabase and database.client:
                database.client.table("post_news_tracker").upsert({
                    "event_key": event_key,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
        except Exception:
            pass
    # Always save to local file as backup
    tracker = _post_news_tracker_load()
    tracker[event_key] = datetime.now(timezone.utc).isoformat()
    _post_news_tracker_save(tracker)
