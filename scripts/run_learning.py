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
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    """الدالة الرئيسية للتعلم"""
    
    logger.info("🧠 بدء التعلم الذكي اليومي: %s", datetime.now(timezone.utc).isoformat())
    
    config = load_config()
    db = DatabaseService(config)
    telegram = TelegramService(config)
    
    try:
        # التحقق من تفعيل التعلم
        learning_config = config.get('learning', {})
        if not learning_config.get('enabled', False):
            logger.info("❌ التعلم الذكي معطل في الإعدادات")
            return
        
        # تحميل الأوزان الحالية
        learning_service = get_learning_service(db, config)
        
        # تحميل الأوزان من قاعدة البيانات
        current_weights = learning_service.current_weights
        logger.info("📊 الأوزان الحالية: %s", current_weights)
        
        # تحليل وتحديث الأوزان
        report = learning_service.analyze_and_update_weights()
        
        # إرسال تقرير التعلم
        summary = learning_service.get_learning_summary()
        telegram.send_message(summary)
        
        # تحديث config بالأوزان الجديدة
        if report.adjusted_weights:
            config['agent_weights'] = report.adjusted_weights
            logger.info("✅ تم تحديث الأوزان: %s", report.adjusted_weights)
        
        logger.info("✅ اكتمل التعلم الذكي بنجاح")
        logger.info("📊 الصفقات: %d | Win Rate: %.1f%%", 
                   report.total_trades_analyzed, report.overall_win_rate)
        logger.info("📝 التغييرات: %s", report.changes_summary)
        
    except Exception as e:
        logger.error("❌ خطأ في التعلم: %s", e)
        telegram.send_error_alert(f"خطأ في التعلم الذكي: {e}")


if __name__ == "__main__":
    main()