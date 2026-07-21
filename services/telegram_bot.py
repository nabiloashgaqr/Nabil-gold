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
        "ORDER_FILLED": "Pending Order Activated",
        "NEAR_TP1": "Near TP1",
        "NEWS_HOLD": "Pending Order Paused by News",
        "PENDING_CANCELLED": "Pending Order Cancelled",
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
        "MOVE_SL_TO_BE", "TRAILING_SL_UPDATED", "ORDER_FILLED", "NEWS_HOLD", "PENDING_CANCELLED", "EXIT_WARNING",
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
        total_core = 5
        # For Path 2 (dual-agent) entries, two_agent is the authoritative
        # source for support/opposition counts.  The classic consensus
        # "selected" is None in that case (the 3-agent consensus was WAIT), so
        # we must read BOTH counts from two_agent — not fall back to
        # sell_count/buy_count which only give support and leave opposition at 0.
        if isinstance(two_agent, dict) and two_agent:
            support = int(two_agent.get("support_count", 0) or 0)
            opposition = int(two_agent.get("opposition_count", 0) or 0)
        else:
            support = int(selected.get("support_count") or classic.get("buy_count" if signal == "BUY" else "sell_count", 0) or 0)
            opposition = int(selected.get("opposition_count") or 0)
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

    def _execution_leg_label(self, setup: Dict[str, Any] | None, plan: Dict[str, Any] | None, *, direction: str = "") -> str | None:
        setup = setup if isinstance(setup, dict) else {}
        plan = plan if isinstance(plan, dict) else {}
        direct = str(setup.get("execution_leg_label") or "").strip()
        if direct:
            return direct
        manual_plan = (plan.get("manual_plan") or {}) if isinstance(plan, dict) else {}
        role = str(setup.get("pending_plan_role") or setup.get("selection_role") or "").upper()
        direction = str(direction or plan.get("session_bias") or "").upper()
        side_word = "BUY" if direction == "BUY" else "SELL" if direction == "SELL" else "TRADE"
        main_label = str(manual_plan.get("main_area_label") or f"MAIN {side_word} AREA")
        add_label = str(manual_plan.get("add_area_label") or f"ADD {side_word} AREA")
        mapping = {
            "PRIMARY": main_label,
            "STANDBY": add_label,
            "STARTER": f"STARTER inside {main_label}",
            "ADD_ON": f"ADD-ON from {add_label}",
        }
        return mapping.get(role) if role else None

    def _trade_execution_leg_label(self, trade: Dict[str, Any]) -> str | None:
        snap = trade.get("signal_snapshot") or {}
        if isinstance(snap, str):
            try:
                import json as _json
                snap = _json.loads(snap)
            except Exception:
                snap = {}
        if not isinstance(snap, dict):
            snap = {}
        setup = snap.get("setup_context") or {}
        plan = snap.get("session_plan") or {}
        return self._execution_leg_label(setup, plan, direction=str(trade.get("type") or trade.get("side") or ""))

    def _setup_lines(self, decision: Dict[str, Any], signal: Dict[str, Any]) -> List[str]:
        setup = decision.get("setup_context") or {}
        if not isinstance(setup, dict):
            setup = {}
        lines: List[str] = []
        setup_type = setup.get("setup_type")
        setup_state = setup.get("setup_state")
        lead_agent = setup.get("lead_agent")
        quality = setup.get("quality_grade") or decision.get("setup_quality")
        selection_role = setup.get("selection_role")
        leg_label = self._execution_leg_label(setup, decision.get("session_plan") or {}, direction=str(decision.get("decision") or signal.get("type") or ""))
        if setup_type or setup_state or lead_agent:
            compact = []
            if setup_type:
                compact.append(str(setup_type).replace("_", " ").title())
            if leg_label:
                compact.append(f"leg {leg_label}")
            if selection_role:
                compact.append(f"role {selection_role}")
            if setup_state:
                compact.append(f"state {setup_state}")
            if lead_agent:
                compact.append(f"lead {lead_agent}")
            if quality:
                compact.append(f"quality {quality}")
            lines.append(f"• <b>Setup:</b> {html.escape(' · '.join(compact))}")
        if leg_label:
            lines.append(f"• <b>Execution leg:</b> {html.escape(leg_label)}")
        zone = signal.get("entry", {}) or {}
        low = zone.get("low")
        high = zone.get("high")
        if low is not None and high is not None:
            try:
                low_f = float(low)
                high_f = float(high)
                if abs(high_f - low_f) > 0.01:
                    lines.append(f"• <b>Entry zone:</b> {self._money(low_f, str(decision.get('symbol') or 'XAU/USD'))} → {self._money(high_f, str(decision.get('symbol') or 'XAU/USD'))}")
            except (TypeError, ValueError):
                pass
        poi_type = setup.get("poi_type")
        sweep_side = setup.get("sweep_side")
        displacement = setup.get("displacement_score")
        target_liquidity = setup.get("target_liquidity")
        if poi_type or sweep_side or displacement not in {None, ""}:
            extra = []
            if poi_type:
                extra.append(f"POI {str(poi_type).replace('_', ' ').title()}")
            if sweep_side:
                extra.append(f"sweep {sweep_side}")
            if displacement not in {None, ""}:
                extra.append(f"disp {displacement}")
            lines.append(f"• <b>SMC context:</b> {html.escape(' · '.join(str(x) for x in extra))}")
        rp = setup.get("return_probability_score")
        td = setup.get("thesis_dominance_score")
        revisit = setup.get("expected_revisit_window")
        if rp not in {None, ""} or td not in {None, ""} or revisit:
            metric_bits = []
            if rp not in {None, ""}:
                metric_bits.append(f"reach {rp}")
            if td not in {None, ""}:
                metric_bits.append(f"dominance {td}")
            if revisit:
                metric_bits.append(f"revisit {revisit}")
            lines.append(f"• <b>POI selection:</b> {html.escape(' · '.join(str(x) for x in metric_bits))}")
        if target_liquidity not in {None, ""}:
            lines.append(f"• <b>Target liquidity:</b> {self._money(target_liquidity, str(decision.get('symbol') or 'XAU/USD'))}")
        governor = decision.get("pending_governor") or {}
        if isinstance(governor, dict) and str(governor.get("action") or "") in {"REPLACE_PENDING", "CANCEL_PENDING_ALLOW_NEW"}:
            old_id = str(governor.get("old_trade_id") or "")
            short = old_id.split("_")[-1] if "_" in old_id else (old_id[-8:] if len(old_id) >= 8 else old_id)
            old_ctx = governor.get("old_context") or {}
            new_ctx = governor.get("new_context") or {}
            lines.append(
                f"• <b>Pending governance:</b> {html.escape(str(governor.get('action')).replace('_', ' ').title())} "
                f"<code>#{html.escape(short)}</code>"
            )
            lines.append(
                f"• <b>Dominance:</b> old {old_ctx.get('thesis_dominance_score', '--')} → new {new_ctx.get('thesis_dominance_score', '--')}"
            )
        adaptive = decision.get("adaptive_execution") or {}
        if isinstance(adaptive, dict) and adaptive.get("action"):
            lines.append(
                f"• <b>Execution switch:</b> {html.escape(str(adaptive.get('action')).replace('_', ' ').title())}"
            )
            if adaptive.get("reason"):
                lines.append(f"• <b>Execution reason:</b> {self._clean_text(adaptive.get('reason'))}")
        invalidation = (decision.get("ai") or {}).get("invalidation") or setup.get("invalidation") or setup.get("entry_reason")
        if self._should_show_invalidation(invalidation, signal.get("stop_loss")):
            label = "Invalidation" if (decision.get("ai") or {}).get("invalidation") else "Structural trigger"
            lines.append(f"• <b>{label}:</b> {self._clean_text(invalidation)}")
        return lines[:7]

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

    def send_session_plan(self, plan: Dict[str, Any]) -> bool:
        symbol = str(plan.get("symbol") or "XAU/USD")
        bias = str(plan.get("session_bias") or plan.get("authority_direction") or "WAIT").upper()
        scenario = str(plan.get("scenario_type") or "DAY_MAP").replace("_", " ").title()
        status = str(plan.get("plan_status") or ("READY" if plan.get("plan_ready") else "NOT_READY")).upper()
        primary_zone = plan.get("primary_entry_zone") or {}
        standby_zone = plan.get("standby_entry_zone") or {}
        confidence = float(plan.get("planner_confidence") or 0)
        grade = str(plan.get("planner_grade") or "--")
        authority = str(plan.get("authority_state") or "--")
        side_word = "BUY" if bias == "BUY" else "SELL" if bias == "SELL" else "TRADE"
        manual_plan = plan.get("manual_plan") or {}
        delivery_context = plan.get("delivery_context") or {}
        delivery_kind = str(delivery_context.get("message_kind") or "OPENING_PLAN").upper()
        delivery_reason = str(delivery_context.get("delivery_reason") or "").strip()
        headline = str(manual_plan.get("headline") or f"{side_word} DAY MAP")
        bias_label = str(manual_plan.get("bias_label") or f"MAIN {side_word} BIAS")
        main_area_label = str(manual_plan.get("main_area_label") or f"PRIMARY {side_word} AREA")
        add_area_label = str(manual_plan.get("add_area_label") or f"SECONDARY {side_word} AREA")

        def _zone_text(zone: Dict[str, Any]) -> str:
            try:
                low = float(zone.get("low"))
                high = float(zone.get("high"))
                return f"{self._money(low, symbol)} → {self._money(high, symbol)}"
            except Exception:
                return "--"

        def _f(value: Any) -> float | None:
            try:
                return float(value)
            except Exception:
                return None

        def _targets() -> tuple[float | None, float | None]:
            entry = _f(plan.get("primary_entry_price"))
            invalidation = _f(plan.get("invalidation_level"))
            target = _f(plan.get("target_liquidity"))
            if entry is None or invalidation is None or target is None:
                return None, target
            risk = abs(invalidation - entry)
            reward = abs(target - entry)
            if risk <= 0 or reward <= 0:
                return None, target
            tp1_dist = min(max(risk, reward * 0.35), reward * 0.5)
            if bias == "BUY":
                return round(entry + tp1_dist, 2), round(target, 2)
            if bias == "SELL":
                return round(entry - tp1_dist, 2), round(target, 2)
            return None, round(target, 2)

        def _invalidation_text() -> str:
            level = _f(plan.get("invalidation_level"))
            if level is None:
                return "--"
            if bias == "BUY":
                return f"Below {self._money(level, symbol)}"
            if bias == "SELL":
                return f"Above {self._money(level, symbol)}"
            return self._money(level, symbol)

        def _execution_text() -> str:
            mode = str(plan.get("execution_preference") or "").upper()
            if mode == "LADDER_PENDING":
                return "Primary area first, secondary area kept ready as backup."
            if mode == "SINGLE_PENDING":
                return "Single planned entry from the primary area only."
            if mode == "NEAR_MARKET_WATCH":
                return "Price is already near the POI — wait for live rejection / confirmation."
            if mode == "SPLIT_EXECUTION_WATCH":
                return "Extreme zone: starter first if price is already inside, then add deeper if needed."
            return str(plan.get("execution_preference") or "Planner-led execution")

        def _opinion_line(opinion: Dict[str, Any]) -> str:
            direction = str(opinion.get("direction") or "WAIT").upper()
            emoji = "🟢" if direction == "BUY" else "🔴" if direction == "SELL" else "🟡"
            label = str(opinion.get("label") or opinion.get("key") or "Agent")
            conf = opinion.get("confidence")
            summary = str(opinion.get("summary") or "").strip()
            signals = [str(x).strip() for x in (opinion.get("signals") or []) if str(x).strip()]
            line = f"{emoji} <b>{html.escape(label)}</b>: {html.escape(direction)}"
            if conf not in {None, ""}:
                line += f" ({float(conf):.0f}%)"
            note = summary or (signals[0] if signals else "")
            if note:
                line += f" — {self._clean_text(note)}"
            return line

        tp1, tp2 = _targets()
        primary_execution = plan.get("primary_execution") or {}
        target_script = manual_plan.get("target_script") or {}
        if target_script.get("tp1") not in {None, ""}:
            tp1 = target_script.get("tp1")
        if target_script.get("tp2") not in {None, ""}:
            tp2 = target_script.get("tp2")
        if primary_execution.get("stop_loss") not in {None, ""}:
            plan["invalidation_level"] = primary_execution.get("stop_loss")
        authority_reason = str(plan.get("authority_reason") or "").strip()
        expected = str(manual_plan.get("expected_path") or plan.get("expected_path") or "").strip()
        primary_rationale = [str(x) for x in (plan.get("primary_rationale") or []) if str(x).strip()]
        standby_rationale = [str(x) for x in (plan.get("standby_rationale") or []) if str(x).strip()]
        narrative = str(manual_plan.get("narrative") or plan.get("plan_narrative") or "").strip()
        confirmation_items = [str(x) for x in (manual_plan.get("confirmation_items") or []) if str(x).strip()]
        missed_area_plan = str(manual_plan.get("missed_area_plan") or "").strip()
        map_change_plan = str(manual_plan.get("map_change_plan") or "").strip()
        execution_items = [str(x) for x in (manual_plan.get("execution_items") or []) if str(x).strip()]
        risk_note = str(manual_plan.get("risk_note") or "").strip()
        agent_opinions = [op for op in (plan.get("agent_opinions") or []) if isinstance(op, dict)]
        gemini_plan_review = plan.get("gemini_plan_review") or {}
        gemini_macro_review = plan.get("gemini_macro_review") or {}
        gemini_news_review = plan.get("gemini_news_review") or {}

        top_title = "SESSION OPENING PLAN" if delivery_kind == "OPENING_PLAN" else "PLAN UPDATE"
        lines = [
            f"🧭 <b>{html.escape(symbol)} — {html.escape(top_title)}</b>",
            f"<b>{html.escape(headline)}</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"{'🟢' if bias == 'BUY' else '🔴' if bias == 'SELL' else '🟡'} <b>{html.escape(bias_label)}:</b> {html.escape(bias)}",
            f"🏷️ <b>Session:</b> {html.escape(str(plan.get('session_label') or '--'))} · {html.escape(str(plan.get('session_quality') or '--'))}",
            f"🏅 <b>Plan Quality:</b> {html.escape(grade)} {confidence:.1f}%",
            f"🧱 <b>Map Strength:</b> {html.escape(authority)}" + (f" · {html.escape(authority_reason)}" if authority_reason else ""),
        ]
        if delivery_reason:
            lines.append(f"📝 <b>Why now:</b> {html.escape(delivery_reason.replace('_', ' '))}")
        lines.extend([
            "──────────────────",
            f"🎯 <b>{html.escape(main_area_label)}</b>",
            f"• {html.escape(_zone_text(primary_zone))}",
            f"• Ref entry: {self._money(plan.get('primary_entry_price'), symbol)}",
        ])
        standby_zone_text = _zone_text(standby_zone)
        standby_entry = plan.get('standby_entry_price')
        if standby_zone_text != "--" or standby_entry not in {None, ""}:
            lines.extend([
                "",
                f"🎯 <b>{html.escape(add_area_label)}</b>",
                f"• {html.escape(standby_zone_text)}",
                f"• Ref entry: {self._money(standby_entry, symbol)}",
            ])
        lines.extend([
            "",
            "🛑 <b>INVALIDATION</b>",
            f"• {html.escape(_invalidation_text())}",
            "",
            "🎯 <b>TARGETS</b>",
        ])
        if tp1 is not None:
            lines.append(f"• TP1 area: {self._money(tp1, symbol)}")
        if tp2 is not None:
            lines.append(f"• TP2 area: {self._money(tp2, symbol)}")
        elif plan.get("target_liquidity") not in {None, ""}:
            lines.append(f"• Target liquidity: {self._money(plan.get('target_liquidity'), symbol)}")
        if plan.get("target_liquidity") not in {None, ""} and tp2 not in {None, ""} and str(self._money(plan.get('target_liquidity'), symbol)) != str(self._money(tp2, symbol)):
            lines.append(f"• Liquidity objective: {self._money(plan.get('target_liquidity'), symbol)}")
        if risk_note:
            lines.append(f"• Risk note: {html.escape(risk_note)}")
        lines.extend([
            "",
            "⚙️ <b>EXECUTION PLAN</b>",
            f"• {html.escape(_execution_text())}",
        ])
        for item in execution_items[:3]:
            lines.append(f"• {html.escape(item)}")
        if str(plan.get("poi_classification") or "").strip():
            lines.append(f"• Zone class: {html.escape(str(plan.get('poi_classification')))}")
        if scenario:
            lines.append(f"• Setup family: {html.escape(scenario)}")
        if expected or narrative or primary_rationale or standby_rationale or confirmation_items or missed_area_plan or map_change_plan:
            lines.extend(["", "🧠 <b>THESIS</b>"])
        if expected:
            lines.append(f"• {html.escape(expected)}")
        if narrative:
            lines.append(f"• {html.escape(narrative)}")
        for item in primary_rationale[:3]:
            lines.append(f"• Primary: {html.escape(item)}")
        for item in standby_rationale[:2]:
            lines.append(f"• Secondary: {html.escape(item)}")
        if confirmation_items:
            lines.append("")
            lines.append("✅ <b>CONFIRMATION</b>")
            for item in confirmation_items[:4]:
                lines.append(f"• {html.escape(item)}")
        if missed_area_plan:
            lines.append("")
            lines.append("📌 <b>IF PRICE MISSES THE MAIN AREA</b>")
            lines.append(f"• {html.escape(missed_area_plan)}")
        if map_change_plan:
            lines.append("")
            lines.append("🔄 <b>IF THE MAP CHANGES</b>")
            lines.append(f"• {html.escape(map_change_plan)}")
        if agent_opinions:
            lines.extend(["", "🗳️ <b>AGENT READS</b>"])
            for opinion in agent_opinions:
                lines.append(_opinion_line(opinion))
        gemini_lines: List[str] = []
        if isinstance(gemini_plan_review, dict) and gemini_plan_review.get("available"):
            verdict = gemini_plan_review.get("market_bias") or gemini_plan_review.get("verdict") or gemini_plan_review.get("opinion") or "REVIEWED"
            reason = gemini_plan_review.get("reason") or gemini_plan_review.get("summary") or ""
            gemini_lines.append(f"🧠 <b>Gemini Context:</b> {self._clean_text(verdict)}" + (f" — {self._clean_text(reason)}" if reason else ""))
        if isinstance(gemini_macro_review, dict) and gemini_macro_review.get("available"):
            verdict = gemini_macro_review.get("macro_verdict") or gemini_macro_review.get("verdict") or "NEUTRAL"
            conf = gemini_macro_review.get("confidence")
            reason = gemini_macro_review.get("reason") or gemini_macro_review.get("summary") or gemini_macro_review.get("primary_driver") or ""
            line = f"🌍 <b>Gemini Macro:</b> {self._clean_text(verdict)}"
            if conf not in {None, ""}:
                line += f" ({html.escape(str(conf))}%)"
            if reason:
                line += f" — {self._clean_text(reason)}"
            gemini_lines.append(line)
        if isinstance(gemini_news_review, dict) and gemini_news_review.get("available"):
            risk = str(gemini_news_review.get("risk_level") or "LOW").upper()
            advice = str(gemini_news_review.get("trading_advice") or "").strip()
            first = ""
            bullets = [str(x).strip() for x in (gemini_news_review.get("summary_bullets") or []) if str(x).strip()]
            if bullets:
                first = bullets[0]
            note = advice or first
            gemini_lines.append(f"📰 <b>Gemini News:</b> {html.escape(risk)}" + (f" — {self._clean_text(note)}" if note else ""))
        if gemini_lines:
            lines.extend(["", "🤖 <b>AI REVIEW</b>"])
            lines.extend(gemini_lines)
        if status != "READY" and str(plan.get("plan_reason") or "").strip():
            lines.extend(["", "⚠️ <b>PLAN STATUS</b>", f"• {html.escape(str(plan.get('plan_reason')))}"])
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append("<i>Session map only — execution still depends on live validation.</i>")
        return self.send_message("\n".join(line for line in lines if str(line).strip() or line == ""), urgent=True)

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
        entry_price = entry.get('price') or decision.get('current_price')
        lines.extend([
            "──────────────────",
            "🎯 <b>TRADE PLAN</b>",
            f"• <b>Order:</b> {html.escape(trade_type)} {html.escape(order_kind)}",
            f"• <b>Entry:</b> {self._money(entry_price, symbol)}",
        ])
        leg_label = self._execution_leg_label(decision.get("setup_context") or {}, decision.get("session_plan") or {}, direction=trade_type)
        if leg_label:
            lines.append(f"• <b>Execution leg:</b> {html.escape(leg_label)}")
        lines.extend([
            f"• <b>Stop Loss:</b> {self._money(signal.get('stop_loss'), symbol)}",
            f"• <b>TP1:</b> {self._money(signal.get('tp1'), symbol)}",
            f"• <b>TP2:</b> {self._money(signal.get('tp2'), symbol)}",
        ])
        if order_kind.endswith("LIMIT") or order_kind.endswith("STOP"):
            try:
                curr = float(entry.get('current_price') or decision.get('current_price') or 0)
                ent = float(entry_price or 0)
                pts = float(entry.get('distance_points') or 0)
            except (TypeError, ValueError):
                curr = 0.0
                ent = 0.0
                pts = 0.0
            activation = "price reaches the entry level"
            if order_kind == "SELL_LIMIT":
                activation = f"price rallies up to {ent:.2f}"
            elif order_kind == "BUY_LIMIT":
                activation = f"price pulls back down to {ent:.2f}"
            elif order_kind == "SELL_STOP":
                activation = f"price breaks down to {ent:.2f}"
            elif order_kind == "BUY_STOP":
                activation = f"price breaks up to {ent:.2f}"
            lines.append("• <b>Status:</b> Pending order — not active yet")
            if curr > 0:
                lines.append(f"• <b>Current price:</b> {curr:.2f} · {pts:.0f} pts to activation")
            lines.append(f"• <b>Activation:</b> This trade activates only when {html.escape(activation)}")
        if rr:
            lines.append(f"• <b>Planned RR:</b> {html.escape(str(rr))}R")
        lines.append("• <b>Protection:</b> SL → entry after +150 pts before TP1")
        lines.append("• <b>Management:</b> Trail gap 150 pts / step 40 pts · check 5m")
        setup_lines = self._setup_lines(decision, signal)
        if setup_lines:
            lines.extend(setup_lines)

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
            lines.append("──────────────────")
            lines.append("📰 <b>GEMINI NEWS CHECK</b>")
            lines.append(f"• <b>Risk:</b> {html.escape(risk_level)}")
            bullets = [str(x) for x in (gemini_news.get("summary_bullets") or []) if str(x).strip()]
            for bullet in bullets[:2]:
                lines.append(f"• {self._clean_text(bullet)}")
            advice = str(gemini_news.get("trading_advice") or "").strip()
            if advice and advice.upper() not in {"N/A", "NONE", "NOT APPLICABLE"}:
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
        if "NEWS_HOLD" in events:
            notes.append("Pending order touched during a blocked news window; activation was paused until post-news recheck.")
        if "PENDING_CANCELLED" in events:
            notes.append("Pending order was cancelled after post-news revalidation failed (invalidated, drift too large, or RR degraded).")
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
            notes.append("Pending order was activated and is now a live trade.")
        if "EXPIRED" in events:
            notes.append("Trade/order expired by time rule.")
        return notes

    @staticmethod
    def _fmt_points(value: Any) -> str:
        try:
            return f"{float(value):+.1f} pts"
        except (TypeError, ValueError):
            return "+0.0 pts"

    @staticmethod
    def _first_reason(*sources: Any) -> str | None:
        for source in sources:
            if isinstance(source, list) and source:
                text = str(source[0]).strip()
                if text:
                    return text
            if isinstance(source, str) and source.strip():
                return source.strip()
        return None

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
        trade_leg_label = self._trade_execution_leg_label(trade)
        if old_status != new_status:
            lines.append(f"• <b>Status:</b> {html.escape(old_status)} → {html.escape(new_status)}")
        else:
            lines.append(f"• <b>Status:</b> {html.escape(new_status)}")
        lines.extend([
            f"• <b>Entry:</b> {self._money(trade.get('entry_price'), symbol)}",
            f"• <b>Current Price:</b> {self._money(current_price, symbol)}",
        ])
        if trade_leg_label:
            lines.append(f"• <b>Plan leg:</b> {html.escape(trade_leg_label)}")
        plan_exec = evaluation.get("plan_execution_context") or {}
        if isinstance(plan_exec, dict) and plan_exec.get("story"):
            lines.append(f"• <b>Execution story:</b> {self._clean_text(plan_exec.get('story'))}")
        if closing:
            close_price = updates.get("close_price") or updates.get("stop_loss") or current_price
            actual = updates.get("final_pnl", pnl_points)
            lines.append(f"• <b>Exit Price:</b> {self._money(close_price, symbol)}")
            lines.append(f"• <b>Actual PnL:</b> {self._fmt_points(actual)}")
        elif "NEWS_HOLD" in events or old_status == "PENDING" or new_status == "PENDING":
            pts_to_fill = evaluation.get("pending_distance_points")
            if pts_to_fill is not None:
                lines.append(f"• <b>Distance to activation:</b> {float(pts_to_fill):.0f} pts")
            hours_open = evaluation.get("hours_open")
            if hours_open is not None:
                lines.append(f"• <b>Waiting:</b> {float(hours_open):.1f}h")
            if "NEWS_HOLD" in events:
                lines.append("• <b>Activation:</b> Paused — touched during news blackout")
            elif "ORDER_FILLED" in events and old_status == "PENDING":
                lines.append("• <b>Activation:</b> Pending order triggered and is now live")
                activation_reason = updates.get("activation_reason")
                if activation_reason:
                    lines.append(f"• <b>Activation review:</b> {self._clean_text(activation_reason)}")
                scenario_gov = evaluation.get("scenario_governor") or {}
                cancelled_siblings = scenario_gov.get("cancelled_ids") or []
                if cancelled_siblings:
                    lines.append(f"• <b>Scenario family:</b> {len(cancelled_siblings)} sibling pending order(s) cancelled")
                    if isinstance(plan_exec, dict) and plan_exec.get("pending_sibling_roles"):
                        roles = " / ".join(str(x) for x in plan_exec.get("pending_sibling_roles") or [])
                        lines.append(f"• <b>Cancelled leg(s):</b> {html.escape(roles)}")
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
        if "PENDING_CANCELLED" in events:
            cancel_reason = self._first_reason(updates.get("reasons"), trade.get("reasons"))
            if cancel_reason:
                lines.append(f"• <b>Cancellation reason:</b> {self._clean_text(cancel_reason)}")
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

    def send_pending_governance(self, governance: Dict[str, Any], *, symbol: str, side: str) -> bool:
        action = str(governance.get("action") or "").upper()
        if action not in {"REPLACE_PENDING", "CANCEL_PENDING_ALLOW_NEW", "KEEP_EXISTING_PENDING"}:
            return False
        reason = self._clean_text(governance.get('reason'))
        if action == "KEEP_EXISTING_PENDING" and "blocked" not in reason.lower():
            return False
        old_id = str(governance.get("old_trade_id") or "")
        short = old_id.split("_")[-1] if "_" in old_id else (old_id[-8:] if len(old_id) >= 8 else old_id or "?")
        old_ctx = governance.get("old_context") or {}
        new_ctx = governance.get("new_context") or {}
        if action == "REPLACE_PENDING":
            title = "♻️ <b>Pending Thesis Replaced</b>"
        elif action == "CANCEL_PENDING_ALLOW_NEW":
            title = "🚫 <b>Pending Thesis Cancelled</b>"
        else:
            title = "🛡️ <b>Pending Replacement Blocked</b>"
        lines = [
            title,
            "━━━━━━━━━━━━━━━━━━━━━",
            f"• <b>Symbol:</b> {html.escape(symbol)}",
            f"• <b>Side:</b> {html.escape(side)}",
        ]
        if old_id:
            lines.append(f"• <b>Previous Pending:</b> <code>#{html.escape(short)}</code>")
        lines.append(f"• <b>Reason:</b> {reason}")
        if old_ctx or new_ctx:
            lines.append(
                f"• <b>Dominance:</b> {old_ctx.get('thesis_dominance_score', '--')} → {new_ctx.get('thesis_dominance_score', '--')}"
            )
            lines.append(
                f"• <b>Reach Probability:</b> {old_ctx.get('return_probability_score', '--')} → {new_ctx.get('return_probability_score', '--')}"
            )
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        return self.send_message("\n".join(lines), urgent=True)

    def send_scenario_governance(self, governance: Dict[str, Any], *, symbol: str, side: str) -> bool:
        action = str(governance.get("action") or "").upper()
        if action not in {"REPLACE_PENDING_FAMILY", "CANCELLED_SIBLINGS_ON_ACTIVATION", "KEEP_EXISTING_FAMILY"}:
            return False
        reason = self._clean_text(governance.get("reason"))
        if action == "KEEP_EXISTING_FAMILY" and not reason:
            return False
        if action == "REPLACE_PENDING_FAMILY":
            title = "🧭 <b>Scenario Family Replaced</b>"
        elif action == "CANCELLED_SIBLINGS_ON_ACTIVATION":
            title = "🪄 <b>Scenario Siblings Cancelled</b>"
        else:
            title = "🛡️ <b>Scenario Family Kept</b>"
        lines = [
            title,
            "━━━━━━━━━━━━━━━━━━━━━",
            f"• <b>Symbol:</b> {html.escape(symbol)}",
            f"• <b>Side:</b> {html.escape(side)}",
        ]
        if governance.get("old_scenario_id") or governance.get("scenario_id"):
            lines.append(
                f"• <b>Scenario:</b> {self._clean_text(governance.get('old_scenario_id') or governance.get('scenario_id'))}"
            )
        if governance.get("new_scenario_id"):
            lines.append(f"• <b>New Scenario:</b> {self._clean_text(governance.get('new_scenario_id'))}")
        if reason:
            lines.append(f"• <b>Reason:</b> {reason}")
        cancelled = governance.get("cancelled_ids") or []
        if cancelled:
            lines.append(f"• <b>Pending Orders Cancelled:</b> {len(cancelled)}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        return self.send_message("\n".join(lines), urgent=True)

    def send_revalidation_block(self, *, symbol: str, side: str, entry_price: Any, reason: str) -> bool:
        lines = [
            "🛑 <b>Re-entry Blocked</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"• <b>Symbol:</b> {html.escape(symbol)}",
            f"• <b>Side:</b> {html.escape(side)}",
            f"• <b>Requested Entry:</b> {self._money(entry_price, symbol)}",
            f"• <b>Reason:</b> {self._clean_text(reason)}",
            "• <b>Guard:</b> Post-exit revalidation requires a materially new thesis before re-entry.",
            "━━━━━━━━━━━━━━━━━━━━━",
        ]
        return self.send_message("\n".join(lines), urgent=True)

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
