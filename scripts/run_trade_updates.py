"""سكريبت تحديث الصفقات المفتوحة.

يعمل كل ساعة عبر GitHub Actions. يجلب السعر الحالي، يمرر الصفقات المفتوحة
إلى OpenTradesManager، ثم يتم تحديث Supabase وإرسال تحديثات Telegram.
✏️ يتحقق من ساعات التداول ويخرج إذا كان خارج الجلسات.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager
from agents.trading_session_agent import TradingSessionAgent
from services.database import DatabaseService
from services.market_data import MarketDataService
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    """تحديث الصفقات المفتوحة."""
    logger.info("بدء تحديث الصفقات: %s", datetime.now(timezone.utc).isoformat())
    config = load_config()

    # ── فحص ساعات التداول ──
    session = TradingSessionAgent(config).check()
    logger.info(
        "🔍 الجلسة: %s | الجودة: %s | مسموح: %s",
        session.get("current_session") or "خارج الجلسة",
        session.get("session_quality", "N/A"),
        session.get("trading_allowed"),
    )

    if not session.get("trading_allowed"):
        logger.info(
            "🚫 خارج ساعات التداول (%s) - لا تحديث حالياً. السبب: %s",
            session.get("current_session") or "غير محدد",
            session.get("reason", ""),
        )
        return  # ══ لا تحديث خارج الجلسات ══

    try:
        market_data = MarketDataService(config)
        telegram = TelegramService(config)
        database = DatabaseService(config)
        manager = OpenTradesManager(config)

        current_price = market_data.get_current_price()
        if not current_price:
            logger.error("فشل في جلب السعر")
            return

        open_trades = database.get_open_trades()
        logger.info("عدد الصفقات المفتوحة: %s", len(open_trades))

        evaluations = manager.update_trades(
            open_trades=open_trades,
            current_price=float(current_price),
            database=database,
            telegram=telegram,
            now=datetime.now(timezone.utc),
        )
        for evaluation in evaluations:
            if evaluation.get("events"):
                logger.info(
                    "تحديث الصفقة %s: %s | %s -> %s | PnL=%s",
                    evaluation.get("trade_id"),
                    ",".join(evaluation.get("events", [])),
                    evaluation.get("old_status"),
                    evaluation.get("new_status"),
                    evaluation.get("pnl_points"),
                )

        logger.info("اكتمل تحديث الصفقات")
    except Exception as exc:  # noqa: BLE001
        logger.exception("خطأ في التحديث: %s", exc)


if __name__ == "__main__":
    main()
