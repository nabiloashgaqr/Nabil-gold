"""Daily/Weekly Report Agent.

يجمع صفقات اليوم أو فترة محددة من قاعدة البيانات ويحسب إحصائيات الأداء ثم ينشئ
تقريراً مناسباً للإرسال إلى تليجرام، مع تحليل حسب الوكيل والاتجاه والجلسة.
"""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List

from agents.base_agent import BaseAgent


class DailyReportAgent(BaseAgent):
    """Build daily/weekly performance reports from trades."""

    name = "daily_report"

    def generate(self, trades: List[Dict[str, Any]], title: str = "التقرير اليومي") -> Dict[str, Any]:
        stats = self._stats(trades)
        return {"agent": self.name, "date": date.today().isoformat(), "stats": stats, "text": self._format_report(stats, title=title)}

    def generate_weekly(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        stats = self._stats(trades)
        return {"agent": self.name, "date": date.today().isoformat(), "stats": stats, "text": self._format_report(stats, title="التقرير الأسبوعي")}

    def _stats(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(trades)
        winners = [t for t in trades if self._pnl(t) > 0 or t.get("status") in {"TP1_HIT", "TP2_HIT"}]
        losers = [t for t in trades if self._pnl(t) < 0 or t.get("status") == "SL_HIT"]
        breakeven = [t for t in trades if t.get("status") == "BE_HIT" or self._pnl(t) == 0 and t.get("status") not in {"OPEN", "TP1_HIT"}]
        open_trades = [t for t in trades if t.get("status") in {"OPEN", "TP1_HIT", "PARTIAL"}]
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
        by_direction = self._by_direction(trades)
        by_agent = self._by_agent(trades)
        by_session = self._by_session(trades)
        recommendations = self._recommendations(total, win_rate, net, profit_factor, by_agent, by_direction)
        return {
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
            "by_direction": by_direction,
            "by_agent": by_agent,
            "by_session": by_session,
            "recommendations": recommendations,
        }

    def _format_report(self, stats: Dict[str, Any], title: str = "Daily Report") -> str:
        title_en = "Weekly Report" if "أسبوع" in title or "Weekly" in title else "Daily Report"
        agent_lines = self._format_ranked(stats.get("by_agent", {}), empty="No agent-source data yet")
        direction = stats.get("by_direction", {})
        recommendations = "\n".join(f"• {html.escape(str(x))}" for x in stats.get("recommendations", [])[:6]) or "• Not enough data for recommendations yet"
        return f"""
📋 <b>{html.escape(title_en)} - XAU/USD</b>
━━━━━━━━━━━━━━━━━━━━━
📅 <b>Date:</b> {html.escape(date.today().isoformat())}

📊 <b>Statistics:</b>
• Total trades: {stats['total']}
• Wins: {stats['wins']} ✅
• Losses: {stats['losses']} ❌
• Breakeven: {stats['breakeven']} ➖
• Open: {stats['open']} 🔄
• Win rate: {stats['win_rate']}%

💰 <b>Results:</b>
• Net points: {stats['net_points']:+.1f}
• Best trade: {stats['best_trade']:+.1f}
• Worst trade: {stats['worst_trade']:+.1f}
• Avg win: {stats['avg_win']:.1f}
• Avg loss: -{stats['avg_loss']:.1f}
• Profit Factor: {stats['profit_factor']}

🧭 <b>By Direction:</b>
• BUY: {direction.get('BUY', {}).get('count', 0)} | Net {direction.get('BUY', {}).get('net', 0):+}
• SELL: {direction.get('SELL', {}).get('count', 0)} | Net {direction.get('SELL', {}).get('net', 0):+}

🤖 <b>Signal Source / Agent Performance:</b>
{agent_lines}

🧠 <b>Recommendations:</b>
{recommendations}

⚠️ <b>Reminder:</b> Paper-trading educational system. Not financial advice.
""".strip()

    def _by_direction(self, trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out = {"BUY": {"count": 0, "net": 0.0}, "SELL": {"count": 0, "net": 0.0}}
        for trade in trades:
            side = str(trade.get("type") or trade.get("trade_type") or "").upper()
            if side in out:
                out[side]["count"] += 1
                out[side]["net"] = round(out[side]["net"] + self._pnl(trade), 1)
        return out

    def _by_agent(self, trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "net": 0.0})
        for trade in trades:
            snapshot = trade.get("signal_snapshot") or {}
            source = (snapshot.get("agent_context") or {}).get("agent") or "consensus"
            pnl = self._pnl(trade)
            out[source]["count"] += 1
            out[source]["net"] = round(out[source]["net"] + pnl, 1)
            if pnl > 0:
                out[source]["wins"] += 1
            elif pnl < 0:
                out[source]["losses"] += 1
        for value in out.values():
            closed = value["wins"] + value["losses"]
            value["win_rate"] = round(value["wins"] / closed * 100, 1) if closed else 0
        return dict(out)

    def _by_session(self, trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "net": 0.0})
        for trade in trades:
            snapshot = trade.get("signal_snapshot") or {}
            session = (snapshot.get("session_info") or {}).get("current_session") or "unknown"
            out[session]["count"] += 1
            out[session]["net"] = round(out[session]["net"] + self._pnl(trade), 1)
        return dict(out)

    def _recommendations(self, total: int, win_rate: float, net: float, profit_factor: float, by_agent: Dict[str, Any], by_direction: Dict[str, Any]) -> List[str]:
        recs: List[str] = []
        if total < 5:
            recs.append("البيانات قليلة؛ استمر في Paper Trading قبل الحكم على الأداء.")
        if win_rate < 45 and total >= 5:
            recs.append("نسبة النجاح منخفضة؛ ارفع شروط الثقة/الموثوقية أو امنع إشارات D/E.")
        if net < 0:
            recs.append("صافي النقاط سلبي؛ راجع AI Trade Reviews وMemory Rules قبل زيادة المخاطرة.")
        if profit_factor and profit_factor < 1:
            recs.append("Profit Factor أقل من 1؛ خفّض عدد الإشارات التجريبية أو شدد Risk Grade.")
        if by_agent:
            worst = sorted(by_agent.items(), key=lambda x: x[1].get("net", 0))[0]
            best = sorted(by_agent.items(), key=lambda x: x[1].get("net", 0), reverse=True)[0]
            recs.append(f"أفضل مصدر حتى الآن: {best[0]} | Net {best[1].get('net', 0):+}")
            if worst[1].get("count", 0) >= 2 and worst[1].get("net", 0) < 0:
                recs.append(f"راقب المصدر الضعيف: {worst[0]} | Net {worst[1].get('net', 0):+}")
        if by_direction.get("BUY", {}).get("net", 0) < 0 and by_direction.get("SELL", {}).get("net", 0) > 0:
            recs.append("SELL يتفوق على BUY في العينة الحالية؛ راقب Daily Bias قبل الشراء.")
        return recs[:8]

    def _format_ranked(self, data: Dict[str, Dict[str, Any]], empty: str) -> str:
        if not data:
            return f"• {empty}"
        lines = []
        for name, value in sorted(data.items(), key=lambda x: x[1].get("net", 0), reverse=True)[:6]:
            lines.append(f"• {html.escape(str(name))}: {value.get('count', 0)} trades | WR {value.get('win_rate', 0)}% | Net {value.get('net', 0):+}")
        return "\n".join(lines)

    def _pnl(self, trade: Dict[str, Any]) -> float:
        for key in ("final_pnl", "current_pnl", "current_pnl_points"):
            value = trade.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return 0.0
