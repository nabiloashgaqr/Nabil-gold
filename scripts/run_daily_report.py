"""سكريبت التقرير اليومي v2.0.

يعمل يومياً عبر GitHub Actions الساعة 23:00 UTC:
1. تقييم أداء الوكلاء
2. إرسال تقرير الأداء
3. إرسال تقرير الصفقات المفتوحة
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.daily_report_agent import DailyReportAgent
from services.database import DatabaseService
from services.telegram_bot import TelegramBot
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


async def send_open_trades_report(db: DatabaseService, telegram: TelegramBot):
    """إرسال تقرير الصفقات المفتوحة"""
    
    try:
        # Get open trades
        query = """
            SELECT id, signal_id, trade_type, entry_price, 
                   current_price, sl, tp1, tp2, opened_at
            FROM trades 
            WHERE status IN ('OPEN', 'PARTIAL', 'TP1_HIT')
            ORDER BY opened_at DESC
        """
        trades = await db.execute_query(query)
        
        if not trades:
            await telegram.send_message(
                '📊 *تقرير الصفقات المفتوحة*\n\n'
                '❌ لا توجد صفقات مفتوحة حالياً'
            )
            return
        
        # Build report
        lines = [
            '━━━━━━━━━━━━━━━━━━━━',
            '📊 *تقرير الصفقات المفتوحة*',
            f'📅 تاريخ التقرير: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")} UTC',
            f'📈 عدد الصفقات المفتوحة: {len(trades)}',
            '━━━━━━━━━━━━━━━━━━━━',
            ''
        ]
        
        total_pnl = 0
        
        for trade in trades:
            trade_type = trade.get('trade_type', 'BUY')
            entry = float(trade.get('entry_price', 0))
            current = float(trade.get('current_price', entry))
            sl = float(trade.get('sl', 0)) if trade.get('sl') else 0
            tp1 = float(trade.get('tp1', 0)) if trade.get('tp1') else 0
            tp2 = float(trade.get('tp2', 0)) if trade.get('tp2') else 0
            status = trade.get('status', 'OPEN')
            
            # Calculate P/L
            if trade_type == 'BUY':
                pnl_points = current - entry
            else:
                pnl_points = entry - current
            
            total_pnl += pnl_points
            
            # Calculate progress to targets
            if sl and entry and sl != entry:
                risk = abs(entry - sl)
                if risk > 0:
                    progress = (abs(pnl_points) / risk) * 100
                else:
                    progress = 0
            else:
                progress = 50  # Default
            
            # Status emoji
            if status == 'TP1_HIT':
                emoji = '🟡'
                status_text = '(TP1 reached)'
            elif pnl_points > 0:
                emoji = '🟢'
                status_text = ''
            elif pnl_points < 0:
                emoji = '🔴'
                status_text = ''
            else:
                emoji = '⚪'
                status_text = ''
            
            lines.append(f'{emoji} *{trade_type}* {status_text}')
            lines.append(f'├ Entry: {entry:.2f}')
            lines.append(f'├ Current: {current:.2f} ({pnl_points:+.2f})')
            if sl:
                lines.append(f'├ SL: {sl:.2f}')
            if tp1:
                lines.append(f'├ TP1: {tp1:.2f}')
            if tp2:
                lines.append(f'├ TP2: {tp2:.2f}')
            lines.append(f'└ Progress: {min(progress, 100):.0f}%')
            lines.append('')
        
        # Summary
        total_emoji = '🟢' if total_pnl > 0 else '🔴' if total_pnl < 0 else '⚪'
        lines.append('━━━━━━━━━━━━━━━━━━━━')
        lines.append(f'{total_emoji} *إجمالي P/L:* {total_pnl:+.2f} points')
        lines.append('━━━━━━━━━━━━━━━━━━━━')
        
        await telegram.send_message('\n'.join(lines))
        logger.info(f"تم إرسال تقرير {len(trades)} صفقات مفتوحة")
        
    except Exception as e:
        logger.error(f"خطأ في تقرير الصفقات المفتوحة: {e}")


def main() -> None:
    """Generate and send daily report."""
    logger.info("بدء التقرير اليومي: %s", datetime.now(timezone.utc).isoformat())
    
    config = load_config()
    telegram = TelegramBot(config)
    database = DatabaseService(config)
    
    try:
        # 1️⃣ إرسال التقرير اليومي
        trades = database.get_today_trades()
        report = DailyReportAgent(config).generate(trades)
        telegram.send_daily_report(report["text"])
        logger.info("تم إرسال تقرير الأداء. عدد الصفقات: %s", len(trades))
        
    except Exception as exc:
        logger.exception("خطأ في التقرير اليومي")
        telegram.send_error_alert(str(exc))
    
    # 2️⃣ إرسال تقرير الصفقات المفتوحة (async)
    try:
        asyncio.run(send_open_trades_report(database, telegram))
    except Exception as exc:
        logger.exception("خطأ في تقرير الصفقات المفتوحة")


if __name__ == "__main__":
    main()