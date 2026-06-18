"""سكريبت التحليل الرئيسي.

يعمل كل 15 دقيقة عبر GitHub Actions. يجلب بيانات الذهب، يشغل الوكلاء (مع AI)،
يطبق إدارة المخاطر والقرار، ثم يحفظ ويرسل الإشارة إذا كانت مؤهلة.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict

# إضافة المسار الرئيسي للمشروع
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.classical_agent import ClassicalAgent
from agents.decision_agent import DecisionAgent
from agents.multitimeframe_agent import MultiTimeframeAgent
from agents.news_risk_agent import NewsRiskAgent
from agents.price_action_agent import PriceActionAgent
from agents.risk_management_agent import RiskManagementAgent
from agents.smc_agent import SMCAgent
from agents.technical_agent import TechnicalAgent
from agents.trading_session_agent import TradingSessionAgent
from services.database import DatabaseService
from services.market_data import MarketDataService
from services.telegram_bot import TelegramService
from services.ai_service import get_ai_service
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def run_agent(agent_name: str, agent: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    """Run one agent safely so one failure does not stop the workflow."""
    try:
        logger.info("Running agent: %s", agent_name)
        return agent.analyze(data)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent %s failed", agent_name)
        return {"agent": agent_name, "signal": "WAIT", "confidence": 0, "reasoning": f"فشل الوكيل: {exc}"}


async def run_analysis_async() -> None:
    """الدالة الرئيسية للتحليل (async)"""
    
    config = load_config()
    telegram = TelegramService(config)
    
    try:
        # ── فحص ساعات التداول أولاً ──
        session = TradingSessionAgent(config).check()
        logger.info(
            "🔍 الجلسة: %s | الجودة: %s | مسموح: %s",
            session.get("current_session") or "خارج الجلسة",
            session.get("session_quality", "N/A"),
            session.get("trading_allowed"),
        )

        if not session.get("trading_allowed"):
            logger.info(
                "🚫 خارج ساعات التداول (%s) - لا تحليل حالياً. السبب: %s",
                session.get("current_session") or "غير محدد",
                session.get("reason", ""),
            )
            return  # ══ لا تحليل خارج الجلسات ══

        # ── تهيئة الخدمات ──
        market_data = MarketDataService(config)
        database = DatabaseService(config)
        
        # ── تهيئة خدمة AI ──
        ai_service = None
        ai_config = config.get('ai_service', {})
        
        if ai_config.get('enabled', False):
            try:
                ai_service = get_ai_service(config)
                logger.info("🤖 AI Service مفعّل: %s", ai_config.get('provider', 'unknown'))
            except Exception as e:
                logger.warning("⚠️ فشل تهيئة AI: %s", e)
        
        logger.info("جلب بيانات السوق...")
        data = market_data.get_gold_data()
        if not data:
            logger.error("فشل في جلب البيانات")
            return

        # Safety: never send production signals from synthetic/demo prices on GitHub Actions.
        allow_synthetic = bool(config.get("data_source", {}).get("allow_synthetic_in_production", False))
        if os.environ.get("GITHUB_ACTIONS") == "true" and data.get("source") == "synthetic_demo" and not allow_synthetic:
            message = "تم إيقاف التحليل: بيانات السوق وهمية synthetic_demo. أضف TWELVE_DATA_API_KEY قبل تشغيل الإشارات."
            logger.error(message)
            telegram.send_error_alert(message)
            return

        # سياق الحساب/المحفظة
        open_trades = database.get_open_trades()
        today_signals = database.get_today_signals_count()
        consecutive_losses = database.get_consecutive_losses()

        # ── تشغيل وكلاء التحليل ──
        all_results: Dict[str, Any] = {
            "technical": run_agent("technical", TechnicalAgent(config, ai_service), data),
            "classical": run_agent("classical", ClassicalAgent(config, ai_service), data),
            "smc": run_agent("smc", SMCAgent(config, ai_service), data),
            "price_action": run_agent("price_action", PriceActionAgent(config, ai_service), data),
            "multitimeframe": run_agent("multitimeframe", MultiTimeframeAgent(config, ai_service), data),
            "current_price": data["current_price"],
            "spread_points": data.get("spread_points"),
            "portfolio": {
                "open_trades_count": len(open_trades),
                "today_signals_count": today_signals,
                "consecutive_losses": consecutive_losses,
            },
        }

        # ── تشغيل وكلاء إضافية (بدون AI) ──
        all_results["session"] = session
        all_results["news"] = NewsRiskAgent(config).check()
        all_results["risk"] = RiskManagementAgent(config).evaluate(all_results)

        # ── تشغيل وكيل القرار (مع AI) ──
        logger.info("تشغيل وكيل القرار (AI-enabled)...")
        
        decision = await DecisionAgent(config, ai_service).decide_async(all_results)
        
        logger.info(
            "القرار: %s - الثقة: %s%% - %s",
            decision.get("decision"),
            decision.get("confidence"),
            decision.get("summary")
        )

        # ── إرسال الإشارة إذا كانت مؤهلة ──
        if decision.get("decision") in {"BUY", "SELL"}:
            settings = config.get("risk_settings", {})
            max_daily = int(settings.get("max_daily_signals", 8))
            max_open = int(settings.get("max_open_trades", 3))
            today_signals = database.get_today_signals_count()
            open_trades = database.get_open_trades()
            
            if today_signals >= max_daily:
                logger.info("تم الوصول للحد الأقصى من الإشارات اليومية: %s", max_daily)
                return
            if len(open_trades) >= max_open:
                logger.info("تم الوصول للحد الأقصى للصفقات المفتوحة: %s", max_open)
                return

            trade_id = database.save_trade(decision)
            decision["trade_id"] = trade_id
            if decision.get("signal"):
                decision["signal"]["trade_id"] = trade_id
            telegram.send_signal(decision)
            logger.info("تم حفظ/إرسال الإشارة: %s", trade_id)
        else:
            logger.info(
                "لا توجد إشارة مؤهلة حالياً. الأسباب/التحذيرات: %s",
                decision.get("warnings")
            )

        logger.info("✅ اكتمل التحليل بنجاح")
        
    except Exception as exc:  # noqa: BLE001
        logger.exception("خطأ في التحليل")
        telegram.send_error_alert(str(exc))


def main() -> None:
    """نقطة الدخول الرئيسية."""
    import asyncio
    asyncio.run(run_analysis_async())


if __name__ == "__main__":
    main()