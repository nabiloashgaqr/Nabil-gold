"""Gemini-based review service.
Independent discretionary analyst for trading signals and performance.
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
    """Independent expert analyzer using Gemini Flash."""

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
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_signal_payload(payload)
        prompt = (
            "You are an Independent Senior Hedge Fund Trader. "
            "Evaluate the data and give your OWN independent opinion (BUY, SELL, or WAIT). "
            "DO NOT review internal agents. Provide your verdict and ONE concise sentence reasoning. "
            "Return STRICT JSON: { 'verdict': 'BUY/SELL/WAIT', 'reason': 'sentence' }"
            f"\n\nDATA:\n{json.dumps(compact)}"
        )
        return self._generate_json(prompt)

    def analyze_market_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Form an independent discretionary opinion from market context."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_market_payload(payload)
        prompt = (
            "You are a Professional Institutional Analyst. Analyze context independently. "
            "DO NOT mention internal agents. Return STRICT JSON: "
            "{ 'market_bias': 'BULLISH/BEARISH/NEUTRAL', 'action': 'BUY/SELL/WAIT', 'reason': 'short sentence' }"
            f"\n\nDATA:\n{json.dumps(compact)}"
        )
        return self._generate_json(prompt)

    def interpret_news_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Interpret news risk in concise bullet points."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_news_payload(payload)
        prompt = (
            "Analyze news and context. Return STRICT JSON: "
            "{ 'risk_level': 'LOW/MEDIUM/HIGH/EXTREME', 'summary_bullets': ['point1', 'point2', 'point3'], 'trading_advice': 'sentence' }"
            f"\n\nDATA:\n{json.dumps(compact)}"
        )
        return self._generate_json(prompt)

    def summarize_learning(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent learning summary in points."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_learning_payload(payload)
        prompt = (
            "Summarize trading performance. Return STRICT JSON: "
            "{ 'execution_score': 1-10, 'key_lessons': ['p1', 'p2', 'p3'], 'adjustment': 'sentence' }"
            f"\n\nDATA:\n{json.dumps(compact)}"
        )
        return self._generate_json(prompt)

    def summarize_daily_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent daily summary in points."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_daily_report_payload(payload)
        prompt = (
            "Summarize the day. Return STRICT JSON: "
            "{ 'verdict': 'CLEAN/FRAGILE/NEUTRAL', 'key_points': ['p1', 'p2', 'p3'], 'summary': 'sentence' }"
            f"\n\nDATA:\n{json.dumps(compact)}"
        )
        return self._generate_json(prompt)

    def summarize_weekly_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent weekly summary in points."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_weekly_report_payload(payload)
        prompt = (
            "Summarize the week. Return STRICT JSON: "
            "{ 'edge_efficiency': 'val', 'market_regime': 'val', 'strategic_points': ['p1', 'p2', 'p3'], 'strategic_pivot': 'sentence' }"
            f"\n\nDATA:\n{json.dumps(compact)}"
        )
        return self._generate_json(prompt)

    def _generate_json(self, prompt: str) -> Dict[str, Any]:
        try:
            url = self.API_URL.format(model=self.model)
            resp = self.session.post(url, params={"key": self.api_key}, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}}, timeout=self.timeout)
            if resp.status_code != 200: return self._unavailable(f"HTTP {resp.status_code}")
            text = self._extract_text(resp.json())
            if not text: return self._unavailable("Empty API result")
            parsed = json.loads(text)
            parsed["available"] = True
            return parsed
        except Exception as e:
            logger.error("Gemini failed: %s", e)
            return self._unavailable(str(e))

    def _extract_text(self, data: Dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        for c in candidates:
            for p in (c.get("content") or {}).get("parts") or []:
                if p.get("text"): return str(p.get("text"))
        return ""

    def _compact_signal_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        d = p.get("decision", {})
        s = d.get("signal", {})
        return {"symbol": p.get("symbol"), "decision": d.get("decision"), "conf": d.get("confidence"), "entry": s.get("entry", {}).get("price"), "sl": s.get("stop_loss"), "tp": s.get("tp2")}

    def _compact_market_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        d = p.get("decision", {})
        ar = p.get("all_results", {})
        return {"symbol": p.get("symbol"), "price": p.get("current_price"), "bias": ar.get("daily_bias", {}).get("bias"), "rsi": ar.get("technical", {}).get("technical", {}).get("rsi")}

    def _compact_news_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {"symbol": p.get("symbol"), "news": p.get("news", {})}

    def _compact_learning_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {"date": p.get("report_date"), "wr": p.get("overall_win_rate"), "total": p.get("total_trades_analyzed")}

    def _compact_daily_report_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {"date": p.get("report_date"), "stats": p.get("stats"), "net": p.get("closed_net_points")}

    def _compact_weekly_report_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {"period": p.get("period"), "stats": p.get("stats")}

    def _unavailable(self, reason: str) -> Dict[str, Any]:
        return {"available": False, "summary": reason}

def get_gemini_review_service(config: Dict[str, Any] | None = None) -> GeminiReviewService:
    return GeminiReviewService(config)
