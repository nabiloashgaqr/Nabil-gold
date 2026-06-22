"""سكريبت التحليل الرئيسي.

يعمل كل 15 دقيقة عبر GitHub Actions. يجلب بيانات الذهب، يشغل الوكلاء (مع AI)،
يطبق إدارة المخاطر والقرار، ثم يحفظ ويرسل الإشارة إذا كانت مؤهلة.
"""

from __future__ import annotations

import logging
import os
import sys
import html
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
    """Always send status updates during trading hours.
    The user wants at least one informative message every hour (even if just "waiting").
    """
    # Always send during trading hours so user gets regular market status
    return True


def should_send_hourly_status(config: Dict[str, Any]) -> bool:
    """Send a clean market status update roughly once per hour (user preference).
    Runs every 10 min, so we only send when minute < 10.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return now.minute < 10




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
                return f"A {direction} trade is already open: {trade.get('id', 'unknown')}"

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
            return f"A similar {direction} signal was sent within the last {lookback_minutes} min: {trade.get('id', 'unknown')}"
        if abs(entry_price - previous_entry) <= tolerance:
            return (
                f"Duplicate {direction} signal: price close to a recent signal "
                f"({previous_entry:.2f} vs {entry_price:.2f}, tolerance={tolerance:.2f})"
            )

    return None

def _dedupe_warnings(warnings: list) -> list:
    """Collapse duplicate / overlapping warnings before showing them in Telegram.

    The decision pipeline can raise two near-identical warnings for the same
    cause - e.g. the rule-based NewsRiskAgent ("News blocked: ...") AND the Groq
    news interpreter ("AI News blocked trading: ...") both fire for the same
    upcoming event. Showing both is noise. This:
      * drops exact duplicates,
      * keeps only the FIRST news-block warning (rule-based, most concise) and
        drops the later AI-news one when both are present,
      * preserves the order of all other warnings.
    """
    seen: set = set()
    result: list = []
    news_block_kept = False
    for w in warnings:
        text = str(w).strip()
        if not text:
            continue
        key = " ".join(text.lower().split())
        if key in seen:
            continue
        lower = text.lower()
        is_news_block = lower.startswith("news blocked") or lower.startswith("ai news blocked")
        if is_news_block:
            if news_block_kept:
                # A news block was already shown; skip the redundant duplicate.
                continue
            news_block_kept = True
        seen.add(key)
        result.append(text)
    return result


def run_agent(agent_name: str, agent: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    """Run one agent safely so one failure does not stop the workflow."""
    try:
        logger.info("Running agent: %s", agent_name)
        return agent.analyze(data)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent %s failed", agent_name)
        return {"agent": agent_name, "signal": "WAIT", "confidence": 0, "reasoning": f"Agent failed: {exc}"}


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
                    "🟡 <b>Gold AI Signals — Market Status</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 Price: {price_text}\n"
                    f"🎯 Decision: WAIT\n"
                    f"📊 Groq: {ai.get('confidence', decision.get('confidence', 0)):.0f}%  •  Agents ≥{agent_thr}%  •  Groq ≥{groq_thr}%\n\n"
                    f"<b>Reason:</b>\n{html.escape(reason_text)}\n\n"
                    f"<b>Notes:</b>\n{warnings_text}\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "<i>Periodic market status • Next check in ~10 min</i>"
                )
    except Exception as exc:  # noqa: BLE001
        logger.exception("خطأ في التحليل")
        telegram.send_error_alert(str(exc))


def main() -> None:
    """نقطة الدخول الرئيسية."""
    import asyncio

    asyncio.run(run_analysis_async())


if __name__ == "__main__":
    main()
