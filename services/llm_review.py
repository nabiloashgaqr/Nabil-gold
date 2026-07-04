"""Gemini-based review service.
Independent discretionary analyst for trading signals and performance.
Macro-aware version — July 2026
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
        llm_cfg = self.config.get("llm_review") or {}
        self.max_retries = int(llm_cfg.get("max_retries", 3))
        self.retry_delay = int(llm_cfg.get("retry_delay_seconds", 3))
        self.session = requests.Session()

    def review_signal(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent expert review of a trade setup — macro aware."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_signal_payload(payload)
        prompt = (
            "You are an Independent Senior Hedge Fund Trader specialized in XAU/USD.\n"
            "You receive: technical consensus, macro/fundamental context, daily bias, risk parameters.\n"
            "Evaluate INDEPENDENTLY — give YOUR own verdict (BUY, SELL, or WAIT).\n"
            "You MUST explicitly consider the macro_direction block:\n"
            "- dxy_trend / usd_strength\n"
            "- yields_trend (us10y / real_yields)\n"
            "- fed_tone\n"
            "- risk_sentiment / geopolitical\n"
            "If macro_direction conflicts with technical signal, lower confidence and explain alignment.\n"
            "DO NOT review internal agent names, only market logic.\n"
            "Return STRICT JSON:\n"
            "{\n"
            "  \"verdict\": \"BUY|SELL|WAIT\",\n"
            "  \"confidence\": 0-100,\n"
            "  \"reason\": \"one concise sentence, mention macro driver if relevant\",\n"
            "  \"macro_alignment\": \"ALIGNED|CONFLICT|NEUTRAL\",\n"
            "  \"risk_level\": \"LOW|MEDIUM|HIGH\",\n"
            "  \"invalidation\": \"one short invalidation level/condition\"\n"
            "}\n"
            f"\n\nDATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt, kind="signal")

    def analyze_market_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Form an independent discretionary opinion from market context — macro aware."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_market_payload(payload)
        prompt = (
            "You are a Professional Institutional Analyst for Gold.\n"
            "Analyze technical + macro context independently.\n"
            "Use macro_direction (DXY, yields, Fed, risk_sentiment) as PRIMARY filter, "
            "then confirm with technical regime.\n"
            "DO NOT mention internal agents.\n"
            "Return STRICT JSON:\n"
            "{ \"market_bias\": \"BULLISH/BEARISH/NEUTRAL\", "
            "\"action\": \"BUY/SELL/WAIT\", "
            "\"macro_read\": \"BULLISH_GOLD|BEARISH_GOLD|NEUTRAL\", "
            "\"reason\": \"short sentence including macro driver\" }\n"
            f"\n\nDATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt, kind="market")

    def interpret_news_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Interpret news risk in concise bullet points — macro aware."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_news_payload(payload)
        prompt = (
            "Analyze news + macro context for XAU/USD. "
            "Incorporate macro_direction if present (DXY, yields, Fed).\n"
            "Return STRICT JSON:\n"
            "{ \"risk_level\": \"LOW/MEDIUM/HIGH/EXTREME\", "
            "\"summary_bullets\": [\"point1\", \"point2\", \"point3\"], "
            "\"trading_advice\": \"sentence\", "
            "\"macro_bias\": \"BULLISH_GOLD|BEARISH_GOLD|NEUTRAL\" }\n"
            f"\n\nDATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt, kind="news")

    def interpret_macro_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Standalone macro interpretation — NEW July 2026."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_macro_payload(payload)
        prompt = (
            "You are a Senior Macro Strategist for Gold (XAU/USD).\n"
            "You receive the full MacroFundamentalAgent output: bias, confidence, score, "
            "confidence_breakdown {dxy, yields, fed, inflation_growth, risk, commodity}, "
            "drivers, evidence, invalidations, data_quality.\n"
            "Give an INDEPENDENT macro verdict, do NOT just repeat the input bias.\n"
            "Weigh DXY + real yields heaviest, then Fed tone, then risk sentiment.\n"
            "Return STRICT JSON:\n"
            "{\n"
            "  \"macro_verdict\": \"BULLISH_GOLD|BEARISH_GOLD|NEUTRAL\",\n"
            "  \"confidence\": 0-100,\n"
            "  \"primary_driver\": \"DXY|YIELDS|FED|RISK|GROWTH|INFLATION\",\n"
            "  \"reason\": \"one sentence with key macro driver\",\n"
            "  \"invalidation\": \"what would flip the view\",\n"
            "  \"trade_bias\": \"BUY|SELL|WAIT\",\n"
            "  \"time_horizon\": \"INTRADAY|SWING|POSITION\"\n"
            "}\n"
            f"\n\nDATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt, kind="macro")

    def interpret_post_news(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Post-news analysis: after a major event, analyze its impact on gold.

        Uses actual vs forecast numbers, price reaction, and DXY macro context
        to give a special recommendation (not a trade entry signal).
        """
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_post_news_payload(payload)
        prompt = (
            "You are a Senior Gold Market Analyst. A major economic event just released its numbers. "
            "Analyze the IMPACT on Gold (XAU/USD) specifically. "
            "Consider: actual vs forecast surprise, DXY/dollar strength from macro context, "
            "and the current price reaction. "
            "Give your RECOMMENDATION - this is NOT an entry signal, it is an informed observation. "
            "Return STRICT JSON: "
            "{ 'event': 'event name', 'surprise': 'BETTER/WORSE/IN_LINE', "
            "'gold_impact': 'BULLISH/BEARISH/NEUTRAL', 'dxy_impact': 'STRENGTHENING/WEAKENING/NEUTRAL', "
            "'recommendation': 'one clear sentence about gold outlook', "
            "'confidence': 1-100, 'key_insight': 'one sentence explaining the main takeaway' }"
            f"\n\nDATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt, kind="post_news")

    def summarize_learning(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent learning summary in points."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_learning_payload(payload)
        prompt = (
            "Summarize trading performance. Return STRICT JSON: "
            "{ 'execution_score': 1-10, 'key_lessons': ['p1', 'p2', 'p3'], 'adjustment': 'sentence' }"
            f"\n\nDATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt, kind="learning")

    def summarize_daily_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent daily summary in points."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_daily_report_payload(payload)
        prompt = (
            "Summarize the day. Return STRICT JSON: "
            "{ 'verdict': 'CLEAN/FRAGILE/NEUTRAL', 'key_points': ['p1', 'p2', 'p3'], 'summary': 'sentence' }"
            f"\n\nDATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt, kind="daily")

    def summarize_weekly_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Independent weekly summary in points."""
        if not self.enabled: return self._unavailable("API key missing")
        compact = self._compact_weekly_report_payload(payload)
        prompt = (
            "Summarize the week. Return STRICT JSON: "
            "{ 'edge_efficiency': 'val', 'market_regime': 'val', 'strategic_points': ['p1', 'p2', 'p3'], 'strategic_pivot': 'sentence' }"
            f"\n\nDATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt, kind="weekly")

    def _generate_json(self, prompt: str, kind: str = "generic") -> Dict[str, Any]:
        import time as _time
        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                url = self.API_URL.format(model=self.model)
                resp = self.session.post(url, params={"key": self.api_key}, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}, timeout=self.timeout)
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    if attempt < self.max_retries:
                        delay = self.retry_delay * attempt
                        logger.warning("Gemini %s attempt %d/%d failed: %s — retry in %ds", kind, attempt, self.max_retries, last_error, delay)
                        _time.sleep(delay)
                        continue
                    return self._unavailable(last_error, kind=kind)
                text = self._extract_text(resp.json())
                if not text:
                    last_error = "Empty API result"
                    if attempt < self.max_retries:
                        delay = self.retry_delay * attempt
                        logger.warning("Gemini %s attempt %d/%d: empty result — retry in %ds", kind, attempt, self.max_retries, delay)
                        _time.sleep(delay)
                        continue
                    return self._unavailable(last_error, kind=kind)
                parsed = json.loads(text)
                parsed["available"] = True
                parsed["kind"] = kind
                parsed["suppressed"] = False
                parsed["quality"] = self._quality_label(parsed, kind)
                parsed["attempts"] = attempt
                if self._is_generic_output(parsed, kind):
                    parsed["available"] = False
                    parsed["suppressed"] = True
                    parsed["suppress_reason"] = "generic_or_insufficient_output"
                    logger.info("Gemini %s review suppressed: generic/insufficient output (attempt %d)", kind, attempt)
                return parsed
            except Exception as e:
                last_error = str(e)
                if attempt < self.max_retries:
                    delay = self.retry_delay * attempt
                    logger.warning("Gemini %s attempt %d/%d exception: %s — retry in %ds", kind, attempt, self.max_retries, last_error, delay)
                    _time.sleep(delay)
                else:
                    logger.error("Gemini %s failed after %d attempts: %s", kind, self.max_retries, last_error)
        return self._unavailable(last_error, kind=kind)

    def _extract_text(self, data: Dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        for c in candidates:
            for p in (c.get("content") or {}).get("parts") or []:
                if p.get("text"): return str(p.get("text"))
        return ""

    # ---- compact payload builders (macro-aware) ----

    def _compact_signal_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        d = p.get("decision", {}) or {}
        s = d.get("signal", {}) or {}
        ar = p.get("all_results", {}) or {}
        # Macro extraction — try 3 locations
        macro = (
            (ar.get("news", {}) or {}).get("macro_direction")
            or (ar.get("macro_fundamental", {}) or {}).get("macro_direction")
            or {}
        )
        # Agent votes summary
        votes_summary = {}
        for name in ("technical","classical","smc","price_action","multitimeframe"):
            r = ar.get(name, {}) or {}
            if r:
                votes_summary[name] = {
                    "signal": r.get("signal") or r.get("direction"),
                    "confidence": r.get("confidence", 0)
                }
        return {
            "symbol": p.get("symbol"),
            "decision": d.get("decision"),
            "confidence": d.get("confidence"),
            "entry": s.get("entry", {}).get("price"),
            "sl": s.get("stop_loss"),
            "tp1": s.get("tp1"),
            "tp2": s.get("tp2"),
            "rr": s.get("rr_ratio"),
            # --- macro block ---
            "macro_direction": {
                "bias": macro.get("bias"),
                "confidence": macro.get("confidence"),
                "score": macro.get("score"),
                "drivers": (macro.get("drivers") or [])[:4],
                "confidence_breakdown": macro.get("confidence_breakdown"),
                "invalidations": (macro.get("invalidations") or [])[:2],
            } if macro else None,
            "daily_bias": {
                "bias": (ar.get("daily_bias", {}) or {}).get("bias"),
                "confidence": (ar.get("daily_bias", {}) or {}).get("confidence"),
            },
            "technical_regime": (
                (ar.get("technical", {}) or {}).get("market_regime")
                or ((ar.get("technical", {}) or {}).get("technical") or {}).get("market_regime")
            ),
            "news_status": (ar.get("news", {}) or {}).get("market_status"),
            "agent_votes": votes_summary,
            "quality": d.get("quality"),
            "current_price": p.get("current_price"),
        }

    def _compact_market_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        d = p.get("decision", {}) or {}
        ar = p.get("all_results", {}) or {}
        macro = (
            (ar.get("news", {}) or {}).get("macro_direction")
            or (ar.get("macro_fundamental", {}) or {}).get("macro_direction")
            or {}
        )
        tech = ar.get("technical", {}) or {}
        tech_inner = tech.get("technical", {}) or {}
        return {
            "symbol": p.get("symbol"),
            "price": p.get("current_price"),
            "bias": (ar.get("daily_bias", {}) or {}).get("bias"),
            "rsi": tech_inner.get("rsi") or tech.get("rsi"),
            "macro_direction": {
                "bias": macro.get("bias"),
                "confidence": macro.get("confidence"),
                "score": macro.get("score"),
                "drivers": macro.get("drivers", [])[:3],
                "breakdown": macro.get("confidence_breakdown"),
            } if macro else None,
            "technical_regime": tech_inner.get("market_regime") or tech.get("market_regime"),
            "session": (ar.get("session", {}) or {}).get("current_session"),
        }

    def _compact_news_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        news = p.get("news", {}) or {}
        macro = news.get("macro_direction") or {}
        # also try top-level macro_agent
        if not macro and p.get("macro_agent"):
            macro = (p.get("macro_agent", {}) or {}).get("macro_direction", {}) or {}
        return {
            "symbol": p.get("symbol"),
            "news": {
                "market_status": news.get("market_status"),
                "can_trade": news.get("can_trade"),
                "risk_level": news.get("risk_level"),
                "upcoming_events": (news.get("upcoming_events") or [])[:2],
            },
            "macro_direction": {
                "bias": macro.get("bias"),
                "confidence": macro.get("confidence"),
                "drivers": (macro.get("drivers") or [])[:3],
            } if macro else None,
            "daily_bias": p.get("daily_bias"),
        }

    def _compact_macro_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Full macro agent output for standalone macro review."""
        # Accept either raw macro_direction or full agent result
        md = p.get("macro_direction") or p.get("macro") or p
        # if it's the full agent result, unwrap
        if "macro_direction" in md and isinstance(md.get("macro_direction"), dict):
            md = md["macro_direction"]
        return {
            "bias": md.get("bias"),
            "confidence": md.get("confidence"),
            "score": md.get("score"),
            "drivers": md.get("drivers", []),
            "reason_codes": md.get("reason_codes", [])[:8],
            "evidence": md.get("evidence", [])[:6],
            "confidence_breakdown": md.get("confidence_breakdown", {}),
            "invalidations": md.get("invalidations", []),
            "data_quality": md.get("data_quality", {}),
            "warnings": md.get("warnings", []),
            "summary": md.get("summary"),
        }

    def _compact_post_news_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "symbol": p.get("symbol", "XAU/USD"),
            "event_name": p.get("event_name"),
            "actual": p.get("actual"),
            "forecast": p.get("forecast"),
            "previous": p.get("previous"),
            "impact_tier": p.get("impact_tier"),
            "minutes_since_release": p.get("minutes_since_release"),
            "current_price": p.get("current_price"),
            "price_before_event": p.get("price_before_event"),
            "price_change_since_event": p.get("price_change_since_event"),
            "dxy_macro": p.get("dxy_macro"),
            "session": p.get("session"),
        }

    def _compact_learning_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "date": p.get("report_date"),
            "wr": p.get("overall_win_rate"),
            "total": p.get("total_trades_analyzed"),
            "rr_efficiency": p.get("rr_efficiency"),
            "session_breakdown": p.get("session_breakdown"),
            "day_of_week_breakdown": p.get("day_of_week_breakdown"),
            "news_proximity": p.get("news_proximity"),
            "regime_fit": p.get("regime_fit"),
        }

    def _compact_daily_report_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        stats = p.get("stats") or {}
        return {
            "date": p.get("report_date"),
            "stats": stats,
            "net": p.get("closed_net_points"),
            "rr_efficiency": p.get("rr_efficiency") or stats.get("rr_efficiency"),
            "session_breakdown": p.get("session_breakdown") or stats.get("session_breakdown"),
            "news_proximity": p.get("news_proximity") or stats.get("news_proximity"),
            "regime_fit": p.get("regime_fit") or stats.get("regime_fit"),
        }

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
            "macro_verdict", "primary_driver", "invalidation", "key_insight", "recommendation",
            "macro_alignment",
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
        if kind in {"daily", "weekly", "learning", "news", "macro"} and not meaningful:
            return True
        return False

    def _quality_label(self, payload: Dict[str, Any], kind: str = "generic") -> str:
        return "generic" if self._is_generic_output(payload, kind) else "ok"

    def _unavailable(self, reason: str, kind: str = "generic") -> Dict[str, Any]:
        return {"available": False, "summary": reason, "reason": reason, "kind": kind, "suppressed": False}

def get_gemini_review_service(config: Dict[str, Any] | None = None) -> GeminiReviewService:
    return GeminiReviewService(config)
