"""سكريبت التقرير اليومي v2.0.

يعمل يومياً عبر GitHub Actions الساعة 23:00 UTC:
1. إرسال تقرير أداء اليوم
2. إرسال تقرير الصفقات المفتوحة

ملاحظة: لا يعتمد هذا السكريبت على SQL raw حتى يعمل مع DatabaseService الحالي
سواءً كان التخزين Supabase أو JSON fallback.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.daily_report_agent import DailyReportAgent
from services.database import DatabaseService
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def _trade_value(trade: dict, *keys: str, default=None):
    """Return the first existing/non-empty value from possible schema aliases."""
    for key in keys:
        value = trade.get(key)
        if value is not None and value != "":
            return value
    return default


def send_open_trades_report(db: DatabaseService, telegram: TelegramService) -> None:
    """إرسال تقرير الصفقات المفتوحة بدون الاعتماد على execute_query."""
    try:
        trades = db.get_open_trades()

        if not trades:
            telegram.send_message(
                "📊 <b>Open Trades Report</b>\n\n"
                "❌ No open trades currently"
            )
            return

        lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            "📊 <b>Open Trades Report</b>",
            f"📅 Report time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
            f"📈 Open trades: {len(trades)}",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
        ]

        total_pnl = 0.0

        for trade in trades:
            trade_type = str(_trade_value(trade, "type", "trade_type", default="BUY")).upper()
            entry = float(_trade_value(trade, "entry_price", default=0) or 0)
            current = float(_trade_value(trade, "current_price", default=entry) or entry)
            sl = float(_trade_value(trade, "stop_loss", "sl", default=0) or 0)
            tp1 = float(_trade_value(trade, "tp1", default=0) or 0)
            tp2 = float(_trade_value(trade, "tp2", "take_profit", default=0) or 0)
            status = str(_trade_value(trade, "status", default="OPEN"))

            pnl_points = current - entry if trade_type == "BUY" else entry - current
            total_pnl += pnl_points

            risk = abs(entry - sl) if sl and entry else 0.0
            progress = min(abs(pnl_points) / risk * 100, 100) if risk > 0 else 0

            if status == "TP1_HIT":
                emoji = "🟡"
                status_text = "(TP1 reached)"
            elif pnl_points > 0:
                emoji = "🟢"
                status_text = ""
            elif pnl_points < 0:
                emoji = "🔴"
                status_text = ""
            else:
                emoji = "⚪"
                status_text = ""

            lines.append(f"{emoji} <b>{trade_type}</b> {status_text}")
            lines.append(f"├ Entry: {entry:.2f}")
            lines.append(f"├ Current: {current:.2f} ({pnl_points:+.2f})")
            if sl:
                lines.append(f"├ SL: {sl:.2f}")
            if tp1:
                lines.append(f"├ TP1: {tp1:.2f}")
            if tp2:
                lines.append(f"├ TP2: {tp2:.2f}")
            lines.append(f"└ Progress: {progress:.0f}%")
            lines.append("")

        total_emoji = "🟢" if total_pnl > 0 else "🔴" if total_pnl < 0 else "⚪"
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"{total_emoji} <b>Total P/L:</b> {total_pnl:+.2f} points")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

        telegram.send_message("\n".join(lines))
        logger.info("تم إرسال تقرير %s صفقات مفتوحة", len(trades))

    except Exception as exc:  # noqa: BLE001
        logger.exception("خطأ في تقرير الصفقات المفتوحة: %s", exc)
        telegram.send_error_alert(f"Open trades report failed: {exc}")


def main() -> None:
    """Generate and send daily report."""
    logger.info("بدء التقرير اليومي: %s", datetime.now(timezone.utc).isoformat())

    config = load_config()
    telegram = TelegramService(config)
    database = DatabaseService(config)

    try:
        trades = database.get_today_trades()
        agent = DailyReportAgent(config)
        report = agent.generate(trades)
        telegram.send_daily_report(report["text"])
        logger.info("تم إرسال تقرير الأداء. عدد الصفقات: %s", len(trades))

        # Weekly AI-style performance summary on Friday in configured timezone.
        tz_name = config.get("schedule", {}).get("timezone", "Asia/Jerusalem")
        try:
            local_now = datetime.now(ZoneInfo(str(tz_name)))
        except Exception:  # noqa: BLE001
            local_now = datetime.now(timezone.utc)
        if local_now.weekday() == 4:  # Friday
            weekly_trades = database.get_recent_trades(limit=150)
            weekly_report = agent.generate_weekly(weekly_trades)
            telegram.send_daily_report(weekly_report["text"])
            logger.info("تم إرسال التقرير الأسبوعي. عدد الصفقات: %s", len(weekly_trades))
    except Exception as exc:  # noqa: BLE001
        logger.exception("خطأ في التقرير اليومي")
        telegram.send_error_alert(str(exc))

    send_open_trades_report(database, telegram)


if __name__ == "__main__":
    main()
