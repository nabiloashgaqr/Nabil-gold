"""Groq-powered news interpretation for gold.

The rule-based NewsRiskAgent detects risky events. This service asks Groq to
interpret their likely XAU/USD impact and returns directional restrictions that
DecisionAgent can respect.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from services.ai_service import get_ai_service

logger = logging.getLogger(__name__)


class NewsInterpreter:
    """Interpret upcoming economic/news events using Groq."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.settings = config.get("ai_news_interpretation", {}) or {}
        self.enabled = bool(self.settings.get("enabled", True))
        self.ai_service = get_ai_service(config)

    async def interpret(self, news: Dict[str, Any], market_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "available": False, "summary": "AI news interpretation disabled"}
        events = news.get("upcoming_events", []) or []
        restrictions = news.get("active_restrictions", []) or []
        # Avoid spending API when there is nothing meaningful to interpret.
        if not events and not restrictions and str(news.get("market_status", "SAFE")).upper() == "SAFE":
            return {"enabled": True, "available": False, "skipped": True, "summary": "No relevant news to interpret"}

        prompt = f"""
أنت محلل أخبار اقتصادي متخصص في الذهب XAU/USD.
فسر تأثير الأخبار التالية على الذهب والدولار، وقدم قيود تداول عملية.

حالة الأخبار الحالية:
{news}

سياق السوق المختصر:
{market_context or {}}

أجب JSON فقط بدون Markdown:
{{
  "risk_level": "LOW|MEDIUM|HIGH|EXTREME",
  "gold_bias": "BULLISH|BEARISH|NEUTRAL",
  "usd_bias": "BULLISH|BEARISH|NEUTRAL",
  "block_trading": true أو false,
  "allowed_direction": "BUY|SELL|BOTH|NONE",
  "confidence_adjustment": رقم بين -30 و +10,
  "minutes_to_wait": رقم,
  "reasoning": "تفسير مختصر",
  "key_events": ["اسم أو وصف أهم حدث"],
  "risk_notes": ["ملاحظة 1", "ملاحظة 2"]
}}
""".strip()
        try:
            response = await self.ai_service._call_ai(prompt, "news_risk")
            if not response.success:
                return {"enabled": True, "available": False, "error": response.error, "summary": f"AI news failed: {response.error}"}
            parsed = self.ai_service.parse_json_response(response.content)
            if not parsed:
                return {"enabled": True, "available": False, "error": "Invalid JSON", "summary": "AI news returned invalid JSON"}
            parsed.update({"enabled": True, "available": True, "provider": response.provider, "model": response.model, "tokens_used": response.tokens_used})
            return parsed
        except Exception as exc:  # noqa: BLE001
            logger.exception("AI news interpretation failed")
            return {"enabled": True, "available": False, "error": str(exc), "summary": f"AI news exception: {exc}"}
