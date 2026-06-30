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
            "You are a Senior Institutional Market Analyst specializing in XAU/USD and Energy Markets (WTI/Brent). "
            "Your task is to evaluate the current trade context using a confluence of Market Structure (HTF), Liquidity Pools, and Intermarket correlations (DXY, US10Y). "
            "Independently verify the Draw on Liquidity and External vs Internal range. "
            "Assess premium vs discount pricing of the current setup. "
            "If XAU/USD and Oil show divergence with their USD-correlates, flag it as a strength or weakness. "
            "Do not invent indicators, prices, correlations, or events beyond the provided data. "
            "Return STRICT JSON only. No conversational filler. Keep it concise, tactical, and desk-ready. "
            "Return JSON with keys market_bias, action, setup_quality, liquidity_targets, confluence_factors, confidence_note, risk_management, summary. "
            "market_bias must be BULLISH, BEARISH, NEUTRAL, or TREND_EXHAUSTED. "
            "action must be BUY, SELL, WAIT, SCALE_IN, or SCALE_OUT. "
            "setup_quality must be HIGH (A+), MEDIUM (B), or LOW (C). "
            "liquidity_targets, confluence_factors, and risk_management must be short arrays of strings with a maximum of 3 items each. "
            "confidence_note must be one professional logic line. "
            "summary must be concise and no more than 20 words.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def interpret_news_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Interpret scheduled economic/news risk for trading posture."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_news_payload(payload)
        prompt = (
            "You are a Macroeconomist and Quantitative News Analyst for Gold and Oil markets. "
            "Your job is to analyze high-impact economic releases such as CPI, NFP, FOMC, and Inventory Data to determine Institutional Volatility Risk. "
            "Differentiate between Directional News and Volatility News. "
            "Evaluate Gold/Oil sensitivity to DXY shifts during this specific event when such context is present in the data. "
            "Provide a Protective Posture based on the magnitude of deviation from consensus when available. "
            "Do not invent event details beyond the provided data. "
            "Output MUST be STRICT JSON. Keep it tactical, short, and execution-ready. "
            "Return JSON with keys risk_level, impact_bias, price_projection, trading_posture, specific_risk_zones, summary. "
            "risk_level must be LOW, MEDIUM, HIGH, or EXTREME. "
            "impact_bias must be HAWKISH, DOVISH, NEUTRAL, or VOLATILE_TWO_SIDED. "
            "price_projection must be SHORT_TERM_SPIKE, SUSTAINED_TREND, or MEAN_REVERSION. "
            "trading_posture must be AGGRESSIVE, CAUTION, WAIT_FOR_CANDLE_CLOSE, or NO_TRADE. "
            "specific_risk_zones must be a short array of strings with a maximum of 3 items. "
            "summary must be tactical and no more than 18 words.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def summarize_learning(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Summarize learning / post-trade insights in a short structured form."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_learning_payload(payload)
        prompt = (
            "You are a Performance Coach for Professional Futures Traders. "
            "Your role is to audit the end-of-day execution for Gold/Oil trades against a strict rule-based framework. "
            "Identify Alpha Leakage such as early exits, revenge trading, or missed setups. "
            "Compare execution against Time of Day, especially London and New York session killzones, when such timing context is present. "
            "Focus on the Execution Gap: did the trader follow the setup or trade the PnL. "
            "Output MUST be STRICT JSON. "
            "Keep every field concise and operational: each array item should be short, and summary must be one tight sentence only. "
            "Return JSON with keys execution_score, psychological_flags, technical_errors, recurring_patterns, next_session_adjustment, summary. "
            "execution_score must be a 1-10 number. "
            "psychological_flags, technical_errors, and recurring_patterns must be short arrays of strings with a maximum of 3 items each. "
            "next_session_adjustment must be one concise actionable sentence. "
            "summary must be professional, concise, and no more than 18 words.\n\n"
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
            "strengths, warnings, and tomorrow_focus must be short arrays of strings with a maximum of 3 items each. "
            "Each array item should be brief and executive-friendly. "
            "summary must be concise, professional, and no more than 22 words.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def summarize_weekly_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a weekly review overlay."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_weekly_report_payload(payload)
        prompt = (
            "You are a Quantitative Strategy Auditor. "
            "Your task is to synthesize weekly trading data for XAU/USD and Oil to identify the Strategy Edge and Risk Clusters. "
            "Identify the Environment Fit: did the system perform better in Trending, Ranging, or Volatile regimes. "
            "Analyze Time-of-Week performance such as Monday reversals vs Friday profit taking when such context exists in the data. "
            "Audit Risk-to-Reward efficiency across all closed positions. "
            "Output MUST be STRICT JSON. "
            "Keep the response boardroom-ready: short bullets, no filler, no repetition. "
            "Return JSON with keys strategy_efficiency, dominant_regime, high_probability_windows, risk_leaks, strategic_pivot, recommendations, summary. "
            "strategy_efficiency must be a percentage-style value or concise numeric string. "
            "dominant_regime must be TRENDING, MEAN_REVERSION, or CHOPPY. "
            "high_probability_windows, risk_leaks, and recommendations must be short arrays of strings with a maximum of 3 items each. "
            "strategic_pivot must be Scale Up, Scale Down, or Hold Strategy Constant. "
            "summary must be a senior executive summary no longer than 24 words.\n\n"
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
