"""Gemini-based review helpers for signals and learning.

Phase 1 design goals:
- Optional and fail-safe: if Gemini is not configured or quota is hit, core bot logic continues.
- Reviewer only: does NOT replace DecisionAgent or LearningService.
- Keep prompts compact and sanitized so free-tier request budgets remain practical.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

import requests

from utils.helpers import sanitize_prompt_text

logger = logging.getLogger(__name__)


class GeminiReviewService:
    """Small wrapper around Gemini Flash for review/explanation tasks."""

    API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.api_key = os.environ.get("GEMINI_API_KEY") or ""
        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self.enabled = bool(self.api_key)
        self.timeout = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "20") or 20)
        self.session = requests.Session()

    def review_signal(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent expert review of a trade setup."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_signal_payload(payload)
        prompt = (
            "You are an Independent Senior Hedge Fund Trader. "
            "Evaluate the provided market data and give your OWN independent opinion (BUY, SELL, or WAIT). "
            "CRITICAL: Do NOT mention, review, or explain the internal agents or indicators. "
            "Provide your verdict and ONE concise sentence explaining your primary reasoning. "
            "Return STRICT JSON with keys verdict, reason, and confidence_note. "
            "verdict must be one of BUY, SELL, WAIT. reason must be ONE short sentence.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def analyze_market_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Form an independent discretionary opinion from market context."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_market_payload(payload)
        prompt = (
            "You are a Professional Institutional Analyst. Analyze the context independently. "
            "DO NOT mention internal system logic or agents. "
            "Return STRICT JSON with keys market_bias (BULLISH/BEARISH/NEUTRAL), action (BUY/SELL/WAIT), reason (ONE short sentence). "
            "Keep it short, tactical, and direct.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def interpret_news_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Interpret news risk in concise bullet points."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_news_payload(payload)
        prompt = (
            "Analyze the news events and market context. "
            "Return STRICT JSON with keys risk_level (LOW/MEDIUM/HIGH/EXTREME), posture (points), summary_bullets (list of 3 short points), trading_advice (one sentence). "
            "summary_bullets must be concise and comprehensive.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def summarize_learning(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent learning summary in points."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_learning_payload(payload)
        prompt = (
            "Summarize the trading performance and learning points. "
            "Return STRICT JSON with keys execution_score (1-10), key_lessons (list of 3 bullet points), adjustment (one sentence). "
            "Keep it very concise and tactical.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def summarize_daily_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent daily summary in points."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_daily_report_payload(payload)
        prompt = (
            "Summarize the day's performance. "
            "Return STRICT JSON with keys verdict (CLEAN/FRAGILE/NEUTRAL), key_points (list of 3 points), summary (one sentence). "
            "Focus only on the result and logic.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def summarize_weekly_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent weekly summary in points."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_weekly_report_payload(payload)
        prompt = (
            "Summarize the week's trading logic and environment. "
            "Return STRICT JSON with keys edge_efficiency, market_regime, strategic_points (list of 3 points), strategic_pivot (one sentence). "
            "Provide independent tactical advice.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def _generate_json(self, prompt: str) -> Dict[str, Any]:
        try:
            url = self.API_URL.format(model=self.model)
            logger.info("🧠 Requesting Gemini (%s)...", self.model)
            response = self.session.post(
                url,
                params={"key": self.api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=self.timeout,
            )
            
            if response.status_code != 200:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                logger.warning("🧠 Gemini API error: %s", error_msg)
                return self._unavailable(error_msg)

            data = response.json()
            text = self._extract_text(data)
            if not text:
                logger.warning("🧠 Gemini returned empty content")
                return self._unavailable("Empty content from API")
            
            parsed = json.loads(text)
            parsed["available"] = True
            parsed["model"] = self.model
            logger.info("🧠 Gemini response received and parsed successfully")
            return parsed
        except Exception as exc:  # noqa: BLE001
            logger.error("🧠 Gemini generation failed: %s", exc)
            return self._unavailable(str(exc))

    def _extract_text(self, data: Dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        for candidate in candidates:
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                text = part.get("text")
                if not text:
                    continue
                
                # Guardrail: Check for generic "insufficient data" responses
                lower_text = str(text).lower()
                if "insufficient data" in lower_text or "not enough data" in lower_text or "no trades recorded" in lower_text:
                    if len(lower_text) < 150: # If it's short AND generic
                        logger.info("🧠 Gemini returned generic/insufficient data response, suppressing.")
                        return ""
                
                return str(text)
        return ""

    def _compact_signal_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        decision = payload.get("decision", {}) or {}
        signal = decision.get("signal", {}) or {}
        classic = decision.get("classic", {}) or {}
        risk = decision.get("risk", {}) or {}
        daily_bias = decision.get("daily_bias", {}) or {}
        return {
            "symbol": payload.get("symbol") or decision.get("symbol"),
            "decision": decision.get("decision"),
            "confidence": decision.get("confidence"),
            "summary": sanitize_prompt_text(decision.get("summary") or decision.get("reasoning") or "", 500),
            "supportive_evidence": [sanitize_prompt_text(x, 140) for x in (decision.get("supportive_evidence") or [])[:5]],
            "warnings": [sanitize_prompt_text(x, 140) for x in (decision.get("warnings") or [])[:5]],
            "agent_context": classic.get("strongest_directional") or decision.get("agent_context"),
            "counts": {
                "buy": classic.get("buy_count"),
                "sell": classic.get("sell_count"),
                "total_voting_agents": classic.get("total_voting_agents"),
            },
            "signal": {
                "entry": (signal.get("entry", {}) or {}).get("price"),
                "stop_loss": signal.get("stop_loss"),
                "tp1": signal.get("tp1"),
                "tp2": signal.get("tp2"),
                "rr_ratio": signal.get("rr_ratio"),
            },
            "risk_summary": sanitize_prompt_text(risk.get("summary") or signal.get("risk_summary") or "", 300),
            "daily_bias": {
                "bias": daily_bias.get("bias"),
                "confidence": daily_bias.get("confidence"),
            },
        }

    def _compact_market_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        decision = payload.get("decision", {}) or {}
        all_results = payload.get("all_results", {}) or {}
        signal = decision.get("signal", {}) or {}
        news = all_results.get("news", {}) or {}
        daily_bias = all_results.get("daily_bias", {}) or decision.get("daily_bias", {}) or {}
        technical = all_results.get("technical", {}) or {}
        classical = all_results.get("classical", {}) or {}
        risk = all_results.get("risk", {}) or decision.get("risk", {}) or {}
        session = all_results.get("session", {}) or {}
        smc = all_results.get("smc", {}) or {}
        multitimeframe = all_results.get("multitimeframe", {}) or {}
        price_action = all_results.get("price_action", {}) or {}
        return {
            "symbol": payload.get("symbol") or decision.get("symbol"),
            "current_price": payload.get("current_price") or all_results.get("current_price") or decision.get("current_price"),
            "decision_summary": sanitize_prompt_text(decision.get("summary") or decision.get("reasoning") or "", 320),
            "system_decision": decision.get("decision"),
            "system_confidence": decision.get("confidence"),
            "signal": {
                "entry": (signal.get("entry", {}) or {}).get("price"),
                "stop_loss": signal.get("stop_loss"),
                "tp1": signal.get("tp1"),
                "tp2": signal.get("tp2"),
                "rr_ratio": signal.get("rr_ratio"),
            },
            "daily_bias": {
                "bias": daily_bias.get("bias"),
                "confidence": daily_bias.get("confidence"),
            },
            "session": {
                "name": session.get("current_session"),
                "quality": session.get("session_quality"),
                "allowed": session.get("trading_allowed"),
                "reason": sanitize_prompt_text(session.get("reason") or "", 180),
            },
            "technical_context": {
                "summary": sanitize_prompt_text(technical.get("reasoning") or technical.get("summary") or "", 220),
                "trend": (technical.get("technical", {}) or {}).get("trend"),
                "rsi": (technical.get("technical", {}) or {}).get("rsi"),
                "macd": (technical.get("technical", {}) or {}).get("macd"),
                "nearest_support": ((technical.get("technical", {}) or {}).get("key_levels") or {}).get("nearest_support"),
                "nearest_resistance": ((technical.get("technical", {}) or {}).get("key_levels") or {}).get("nearest_resistance"),
            },
            "classical_context": {
                "summary": sanitize_prompt_text(classical.get("reasoning") or classical.get("summary") or "", 220),
                "support_levels": list(classical.get("support_levels") or [])[:3],
                "resistance_levels": list(classical.get("resistance_levels") or [])[:3],
            },
            "smc_context": {
                "summary": sanitize_prompt_text(smc.get("reasoning") or smc.get("summary") or "", 220),
                "liquidity_sweeps": list(smc.get("liquidity_sweeps") or [])[:3],
                "order_blocks": list(smc.get("order_blocks") or [])[:3],
                "fvgs": list(smc.get("fair_value_gaps") or smc.get("fvgs") or [])[:3],
            },
            "multitimeframe_context": {
                "summary": sanitize_prompt_text(multitimeframe.get("reasoning") or multitimeframe.get("summary") or "", 220),
                "trend_alignment": multitimeframe.get("trend_alignment"),
            },
            "price_action_context": {
                "summary": sanitize_prompt_text(price_action.get("reasoning") or price_action.get("summary") or "", 220),
                "patterns": list(price_action.get("patterns") or [])[:4],
            },
            "risk_summary": sanitize_prompt_text(risk.get("summary") or "", 220),
            "news_context": {
                "market_status": news.get("market_status"),
                "can_trade": news.get("can_trade"),
                "summary": sanitize_prompt_text(news.get("summary") or news.get("reasoning") or "", 220),
                "events": list(news.get("events") or [])[:5],
            },
        }

    def _compact_news_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        news = payload.get("news") or {}
        return {
            "symbol": payload.get("symbol"),
            "current_price": payload.get("current_price"),
            "session": payload.get("session") or {},
            "daily_bias": payload.get("daily_bias") or {},
            "technical_context": payload.get("technical_context") or {},
            "news": {
                "market_status": news.get("market_status"),
                "can_trade": news.get("can_trade"),
                "summary": sanitize_prompt_text(news.get("summary") or news.get("reasoning") or "", 240),
                "events": list(news.get("events") or [])[:6],
                "ai_interpretation": news.get("ai_interpretation") or {},
            },
        }

    def _compact_learning_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "report_date": payload.get("report_date"),
            "overall_win_rate": payload.get("overall_win_rate"),
            "total_trades_analyzed": payload.get("total_trades_analyzed"),
            "changes_summary": sanitize_prompt_text(payload.get("changes_summary") or "", 400),
            "top_performers": list(payload.get("top_performers") or [])[:5],
            "bottom_performers": list(payload.get("bottom_performers") or [])[:5],
            "recommendations": [sanitize_prompt_text(x, 160) for x in (payload.get("recommendations") or [])[:8]],
            "session_breakdown": payload.get("session_breakdown") or {},
            "rule_violations": list(payload.get("rule_violations") or [])[:8],
            "missed_setups": list(payload.get("missed_setups") or [])[:8],
            "alpha_leakage_notes": list(payload.get("alpha_leakage_notes") or [])[:8],
        }

    def _compact_daily_report_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "report_date": payload.get("report_date"),
            "stats": payload.get("stats") or {},
            "closed_trades_count": payload.get("closed_trades_count"),
            "open_trades_count": payload.get("open_trades_count"),
            "closed_net_points": payload.get("closed_net_points"),
            "floating_net_points": payload.get("floating_net_points"),
            "learning_excerpt": sanitize_prompt_text(payload.get("learning_excerpt") or "", 500),
            "closed_trades_sample": list(payload.get("closed_trades_sample") or [])[:8],
            "open_trades_sample": list(payload.get("open_trades_sample") or [])[:6],
        }

    def _compact_weekly_report_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "period": payload.get("period"),
            "headline": sanitize_prompt_text(payload.get("headline") or "", 220),
            "stats": payload.get("stats") or {},
            "recommendations": [sanitize_prompt_text(x, 180) for x in (payload.get("recommendations") or [])[:10]],
            "report_excerpt": sanitize_prompt_text(payload.get("report_excerpt") or "", 800),
            "time_of_week_breakdown": payload.get("time_of_week_breakdown") or {},
            "rr_distribution": payload.get("rr_distribution") or {},
            "closed_trades_sample": list(payload.get("closed_trades_sample") or [])[:12],
            "environment_fit": payload.get("environment_fit") or {},
        }

    def _unavailable(self, reason: str) -> Dict[str, Any]:
        return {
            "available": False,
            "verdict": "UNAVAILABLE",
            "confidence_note": "Gemini review unavailable",
            "strengths": [],
            "risks": [],
            "lessons": [],
            "warnings": [],
            "summary": sanitize_prompt_text(reason, 240),
        }


def get_gemini_review_service(config: Dict[str, Any] | None = None) -> GeminiReviewService:
    return GeminiReviewService(config)
