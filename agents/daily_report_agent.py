"""Daily Report Agent.

يجمع صفقات اليوم من قاعدة البيانات ويحسب إحصائيات الأداء ثم ينشئ تقريراً
مناسباً للإرسال إلى تليجرام.
"""

from __future__ import annotations

import html
from datetime import date
from typing import Any, Dict, List

from agents.base_agent import BaseAgent


class DailyReportAgent(BaseAgent):
    """Build daily performance report from trades."""

    name = "daily_report"

    def generate(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(trades)
        winners = [t for t in trades if self._pnl(t) > 0 or t.get("status") in {"TP1_HIT", "TP2_HIT"}]
        losers = [t for t in trades if self._pnl(t) < 0 or t.get("status") == "SL_HIT"]
        breakeven = [t for t in trades if t.get("status") == "BE_HIT" or self._pnl(t) == 0 and t.get("status") not in {"OPEN", "TP1_HIT"}]
        open_trades = [t for t in trades if t.get("status") in {"OPEN", "TP1_HIT"}]
        pnl_values = [self._pnl(t) for t in trades]
        gross_profit = sum(x for x in pnl_values if x > 0)
        gross_loss = abs(sum(x for x in pnl_values if x < 0))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else (round(gross_profit, 2) if gross_profit else 0)
        win_rate = round((len(winners) / total) * 100, 1) if total else 0
        best = max(pnl_values) if pnl_values else 0
        worst = min(pnl_values) if pnl_values else 0
        avg_win = round(gross_profit / len(winners), 1) if winners else 0
        avg_loss = round(gross_loss / len(losers), 1) if losers else 0
        net = round(sum(pnl_values), 1)
        stats = {
            "total": total,
            "wins": len(winners),
            "losses": len(losers),
            "breakeven": len(breakeven),
            "open": len(open_trades),
            "win_rate": win_rate,
            "net_points": net,
            "best_trade": round(best, 1),
            "worst_trade": round(worst, 1),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
        }
        return {"agent": self.name, "date": date.today().isoformat(), "stats": stats, "text": self._format_report(stats)}

    def _format_report(self, stats: Dict[str, Any]) -> str:
        return f"""
📋 <b>التقرير اليومي - XAU/USD</b>
━━━━━━━━━━━━━━━━━━━━━
📅 <b>التاريخ:</b> {html.escape(date.today().isoformat())}

📊 <b>الإحصائيات:</b>
• عدد الصفقات: {stats['total']}
• رابحة: {stats['wins']} ✅
• خاسرة: {stats['losses']} ❌
• تعادل: {stats['breakeven']} ➖
• مفتوحة: {stats['open']} 🔄
• نسبة النجاح: {stats['win_rate']}%

💰 <b>النتائج:</b>
• صافي النقاط: {stats['net_points']:+.1f} نقطة
• أفضل صفقة: {stats['best_trade']:+.1f} نقطة
• أسوأ صفقة: {stats['worst_trade']:+.1f} نقطة
• متوسط الربح: {stats['avg_win']:.1f}
• متوسط الخسارة: -{stats['avg_loss']:.1f}
• Profit Factor: {stats['profit_factor']}

📈 <b>تحليل الأداء:</b>
• هذه نسخة تقرير المرحلة الأولى.
• سيتم لاحقاً ربط الأداء بدقة كل وكيل وفترة التداول.

⚠️ <b>تذكير:</b> هذا نظام تجريبي/تعليمي وليس توصية مالية.
""".strip()

    def _pnl(self, trade: Dict[str, Any]) -> float:
        for key in ("final_pnl", "current_pnl", "current_pnl_points"):
            value = trade.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return 0.0
