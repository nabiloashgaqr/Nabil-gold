"""AI trade review service.

Reviews recently closed losing trades with Groq, extracts lessons, stores the
review, and returns a compact Telegram-ready summary. Reviews are intentionally
limited per run to control API usage.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from services.ai_service import get_ai_service
from services.memory_rules import build_memory_rules_from_review
from utils.helpers import get_trade_side

logger = logging.getLogger(__name__)


CLOSED_LOSS_STATUSES = {"SL_HIT"}
CLOSED_STATUSES = {"TP2_HIT", "SL_HIT", "BE_HIT", "EXPIRED", "MANUAL_CLOSE", "CLOSED", "CANCELLED"}


class TradeReviewService:
    """Use Groq to review losing closed trades and learn lessons."""

    def __init__(self, database, config: Dict[str, Any]) -> None:
        self.db = database
        self.config = config
        self.settings = config.get("ai_trade_review", {}) or {}
        self.enabled = bool(self.settings.get("enabled", True))
        self.max_reviews = int(self.settings.get("max_reviews_per_run", 3) or 3)
        self.recent_limit = int(self.settings.get("recent_trades_limit", 30) or 30)
        self.ai_service = get_ai_service(config)

    def _pnl(self, trade: Dict[str, Any]) -> float:
        for key in ("final_pnl", "current_pnl", "current_pnl_points", "pnl"):
            value = trade.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    def _is_losing_closed_trade(self, trade: Dict[str, Any]) -> bool:
        status = str(trade.get("status", "")).upper()
        if status in {"OPEN", "PARTIAL", "TP1_HIT", "PENDING"}:
            return False
        if status in CLOSED_LOSS_STATUSES:
            return True
        if status in CLOSED_STATUSES and self._pnl(trade) < 0:
            return True
        return False

    def _needs_review(self, trade: Dict[str, Any]) -> bool:
        if trade.get("ai_reviewed") is True:
            return False
        snapshot = trade.get("signal_snapshot") or {}
        if isinstance(snapshot, dict) and snapshot.get("ai_review"):
            return False
        return self._is_losing_closed_trade(trade)

    def get_trades_to_review(self) -> List[Dict[str, Any]]:
        trades = self.db.get_recent_trades(limit=self.recent_limit)
        selected: List[Dict[str, Any]] = []
        for trade in trades:
            if self._needs_review(trade):
                selected.append(trade)
            if len(selected) >= self.max_reviews:
                break
        return selected

    def _build_prompt(self, trade: Dict[str, Any]) -> str:
        # Use unified accessor for side (BUY/SELL) while keeping legacy `type` for compatibility.
        side = get_trade_side(trade)
        safe_trade = {
            "id": trade.get("id"),
            "side": side,
            "type": trade.get("type") or trade.get("trade_type") or side,
            "status": trade.get("status"),
            "entry_price": trade.get("entry_price"),
            "stop_loss": trade.get("stop_loss"),
            "tp1": trade.get("tp1"),
            "tp2": trade.get("tp2"),
            "close_price": trade.get("close_price"),
            "final_pnl": trade.get("final_pnl"),
            "current_pnl": trade.get("current_pnl"),
            "confidence": trade.get("confidence"),
            "created_at": trade.get("created_at"),
            "closed_at": trade.get("closed_at"),
            "reasons": trade.get("reasons", []),
            "signal_snapshot": trade.get("signal_snapshot", {}),
        }
        return f"""
أنت مدقق تداول محترف لنظام Gold AI Signals.
حلل الصفقة الخاسرة التالية بهدف التعلم فقط، وليس لوم المستخدم.

بيانات الصفقة JSON:
{json.dumps(safe_trade, ensure_ascii=False, default=str)[:12000]}

أجب JSON فقط بدون Markdown:
{{
  "failure_category": "ENTRY_EARLY|WRONG_DIRECTION|NEWS_RISK|LOW_QUALITY|SL_TOO_TIGHT|MARKET_REVERSAL|RISK_MANAGEMENT|OTHER",
  "root_cause": "السبب الجذري المختصر",
  "what_went_wrong": ["نقطة 1", "نقطة 2"],
  "what_worked": ["نقطة إيجابية إن وجدت"],
  "agent_feedback": {{
    "technical": "ملاحظة قصيرة",
    "smc": "ملاحظة قصيرة",
    "price_action": "ملاحظة قصيرة",
    "multitimeframe": "ملاحظة قصيرة",
    "risk": "ملاحظة قصيرة"
  }},
  "rule_suggestions": ["قاعدة عملية قابلة للتطبيق 1", "قاعدة 2"],
  "avoid_next_time": "ما الذي يجب تجنبه مستقبلاً",
  "confidence_in_review": 0-100
}}
""".strip()

    async def review_trade(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        prompt = self._build_prompt(trade)
        if not hasattr(self.ai_service, "_call_ai"):
            raise RuntimeError("AI service does not support direct review prompts")
        response = await self.ai_service._call_ai(prompt, "decision")
        if not response.success:
            raise RuntimeError(response.error or "AI review failed")
        parsed = self.ai_service.parse_json_response(response.content)
        if not parsed:
            raise RuntimeError("AI review response was not valid JSON")
        review = {
            "trade_id": str(trade.get("id")),
            "reviewed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "provider": response.provider,
            "model": response.model,
            "tokens_used": response.tokens_used,
            "review": parsed,
        }
        memory_rules = build_memory_rules_from_review(review, trade)
        saved_rule_ids: List[str] = []
        if memory_rules:
            saved_rule_ids = self.db.save_memory_rules(memory_rules)
            review["memory_rule_ids"] = saved_rule_ids
        self.db.save_trade_review(review)
        if trade.get("id"):
            self.db.update_trade(
                str(trade.get("id")),
                {"ai_reviewed": True, "ai_review": parsed, "memory_rule_ids": saved_rule_ids},
            )
        return review

    async def review_recent_losses(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "reviewed": [], "skipped_reason": "disabled"}
        trades = self.get_trades_to_review()
        reviewed: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        for trade in trades:
            try:
                reviewed.append(await self.review_trade(trade))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to review trade %s", trade.get("id"))
                errors.append({"trade_id": str(trade.get("id")), "error": str(exc)})
        return {"enabled": True, "reviewed": reviewed, "errors": errors, "candidates": len(trades)}


def format_trade_review_summary(result: Dict[str, Any]) -> str:
    """Format review result for Telegram."""
    if not result.get("enabled", True):
        return "🧠 <b>AI Trade Review</b>\n━━━━━━━━━━━━━━━━━━━━\nالنظام معطل حالياً."
    reviewed = result.get("reviewed", []) or []
    errors = result.get("errors", []) or []
    if not reviewed and not errors:
        return (
            "🧠 <b>AI Trade Review</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "لا توجد صفقات خاسرة جديدة تحتاج مراجعة.\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )

    lines = [
        "🧠 <b>AI Trade Review للخسائر</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"تمت مراجعة: {len(reviewed)} صفقة",
    ]
    for item in reviewed:
        review = item.get("review", {}) or {}
        suggestions = review.get("rule_suggestions") or []
        if isinstance(suggestions, list):
            suggestions_text = "\n".join(f"• {s}" for s in suggestions[:2])
        else:
            suggestions_text = f"• {suggestions}"
        lines.extend(
            [
                "",
                f"🔻 الصفقة: <code>{item.get('trade_id')}</code>",
                f"├ التصنيف: {review.get('failure_category', 'OTHER')}",
                f"├ السبب: {review.get('root_cause', 'غير محدد')}",
                f"└ الثقة بالمراجعة: {review.get('confidence_in_review', 'N/A')}%",
                f"🧠 قواعد ذاكرة جديدة: {len(item.get('memory_rule_ids', []) or [])}",
            ]
        )
        if suggestions_text:
            lines.append("\nاقتراحات:")
            lines.append(suggestions_text)
    if errors:
        lines.append("")
        lines.append(f"⚠️ أخطاء مراجعة: {len(errors)}")
        for error in errors[:2]:
            lines.append(f"• {error.get('trade_id')}: {error.get('error')}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def run_trade_review(database, config: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronous wrapper used by scripts."""
    service = TradeReviewService(database, config)
    return asyncio.run(service.review_recent_losses())
