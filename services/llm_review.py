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
        """Review a candidate signal and return a short structured summary.

        Expected output keys:
        - verdict: APPROVE / CAUTION / REJECT / UNAVAILABLE
        - confidence_note: short text
        - strengths: list[str]
        - risks: list[str]
        - summary: short paragraph
        """
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_signal_payload(payload)
        prompt = (
            "You are reviewing a trading signal produced by a rule-based multi-agent system. "
            "Do not invent prices or indicators. Use only the provided data. "
            "Return STRICT JSON with keys verdict, confidence_note, strengths, risks, summary. "
            "verdict must be one of APPROVE, CAUTION, REJECT. strengths and risks must be short arrays of strings. "
            "summary must be concise and factual.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def analyze_market_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent market analysis overlay.

        This does NOT explain internal agents. It forms a compact discretionary
        opinion from the supplied structured market context only.
        """
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_market_payload(payload)
        prompt = (
            "You are a professional discretionary market analyst reviewing XAU/USD trade context. "
            "Your job is NOT to explain internal agents or repeat their logic. "
            "Your job is to independently assess the market setup using only the structured data provided. "
            "Do not invent indicators, prices, or events. "
            "Do not mention missing data unless it prevents analysis. "
            "Focus on market structure, directional bias, setup quality, timing risk, and tradeability. "
            "If the setup is weak or conflicting, clearly say WAIT. "
            "If there is elevated event/news risk, reflect it in the action and risks. "
            "Return STRICT JSON only with keys market_bias, action, setup_quality, confidence_note, key_levels, risks, summary. "
            "market_bias must be BULLISH, BEARISH, or NEUTRAL. "
            "action must be BUY, SELL, or WAIT. "
            "setup_quality must be HIGH, MEDIUM, or LOW. "
            "key_levels and risks must be short arrays of strings. "
            "summary must be concise and professional.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def interpret_news_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Interpret scheduled economic/news risk for trading posture."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_news_payload(payload)
        prompt = (
            "You are a macro news and market-impact analyst for XAU/USD. "
            "Your job is to interpret scheduled economic news and explain the likely trading impact on gold. "
            "Do not invent news details beyond the provided data. "
            "Focus on volatility risk, directional uncertainty, safe trading posture, and timing. "
            "Return STRICT JSON only with keys risk_level, impact_bias, trading_posture, minutes_to_avoid, risks, summary. "
            "risk_level must be LOW, MEDIUM, HIGH, or EXTREME. "
            "impact_bias must be BULLISH, BEARISH, MIXED, or NEUTRAL. "
            "trading_posture must be NORMAL, CAUTION, WAIT, or NO_TRADE. "
            "risks must be a short array of strings. "
            "summary must be concise and professional.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def summarize_learning(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Summarize learning / post-trade insights in a short structured form."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_learning_payload(payload)
        prompt = (
            "You are reviewing end-of-day trading performance for a rule-based gold trading system. "
            "Your role is to extract practical lessons, recurring problems, and next-session cautions. "
            "Do not invent statistics or missing data. "
            "Return STRICT JSON only with keys lessons, warnings, strengths, summary. "
            "lessons, warnings, and strengths must be short arrays of strings. "
            "summary must be concise and professional.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def summarize_daily_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a compact daily review for the consolidated report."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_daily_report_payload(payload)
        prompt = (
            "You are reviewing end-of-day performance of a gold trading system. "
            "Summarize what mattered today, what was done well, what should be watched tomorrow, and whether the day was clean or fragile. "
            "Do not invent missing trades or metrics. "
            "Return STRICT JSON only with keys strengths, warnings, tomorrow_focus, summary. "
            "strengths, warnings, and tomorrow_focus must be short arrays of strings. "
            "summary must be concise and professional.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def summarize_weekly_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a weekly review overlay."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_weekly_report_payload(payload)
        prompt = (
            "You are reviewing weekly performance of a gold trading system. "
            "Identify recurring patterns, strong conditions, weak conditions, and risk-control lessons. "
            "Do not invent missing trades or metrics. "
            "Return STRICT JSON only with keys strengths, weaknesses, patterns, recommendations, summary. "
            "All list fields must be short arrays of strings. "
            "summary must be concise and professional.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def _generate_json(self, prompt: str) -> Dict[str, Any]:
        try:
            url = self.API_URL.format(model=self.model)
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
            response.raise_for_status()
            data = response.json()
            text = self._extract_text(data)
            if not text:
                return self._unavailable("Gemini returned empty content")
            parsed = json.loads(text)
            parsed["available"] = True
            parsed["model"] = self.model
            return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini review unavailable: %s", exc)
            return self._unavailable(str(exc))

    def _extract_text(self, data: Dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        for candidate in candidates:
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                text = part.get("text")
                if text:
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
            },
            "daily_bias": {
                "bias": daily_bias.get("bias"),
                "confidence": daily_bias.get("confidence"),
            },
            "session": {
                "name": session.get("current_session"),
                "quality": session.get("session_quality"),
                "allowed": session.get("trading_allowed"),
            },
            "technical_context": {
                "summary": sanitize_prompt_text(technical.get("reasoning") or technical.get("summary") or "", 220),
                "trend": (technical.get("technical", {}) or {}).get("trend"),
                "rsi": (technical.get("technical", {}) or {}).get("rsi"),
                "nearest_support": ((technical.get("technical", {}) or {}).get("key_levels") or {}).get("nearest_support"),
                "nearest_resistance": ((technical.get("technical", {}) or {}).get("key_levels") or {}).get("nearest_resistance"),
            },
            "classical_context": {
                "summary": sanitize_prompt_text(classical.get("reasoning") or classical.get("summary") or "", 220),
                "support_levels": list(classical.get("support_levels") or [])[:3],
                "resistance_levels": list(classical.get("resistance_levels") or [])[:3],
            },
            "risk_summary": sanitize_prompt_text(risk.get("summary") or "", 220),
            "news_context": {
                "market_status": news.get("market_status"),
                "can_trade": news.get("can_trade"),
                "summary": sanitize_prompt_text(news.get("summary") or news.get("reasoning") or "", 220),
            },
        }

    def _compact_news_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "symbol": payload.get("symbol"),
            "current_price": payload.get("current_price"),
            "session": payload.get("session") or {},
            "news": payload.get("news") or {},
            "daily_bias": payload.get("daily_bias") or {},
            "technical_context": payload.get("technical_context") or {},
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
        }

    def _compact_weekly_report_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "period": payload.get("period"),
            "headline": sanitize_prompt_text(payload.get("headline") or "", 220),
            "stats": payload.get("stats") or {},
            "recommendations": [sanitize_prompt_text(x, 180) for x in (payload.get("recommendations") or [])[:10]],
            "report_excerpt": sanitize_prompt_text(payload.get("report_excerpt") or "", 800),
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
