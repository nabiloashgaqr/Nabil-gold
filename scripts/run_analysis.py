"""سكريبت التحليل الرئيسي.

يعمل كل 15 دقيقة عبر GitHub Actions. يجلب بيانات الذهب، يشغل الوكلاء (مع AI)،
يطبق إدارة المخاطر والقرار، ثم يحفظ ويرسل الإشارة إذا كانت مؤهلة.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

# إضافة المسار الرئيسي للمشروع
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.classical_agent import ClassicalAgent
from agents.decision_agent import DecisionAgent
from agents.daily_bias_agent import DailyBiasAgent
from agents.multitimeframe_agent import MultiTimeframeAgent
from agents.news_risk_agent import NewsRiskAgent
from agents.price_action_agent import PriceActionAgent
from agents.risk_management_agent import RiskManagementAgent
from agents.smc_agent import SMCAgent
from agents.technical_agent import TechnicalAgent
from agents.trading_session_agent import TradingSessionAgent
from services.database import DatabaseService
from services.dynamic_risk import DynamicRiskManager, should_block_signal
from services.market_data import MarketDataService
from services.news_interpreter import NewsInterpreter
from services.telegram_bot import TelegramService
from services.ai_service import get_ai_service
from services.learning_service import get_learning_service
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def synthetic_timeframe_sources(data: Dict[str, Any]) -> list[str]:
    """Return timeframe/source names that are synthetic demo data."""
    synthetic: list[str] = []
    if data.get("source") == "synthetic_demo":
        synthetic.append(str(data.get("timeframe") or "primary"))
    for timeframe, payload in (data.get("timeframes", {}) or {}).items():
        if isinstance(payload, dict) and payload.get("source") == "synthetic_demo":
            name = str(timeframe)
            if name not in synthetic:
                synthetic.append(name)
    return synthetic


def should_send_status(config: Dict[str, Any]) -> bool:
    """Send status/no-signal Telegram messages only on manual runs or if enabled.

    Scheduled analysis runs every 15 minutes, so no-signal notifications are off by
    default to avoid Telegram spam. Manual workflow_dispatch runs always get a
    status message so you can confirm the bot is alive.
    """
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return True
    return bool(config.get("notifications", {}).get("send_no_signal_updates", False))




def _parse_datetime(value: Any) -> datetime | None:
    """Parse common ISO timestamps safely as UTC-aware datetimes."""
    if not value:
        return None
    try:
        text = str(value).replace('Z', '+00:00')
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _trade_direction(trade: Dict[str, Any]) -> str:
    return str(trade.get('type') or trade.get('trade_type') or trade.get('decision') or '').upper()


def _trade_entry_price(trade: Dict[str, Any]) -> float | None:
    for key in ('entry_price', 'current_price'):
        value = trade.get(key)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def duplicate_signal_reason(decision: Dict[str, Any], database: DatabaseService, config: Dict[str, Any]) -> str | None:
    """Return a reason if this signal is a duplicate of an open/recent similar signal."""
    filt = config.get('duplicate_signal_filter', {}) or {}
    if not filt.get('enabled', True):
        return None

    direction = str(decision.get('decision', '')).upper()
    if direction not in {'BUY', 'SELL'}:
        return None

    signal = decision.get('signal', {}) or {}
    entry = signal.get('entry', {}) or {}
    try:
        entry_price = float(entry.get('price') or decision.get('current_price') or 0)
    except (TypeError, ValueError):
        entry_price = 0.0

    risk = decision.get('risk', {}) or {}
    try:
        atr = float((risk.get('risk_metrics', {}) or {}).get('atr') or 0)
    except (TypeError, ValueError):
        atr = 0.0

    tolerance_points = float(filt.get('price_tolerance_points', 3.0) or 3.0)
    tolerance_atr = float(filt.get('price_tolerance_atr_multiplier', 0.75) or 0.75)
    tolerance = max(tolerance_points, atr * tolerance_atr)
    now = datetime.now(timezone.utc)

    open_trades = database.get_open_trades()
    if filt.get('block_if_open_same_direction', True):
        for trade in open_trades:
            if _trade_direction(trade) == direction:
                return f"توجد صفقة {direction} مفتوحة بالفعل: {trade.get('id', 'unknown')}"

    lookback_minutes = int(filt.get('lookback_minutes', 90) or 90)
    cutoff = now - timedelta(minutes=lookback_minutes)
    for trade in database.get_recent_trades(limit=30):
        if _trade_direction(trade) != direction:
            continue
        created = _parse_datetime(trade.get('created_at') or trade.get('entry_time') or trade.get('opened_at'))
        if created and created < cutoff:
            continue
        previous_entry = _trade_entry_price(trade)
        if previous_entry is None or entry_price <= 0:
            return f"إشارة {direction} مشابهة أرسلت خلال آخر {lookback_minutes} دقيقة: {trade.get('id', 'unknown')}"
        if abs(entry_price - previous_entry) <= tolerance:
            return (
                f"إشارة مكررة {direction}: السعر قريب من إشارة حديثة "
                f"({previous_entry:.2f} vs {entry_price:.2f}, tolerance={tolerance:.2f})"
            )

    return None

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
            if should_send_status(config):
                telegram.send_message(
                    "🚫 <b>Gold AI Signals - لا يوجد تحليل الآن</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"السبب: {session.get('reason', 'خارج ساعات التداول')}\n"
                    f"الجلسة: {session.get('current_session') or 'غير محدد'}\n"
                    f"الجودة: {session.get('session_quality', 'N/A')}\n"
                    "━━━━━━━━━━━━━━━━━━━━"
                )
            return  # ══ لا تحليل خارج الجلسات ══

        # ── تهيئة الخدمات ──
        market_data = MarketDataService(config)
        database = DatabaseService(config)

        # ── تهيئة خدمة AI ──
        ai_service = None
        ai_config = config.get("ai_service", {})

        if ai_config.get("enabled", False):
            try:
                ai_service = get_ai_service(config)
                logger.info("🤖 AI Service مفعّل: %s", ai_config.get("provider", "unknown"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("⚠️ فشل تهيئة AI: %s", exc)
                if not bool(ai_config.get("fallback_to_classic", True)):
                    telegram.send_error_alert(f"Groq إجباري لكن فشلت تهيئة AI: {exc}")
                    return

        logger.info("جلب بيانات السوق...")
        data = market_data.get_gold_data()
        if not data:
            logger.error("فشل في جلب البيانات")
            return

        # Safety: never send production signals from synthetic/demo prices on GitHub Actions.
        allow_synthetic = bool(config.get("data_source", {}).get("allow_synthetic_in_production", False))
        synthetic_sources = synthetic_timeframe_sources(data)
        if os.environ.get("GITHUB_ACTIONS") == "true" and synthetic_sources and not allow_synthetic:
            message = f"Analysis blocked: synthetic_demo data detected in production timeframes: {', '.join(sorted(set(synthetic_sources)))}. Configure TWELVE_DATA_API_KEY."
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
        all_results["daily_bias"] = run_agent("daily_bias", DailyBiasAgent(config), data)
        if config.get("ai_news_interpretation", {}).get("enabled", True):
            all_results["news_ai"] = await NewsInterpreter(config).interpret(
                all_results["news"],
                {
                    "current_price": all_results.get("current_price"),
                    "daily_bias": all_results.get("daily_bias"),
                    "technical_summary": all_results.get("technical", {}).get("summary"),
                },
            )
            all_results["news"]["ai_interpretation"] = all_results["news_ai"]
        all_results["risk"] = RiskManagementAgent(config).evaluate(all_results)
        all_results["dynamic_risk"] = DynamicRiskManager(config).evaluate(database)
        all_results["memory_rules"] = database.get_active_memory_rules(
            limit=int(config.get("ai_memory_rules", {}).get("max_active_rules_in_prompt", 8))
        )
        logger.info("🧠 قواعد الذاكرة النشطة المحملة: %s", len(all_results["memory_rules"]))

        # ── تشغيل وكيل القرار (مع AI) ──
        logger.info("تشغيل وكيل القرار (AI-enabled)...")

        # --- 1) LearningService wired ---
        learning_service = None
        try:
            learning_service = get_learning_service(database, config)
        except Exception:
            learning_service = None
        decision = await DecisionAgent(config, ai_service, learning_service).decide_async(all_results)

        decision["dynamic_risk"] = all_results.get("dynamic_risk", {})
        logger.info(
            "القرار: %s - الثقة: %s%% - %s | DynamicRisk=%s",
            decision.get("decision"),
            decision.get("confidence"),
            decision.get("summary"),
            decision.get("dynamic_risk", {}).get("summary"),
        )

        # ── إضافة وضع التشغيل/التداول الحالي للقرار ──
        github_event = os.environ.get("GITHUB_EVENT_NAME", "local")
        operation_mode = str(config.get("operation_mode", "observation")).lower()
        decision["run_source"] = "scheduled" if github_event == "schedule" else "manual" if github_event == "workflow_dispatch" else github_event
        decision["operation_mode"] = operation_mode
        groq_obs_cfg = config.get("groq_observation_mode", {}) or {}
        if operation_mode == "observation" and groq_obs_cfg.get("enabled", False) and groq_obs_cfg.get("allow_single_agent_context", False):
            decision["decision_mode"] = "One-Agent + Groq"
        else:
            decision["decision_mode"] = "Groq Observation" if operation_mode == "observation" and groq_obs_cfg.get("enabled", False) else "Production Strict"
        decision["requires_three_agents"] = bool((config.get("operation_modes", {}).get(operation_mode, {}) or {}).get("requires_three_agents", operation_mode != "observation"))
        trading_mode = str(config.get("trading_mode", "paper")).lower()
        paper_config = config.get("paper_trading", {}) or {}
        decision["trading_mode"] = trading_mode
        decision["paper_trading"] = trading_mode == "paper" or bool(paper_config.get("enabled", False))
        decision["paper_config"] = {
            "starting_balance": paper_config.get("starting_balance"),
            "currency": paper_config.get("currency", "USD"),
            "default_lot_size": paper_config.get("default_lot_size", 0.01),
        }
        if decision.get("signal"):
            decision["signal"]["trading_mode"] = trading_mode
            decision["signal"]["paper_trading"] = decision["paper_trading"]

        # ── إرسال الإشارة إذا كانت مؤهلة ──
        if decision.get("decision") in {"BUY", "SELL"}:
            settings = config.get("risk_settings", {})
            max_daily = int(settings.get("max_daily_signals", 8))
            max_open = int(settings.get("max_open_trades", 3))
            today_signals = database.get_today_signals_count()
            open_trades = database.get_open_trades()
            if today_signals >= max_daily:
                logger.info("تم الوصول للحد الأقصى من الإشارات اليومية: %s", max_daily)
                if should_send_status(config):
                    telegram.send_message(f"🟡 No signal: daily signal limit reached ({max_daily}).")
                return
            if len(open_trades) >= max_open:
                logger.info("تم الوصول للحد الأقصى للصفقات المفتوحة: %s", max_open)
                if should_send_status(config):
                    telegram.send_message(f"🟡 No signal: max open trades reached ({max_open}).")
                return

            dynamic_block_reason = should_block_signal(decision, all_results.get("dynamic_risk", {}))
            if dynamic_block_reason:
                logger.info("تم منع الإشارة بسبب Dynamic Risk: %s", dynamic_block_reason)
                if should_send_status(config):
                    telegram.send_message(
                        "🟡 <b>تم منع الإشارة بواسطة Dynamic Risk</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"القرار: {decision.get('decision')}\n"
                        f"السبب: {dynamic_block_reason}\n"
                        f"الحالة: {all_results.get('dynamic_risk', {}).get('level')}\n"
                        "━━━━━━━━━━━━━━━━━━━━"
                    )
                return

            duplicate_reason = duplicate_signal_reason(decision, database, config)
            if duplicate_reason:
                logger.info("تم منع إشارة مكررة: %s", duplicate_reason)
                if should_send_status(config):
                    telegram.send_message(
                        "🟡 <b>تم منع إشارة مكررة</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"القرار: {decision.get('decision')}\n"
                        f"السبب: {duplicate_reason}\n"
                        "━━━━━━━━━━━━━━━━━━━━"
                    )
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
                decision.get("warnings"),
            )
            if should_send_status(config):
                warnings = decision.get("warnings") or []
                warnings_text = "\n".join(f"• {w}" for w in warnings[:6]) or "• لا توجد تحذيرات؛ القرار الحالي WAIT فقط"
                telegram.send_message(
                    "🟡 <b>Gold AI Signals - لا توجد إشارة مؤهلة</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"السعر الحالي: {decision.get('current_price', all_results.get('current_price'))}\n"
                    f"القرار: {decision.get('decision', 'WAIT')}\n"
                    f"الثقة: {decision.get('confidence', 0)}%\n"
                    f"السبب: {decision.get('summary', decision.get('reasoning', 'غير محدد'))}\n\n"
                    f"<b>ملاحظات:</b>\n{warnings_text}\n"
                    "━━━━━━━━━━━━━━━━━━━━"
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
