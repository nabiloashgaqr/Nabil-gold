"""AI Memory Rules Engine.

Turns Groq trade-review lessons into persistent rules, then provides active rules
for future decision prompts. Rules are intentionally advisory by default: they
are injected into Groq's decision prompt so the AI can remember recurring
mistakes without creating brittle hard-coded filters too early.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List


DEFAULT_CATEGORY = "AI_REVIEW_LESSON"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable_rule_id(rule_text: str, source_trade_id: str | None = None) -> str:
    raw = f"{source_trade_id or ''}|{rule_text.strip().lower()}".encode("utf-8")
    return "MEM_" + hashlib.sha1(raw).hexdigest()[:12].upper()


def _infer_applies_to(rule_text: str, trade: Dict[str, Any] | None = None) -> str:
    text = rule_text.upper()
    if "BUY" in text and "SELL" not in text:
        return "BUY"
    if "SELL" in text and "BUY" not in text:
        return "SELL"
    if trade:
        side = str(trade.get("type") or trade.get("trade_type") or "").upper()
        if side in {"BUY", "SELL"}:
            return side
    return "BOTH"


def _infer_category(rule_text: str) -> str:
    text = rule_text.lower()
    if any(word in text for word in ["news", "خبر", "أخبار", "fomc", "cpi", "nfp"]):
        return "NEWS_RISK"
    if any(word in text for word in ["sl", "stop", "وقف", "خسارة"]):
        return "STOP_LOSS"
    if any(word in text for word in ["support", "resistance", "دعم", "مقاومة"]):
        return "LEVELS"
    if any(word in text for word in ["confirm", "confirmation", "إغلاق", "تأكيد", "شمعة"]):
        return "ENTRY_CONFIRMATION"
    if any(word in text for word in ["trend", "اتجاه", "4h", "daily"]):
        return "TREND_FILTER"
    return DEFAULT_CATEGORY


def build_memory_rules_from_review(review_item: Dict[str, Any], trade: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    """Create memory rule payloads from a saved AI trade review."""
    review = review_item.get("review", {}) or {}
    source_trade_id = str(review_item.get("trade_id") or (trade or {}).get("id") or "")
    suggestions = review.get("rule_suggestions") or []
    if isinstance(suggestions, str):
        suggestions = [suggestions]

    rules: List[Dict[str, Any]] = []
    for suggestion in suggestions[:5]:
        rule_text = str(suggestion).strip()
        if not rule_text:
            continue
        confidence = review.get("confidence_in_review", 70)
        try:
            confidence_value = max(0, min(100, int(float(confidence))))
        except (TypeError, ValueError):
            confidence_value = 70
        rules.append(
            {
                "id": _stable_rule_id(rule_text, source_trade_id),
                "rule_text": rule_text,
                "category": _infer_category(rule_text),
                "applies_to": _infer_applies_to(rule_text, trade),
                "confidence": confidence_value,
                "source_trade_id": source_trade_id,
                "source": "ai_trade_review",
                "active": True,
                "times_triggered": 0,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "metadata": {
                    "failure_category": review.get("failure_category"),
                    "root_cause": review.get("root_cause"),
                },
            }
        )
    return rules


def sanitize_rule_text(text: str, max_len: int = 240) -> str:
    """Strip prompt-injection risky chars for Groq prompt safety."""
    if not text: return ""
    s = str(text).replace("`","'").replace("{","(").replace("}"," )")
    # remove common prompt injection markers
    for bad in ["SYSTEM:", "Ignore previous", "###", "<|", "PROMPT:", "ASSISTANT:"]:
        s = s.replace(bad, "")
    s = " ".join(s.split())
    return s[:max_len]

def format_memory_rules_for_prompt(rules: List[Dict[str, Any]], max_rules: int = 8) -> str:
    """Format active rules for Groq's decision prompt."""
    active = [r for r in rules if r.get("active", True)]
    active.sort(key=lambda r: (float(r.get("confidence") or 0), str(r.get("updated_at", ""))), reverse=True)
    if not active:
        return "لا توجد قواعد ذاكرة نشطة حالياً."
    lines = []
    for idx, rule in enumerate(active[:max_rules], start=1):
        lines.append(
            f"{idx}. [{rule.get('category', DEFAULT_CATEGORY)} | {rule.get('applies_to', 'BOTH')} | "
            f"ثقة {rule.get('confidence', 0)}%] {sanitize_rule_text(rule.get('rule_text', ''))}"
        )
    return "\n".join(lines)
