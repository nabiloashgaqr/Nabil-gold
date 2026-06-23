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

    update_outside_hours = bool(config.get("trade_management", {}).get("update_outside_trading_hours", False))
    force_update = os.environ.get("FORCE_TRADE_UPDATE", "false").lower() in {"1", "true", "yes"}
    if not session.get("trading_allowed") and not update_outside_hours and not force_update:
        logger.info(
            "🚫 خارج ساعات تحديث الصفقات (%s) - لا تحديث حالياً. السبب: %s",
            session.get("current_session") or "غير محدد",
            session.get("reason", ""),
        )
        return  # ══ لا تحديث خارج الجلسات إلا عند FORCE_TRADE_UPDATE ══
    if not session.get("trading_allowed") and (update_outside_hours or force_update):
        logger.info(
            "ℹ️ خارج ساعات التحديث العادية، لكن التحديث مستمر بسبب update_outside_trading_hours أو FORCE_TRADE_UPDATE"
        )

    try:
        market_data = MarketDataService(config)
        telegram = TelegramService(config)
        database = DatabaseService(config)
        manager = OpenTradesManager(config)

        # Use an OHLC payload instead of blind quote fallback so production never
        # manages/closes trades using synthetic_demo prices if the market API fails.
        price_payload = market_data.get_ohlcv(config.get("primary_timeframe", "15m"), outputsize=60)
        allow_synthetic = bool(config.get("data_source", {}).get("allow_synthetic_in_production", False))
        if os.environ.get("GITHUB_ACTIONS") == "true" and price_payload.get("source") == "synthetic_demo" and not allow_synthetic:
            logger.error("تم إيقاف تحديث الصفقات: السعر من synthetic_demo. راجع TWELVE_DATA_API_KEY.")
            telegram.send_error_alert("Trade updates stopped: price is from synthetic_demo. Check TWELVE_DATA_API_KEY.")
            return
        current_price = price_payload.get("current_price")
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
        total_events = 0
        for evaluation in evaluations:
            if evaluation.get("events"):
                total_events += len(evaluation.get("events", []))
                logger.info(
                    "تحديث الصفقة %s: %s | %s -> %s | PnL=%s",
                    evaluation.get("trade_id"),
                    ",".join(evaluation.get("events", [])),
                    evaluation.get("old_status"),
                    evaluation.get("new_status"),
                    evaluation.get("pnl_points"),
                )

        # ── Confirmation message ───────────────────────────────────────────
        # A Telegram event is only sent when something actually happens
        # (TP1/TP2/SL/BE/trailing/near-tp1/...). On a quiet hour there are no
        # events, so the user would otherwise see "nothing happened" and wonder
        # whether the run worked. On MANUAL runs (or when notify_on_trade_update
        # is enabled) send a short confirmation summary so it's never silent.
        manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" or force_update
        notify_updates = bool(config.get("notifications", {}).get("notify_on_trade_update", False))
        if (manual or notify_updates) and total_events == 0:
            import html as _html
            if open_trades:
                lines = []
                for ev in evaluations:
                    pnl = ev.get("pnl_points", 0)
                    sign = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "➖"
                    lines.append(
                        f"{sign} <code>{_html.escape(str(ev.get('trade_id')))}</code> "
                        f"{_html.escape(str(ev.get('new_status')))} · {pnl:+.1f} pts"
                    )
                body = "\n".join(lines[:20])
                telegram.send_message(
                    "🔄 <b>Trade Update — no new events</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 Price: {float(current_price):.2f}\n"
                    f"📊 Open trades: {len(open_trades)} · PnL refreshed\n\n"
                    f"{body}\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "<i>No TP/SL/breakeven/trailing triggered this cycle.</i>"
                )
            else:
                telegram.send_message(
                    "🔄 <b>Trade Update</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 Price: {float(current_price):.2f}\n"
                    "📊 No open trades to manage."
                )

        logger.info("اكتمل تحديث الصفقات (events=%s, open=%s)", total_events, len(open_trades))
    except Exception as exc:  # noqa: BLE001
        logger.exception("خطأ في التحديث: %s", exc)


if __name__ == "__main__":
    main()
