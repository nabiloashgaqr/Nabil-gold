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
        return self._generate_json(prompt, kind="signal")

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
        return self._generate_json(prompt, kind="market")

    def interpret_news_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Interpret news risk in concise bullet points."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_news_payload(payload)
        prompt = (
            "Analyze news and context. Return STRICT JSON: "
            "{ 'risk_level': 'LOW/MEDIUM/HIGH/EXTREME', 'summary_bullets': ['point1', 'point2', 'point3'], 'trading_advice': 'sentence' }"
            f"\n\nDATA:\n{json.dumps(compact)}"
        )
        return self._generate_json(prompt, kind="news")

    def summarize_learning(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent learning summary in points."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_learning_payload(payload)
        prompt = (
            "Summarize trading performance. Return STRICT JSON: "
            "{ 'execution_score': 1-10, 'key_lessons': ['p1', 'p2', 'p3'], 'adjustment': 'sentence' }"
            f"\n\nDATA:\n{json.dumps(compact)}"
        )
        return self._generate_json(prompt, kind="learning")

    def summarize_daily_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent daily summary in points."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_daily_report_payload(payload)
        prompt = (
            "Summarize the day. Return STRICT JSON: "
            "{ 'verdict': 'CLEAN/FRAGILE/NEUTRAL', 'key_points': ['p1', 'p2', 'p3'], 'summary': 'sentence' }"
            f"\n\nDATA:\n{json.dumps(compact)}"
        )
        return self._generate_json(prompt, kind="daily")

    def summarize_weekly_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent weekly summary in points."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_weekly_report_payload(payload)
        prompt = (
            "Summarize the week. Return STRICT JSON: "
            "{ 'edge_efficiency': 'val', 'market_regime': 'val', 'strategic_points': ['p1', 'p2', 'p3'], 'strategic_pivot': 'sentence' }"
            f"\n\nDATA:\n{json.dumps(compact)}"
        )
        return self._generate_json(prompt, kind="weekly")

    def _generate_json(self, prompt: str, kind: str = "generic") -> Dict[str, Any]:
        try:
            url = self.API_URL.format(model=self.model)
            resp = self.session.post(url, params={"key": self.api_key}, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}}, timeout=self.timeout)
            if resp.status_code != 200:
                return self._unavailable(f"HTTP {resp.status_code}", kind=kind)
            text = self._extract_text(resp.json())
            if not text:
                return self._unavailable("Empty API result", kind=kind)
            parsed = json.loads(text)
            parsed["available"] = True
            parsed["kind"] = kind
            parsed["suppressed"] = False
            parsed["quality"] = self._quality_label(parsed, kind)
            if self._is_generic_output(parsed, kind):
                parsed["available"] = False
                parsed["suppressed"] = True
                parsed["suppress_reason"] = "generic_or_insufficient_output"
                logger.info("🧠 Gemini %s review suppressed: generic/insufficient output", kind)
            return parsed
        except Exception as e:
            logger.error("Gemini %s failed: %s", kind, e)
            return self._unavailable(str(e), kind=kind)

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
        stats = p.get("stats") or {}
        return {
            "period": p.get("period"),
            "stats": stats,
            "time_of_week_breakdown": p.get("time_of_week_breakdown") or stats.get("time_of_week"),
            "rr_distribution": p.get("rr_distribution") or stats.get("rr_efficiency"),
            "environment_fit": p.get("environment_fit") or stats.get("regime_fit"),
            "news_proximity": p.get("news_proximity") or stats.get("news_proximity"),
        }

    def _quality_texts(self, payload: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        for key in (
            "summary", "reason", "trading_advice", "adjustment", "strategic_pivot",
            "edge_efficiency", "market_regime", "verdict", "market_bias", "action",
        ):
            value = payload.get(key)
            if value is not None:
                values.append(str(value))
        for key in ("key_points", "summary_bullets", "key_lessons", "strategic_points"):
            items = payload.get(key) or []
            if isinstance(items, list):
                values.extend(str(item) for item in items if item is not None)
        return [" ".join(v.split()).strip() for v in values if str(v).strip()]

    def _is_generic_output(self, payload: Dict[str, Any], kind: str = "generic") -> bool:
        texts = self._quality_texts(payload)
        combined = " ".join(texts).lower()
        if not texts:
            return True
        generic_markers = (
            "insufficient data", "not enough data", "cannot determine", "unable to determine",
            "more data is needed", "no specific", "not available", "n/a", "none provided",
        )
        if any(marker in combined for marker in generic_markers):
            return True
        meaningful = [t for t in texts if len(t) >= 18 and len(set(t.lower().split())) >= 4]
        if kind in {"daily", "weekly", "learning", "news"} and not meaningful:
            return True
        return False

    def _quality_label(self, payload: Dict[str, Any], kind: str = "generic") -> str:
        return "generic" if self._is_generic_output(payload, kind) else "ok"

    def _unavailable(self, reason: str, kind: str = "generic") -> Dict[str, Any]:
        return {"available": False, "summary": reason, "reason": reason, "kind": kind, "suppressed": False}

def get_gemini_review_service(config: Dict[str, Any] | None = None) -> GeminiReviewService:
    return GeminiReviewService(config)
