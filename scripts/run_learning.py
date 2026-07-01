"""
🧠 Daily Learning Script - Gold AI Signals
سكريبت التعلم الذكي اليومي
يشغل كل يوم لتحليل أداء الوكلاء وتحديث الأوزان
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import DatabaseService
from services.learning_service import get_learning_service
from services.llm_review import get_gemini_review_service
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def _quiet_mode() -> bool:
    """When running inside the consolidated end-of-day digest, individual scripts
    should NOT send their own Telegram message; the daily report aggregates them.
    """
    return os.environ.get("EOD_QUIET", "").lower() in {"1", "true", "yes"}


def _write_eod_section(name: str, text: str) -> None:
    """Persist a section so the consolidated daily report can merge it.

    Written into storage/eod_<name>.txt; the daily report reads then deletes it.
    Within a single GitHub Actions job the workspace is shared across steps.
    """
    try:
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "storage"
        root.mkdir(parents=True, exist_ok=True)
        (root / f"eod_{name}.txt").write_text(text or "", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not persist EOD section %s: %s", name, exc)


def main() -> str | None:
    """الدالة الرئيسية للتعلم. Returns the learning summary text (for the digest)."""

    logger.info("🧠 بدء التعلم الذكي اليومي: %s", datetime.now(timezone.utc).isoformat())

    config = load_config()
    db = DatabaseService(config)
    telegram = TelegramService(config)

    try:
        # التحقق من تفعيل التعلم
        learning_config = config.get('learning', {})
        if not learning_config.get('enabled', False):
            logger.info("❌ التعلم الذكي معطل في الإعدادات")
            return None

        # تحميل الأوزان الحالية
        learning_service = get_learning_service(db, config)

        # تحميل الأوزان من قاعدة البيانات
        current_weights = learning_service.current_weights
        logger.info("📊 الأوزان الحالية: %s", current_weights)

        # تحليل وتحديث الأوزان
        import asyncio
        report = asyncio.run(learning_service.analyze_and_update_weights())

        # بناء تقرير التعلم
        summary = learning_service.get_learning_summary()

        # ── Optional Gemini learning review (Phase 1 reviewer only) ──
        try:
            gemini = get_gemini_review_service(config)
            if not gemini.enabled:
                logger.info("🧠 Gemini learning review skipped: API key not configured")
            else:
                review = gemini.summarize_learning({
                    "report_date": report.report_date,
                    "overall_win_rate": report.overall_win_rate,
                    "total_trades_analyzed": report.total_trades_analyzed,
                    "changes_summary": report.changes_summary,
                    "top_performers": report.top_performers,
                    "bottom_performers": report.bottom_performers,
                    "recommendations": report.recommendations,
                    "session_breakdown": getattr(report, "session_breakdown", {}),
                    "rule_violations": getattr(report, "rule_violations", []),
                    "missed_setups": getattr(report, "missed_setups", []),
                    "alpha_leakage_notes": getattr(report, "alpha_leakage_notes", []),
                })
                if review.get("available"):
                    lines = [summary, "", "🧠 Gemini Independent Learning Review"]
                    if review.get("execution_score") is not None:
                        lines.append(f"• Execution Score: {review.get('execution_score')}/10")
                    
                    # Short bullets only
                    key_lessons = review.get("key_lessons") or []
                    for lesson in key_lessons[:3]:
                        lines.append(f"• {lesson}")
                    
                    if review.get("adjustment"):
                        lines.append(f"• Adjustment: {review.get('adjustment')}")
                        
                    summary = "\n".join(lines)
                    logger.info(
                        "✅ Gemini learning review added: score=%s lessons=%d quality=%s",
                        review.get("execution_score"), len(key_lessons), review.get("quality", "ok")
                    )
                elif review.get("suppressed"):
                    logger.info("🧠 Gemini learning review suppressed: %s", review.get("suppress_reason", "generic"))
                else:
                    logger.warning("🧠 Gemini learning review unavailable: %s", review.get("summary") or review.get("reason"))
        except Exception as gemini_exc:
            logger.exception("🧠 Gemini learning review failed")

        # إرسال تقرير التعلم (إلا في وضع الكتم الخاص بنهاية اليوم)
        if _quiet_mode():
            logger.info("🔇 EOD_QUIET: لن تُرسل رسالة تعلّم منفصلة (ستُدمج في التقرير اليومي)")
            _write_eod_section("learning", summary)
        else:
            telegram.send_message(summary)

        # تحديث config بالأوزان الجديدة
        if report.adjusted_weights:
            config['agent_weights'] = report.adjusted_weights
            logger.info("✅ تم تحديث الأوزان: %s", report.adjusted_weights)

        logger.info("✅ اكتمل التعلم الذكي بنجاح")
        logger.info("📊 الصفقات: %d | Win Rate: %.1f%%",
                   report.total_trades_analyzed, report.overall_win_rate)
        logger.info("📝 التغييرات: %s", report.changes_summary)
        return summary

    except Exception as e:
        logger.error("❌ خطأ في التعلم: %s", e)
        if not _quiet_mode():
            telegram.send_error_alert(f"Smart learning error: {e}")
        return None


if __name__ == "__main__":
    main()