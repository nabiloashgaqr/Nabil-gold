"""Daily/Weekly Report Agent.

Collects trades from the database, calculates performance statistics,
and builds a professional Telegram report with per-instrument breakdown,
trade details, and proper Profit Factor calculation.
"""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from utils.helpers import format_price


class DailyReportAgent(BaseAgent):
    """Build daily/weekly performance reports from trades."""

    name = "daily_report"

    def generate(self, trades: List[Dict[str, Any]], title: str = "Daily Report") -> Dict[str, Any]:
        stats = self._stats(trades)
        return {"agent": self.name, "date": date.today().isoformat(), "stats": stats, "text": self._format_report(stats, trades, title=title)}

    def generate_weekly(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        stats = self._stats(trades)
        return {"agent": self.name, "date": date.today().isoformat(), "stats": stats, "text": self._format_report(stats, trades, title="Weekly Report")}

    def _stats(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(trades)
        winners = [t for t in trades if self._pnl(t) > 0 or t.get("status") in {"TP1_HIT", "TP2_HIT"}]
        losers = [t for t in trades if self._pnl(t) < 0 or t.get("status") == "SL_HIT"]
        breakeven = [t for t in trades if t.get("status") == "BE_HIT" or self._pnl(t) == 0 and t.get("status") not in {"OPEN", "TP1_HIT"}]
        open_trades = [t for t in trades if t.get("status") in {"OPEN", "TP1_HIT", "PARTIAL"}]
        pnl_values = [self._pnl(t) for t in trades]
        gross_profit = sum(x for x in pnl_values if x > 0)
        gross_loss = abs(sum(x for x in pnl_values if x < 0))
        if gross_loss > 0:
            profit_factor = round(gross_profit / gross_loss, 2)
        else:
            profit_factor = 99.9 if gross_profit > 0 else 0
        win_rate = round((len(winners) / total) * 100, 1) if total else 0
        best = max(pnl_values) if pnl_values else 0
        worst = min(pnl_values) if pnl_values else 0
        avg_win = round(gross_profit / len(winners), 1) if winners else 0
        avg_loss = round(gross_loss / len(losers), 1) if losers else 0
        net = round(sum(pnl_values), 1)
        by_direction = self._by_direction(trades)
        by_agent = self._by_agent(trades)
        by_session = self._by_session(trades)
        by_instrument = self._by_instrument(trades)
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
            "gross_profit": round(gross_profit, 1),
            "gross_loss": round(gross_loss, 1),
            "by_direction": by_direction,
            "by_agent": by_agent,
            "by_session": by_session,
            "by_instrument": by_instrument,
            "recommendations": recommendations,
        }

    def _format_report(self, stats: Dict[str, Any], trades: List[Dict[str, Any]], title: str = "Daily Report") -> str:
        title_en = "Weekly Report" if "weekly" in title.lower() else "Daily Report"

        # Profit Factor
        pf = stats.get("profit_factor", 0)
        pf_display = "∞" if pf >= 99 else f"{pf:.2f}"

        # Per-instrument breakdown
        instrument_lines = self._format_instruments(stats.get("by_instrument", {}))

        # Direction breakdown
        direction = stats.get("by_direction", {})
        buy = direction.get("BUY", {})
        sell = direction.get("SELL", {})

        # Session breakdown
        session_map = {
            "Asian Session (00:00-07:00 UTC)": "🌏 Asian Session (00:00-07:00 UTC)",
            "London Session (07:00-12:00 UTC)": "🇬🇧 London Session (07:00-12:00 UTC)",
            "London-NY Overlap (12:00-16:00 UTC)": "🇺🇸🇬🇧 London-NY Overlap (12:00-16:00 UTC)",
            "New York Session (16:00-21:00 UTC)": "🇺🇸 New York Session (16:00-21:00 UTC)",
            "Late NY Session (21:00-00:00 UTC)": "🌙 Late NY Session (21:00-00:00 UTC)",
            "unknown": "❓ Unknown Session",
        }
        session_lines = []
        for session, data in sorted(stats.get("by_session", {}).items(), key=lambda x: x[1].get("net", 0), reverse=True):
            icon = "[+]" if data.get("net", 0) > 0 else "[-]" if data.get("net", 0) < 0 else "[=]"
            session_name = session_map.get(session, session)
            session_lines.append(
                f"  {icon} {session_name}: {data.get('count', 0)} trades | Net {data.get('net', 0):+.1f} pts"
            )
        session_section = "\n".join(session_lines) if session_lines else "  No session data"

        # Trade details
        trade_details = self._format_trade_details(trades)

        # Win/Loss streaks
        streaks = self._calculate_streaks(trades)

        # Risk metrics
        risk = self._risk_metrics(stats)

        # Recommendations
        recommendations = "\n".join(f"• {html.escape(str(x))}" for x in stats.get("recommendations", [])[:4]) or "• Not enough data yet"

        separator = "───────────────────────────────────"

        return f"""📊 SmartSignal — {title_en}
📅 Period: {html.escape(date.today().isoformat())}
{separator}

📈 SUMMARY
  Total: {stats['total']} trades
  ✅ Wins: {stats['wins']}  |  ❌ Losses: {stats['losses']}  |  ⚪ BE: {stats['breakeven']}  |  🔄 Open: {stats['open']}
  🎯 Win Rate: {stats['win_rate']}%
{separator}

💰 PERFORMANCE
  💵 Net: {stats['net_points']:+.1f} pts (${stats['net_points'] / 10:+.1f})
  📊 Gross Profit: +{stats['gross_profit']:.1f} pts  |  Gross Loss: -{stats['gross_loss']:.1f} pts
  ⚖️ Profit Factor: {pf_display}
  📈 Avg Win: +{stats['avg_win']:.1f}  |  Avg Loss: -{stats['avg_loss']:.1f}
  🏆 Best: {stats['best_trade']:+.1f}  |  💔 Worst: {stats['worst_trade']:+.1f}
{separator}

🎯 WIN/LOSS STREAKS
  🔥 Best Streak: {streaks['best_win']} wins
  ⚠️ Worst Streak: {streaks['worst_loss']} losses
  📍 Current: {streaks['current']}
{separator}

📊 BY INSTRUMENT
{instrument_lines}
{separator}

🧭 BY DIRECTION
  🔼 BUY: {buy.get('count', 0)} trades → {buy.get('net', 0):+.1f} pts
  🔽 SELL: {sell.get('count', 0)} trades → {sell.get('net', 0):+.1f} pts
{separator}

🌍 BY SESSION
{session_section}
{separator}

📋 TRADE DETAILS
{trade_details}
{separator}

{risk}
{separator}

💡 RECOMMENDATIONS
{recommendations}

⚠️ Paper trading only — not financial advice.""".strip()

    def _format_instruments(self, by_instrument: Dict[str, Dict[str, Any]]) -> str:
        if not by_instrument:
            return "  No instrument data"
        lines = []
        for symbol, data in sorted(by_instrument.items(), key=lambda x: x[1].get("net", 0), reverse=True):
            icon = "[+]" if data.get("net", 0) > 0 else "[-]" if data.get("net", 0) < 0 else "[=]"
            lines.append(
                f"  {icon} {symbol}: "
                f"{data.get('count', 0)} trades | "
                f"WR {data.get('win_rate', 0)}% | "
                f"Net {data.get('net', 0):+.1f} pts"
            )
        return "\n".join(lines)

    def _format_trade_details(self, trades: List[Dict[str, Any]]) -> str:
        if not trades:
            return ""

        closed = [t for t in trades if t.get("status") not in {"OPEN", "TP1_HIT", "PARTIAL"}]
        if not closed:
            return ""

        lines = ["TRADE DETAILS"]
        for t in closed[-10:]:  # Last 10 trades
            symbol = str(t.get("symbol") or "XAU/USD")
            side = str(t.get("type") or t.get("side") or "?").upper()
            entry = self._f(t.get("entry_price"))
            sl = self._f(t.get("stop_loss"))
            tp1 = self._f(t.get("tp1"))
            tp2 = self._f(t.get("tp2"))
            pnl = self._pnl(t)
            status = str(t.get("status", "?"))
            sl_moved = t.get("sl_moved_to_entry", False)

            # Result icon
            if pnl > 0:
                icon = "[+]"
            elif pnl < 0:
                icon = "[-]"
            else:
                icon = "[=]"

            # Status text
            status_text = status.replace("_", " ").title()
            if sl_moved and status in {"TP2_HIT", "BE_HIT"}:
                status_text += " (SL->Entry)"

            lines.append(
                f"  {icon} {side} {symbol} | "
                f"Entry {format_price(entry, symbol)} | "
                f"SL {format_price(sl, symbol)} | "
                f"TP1 {format_price(tp1, symbol)} | "
                f"TP2 {format_price(tp2, symbol)} | "
                f"{pnl:+.1f} pts | "
                f"{status_text}"
            )

        return "\n".join(lines)

    def _calculate_streaks(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        best_win = 0
        worst_loss = 0
        current_win = 0
        current_loss = 0

        for t in trades:
            pnl = self._pnl(t)
            if pnl > 0:
                current_win += 1
                current_loss = 0
                best_win = max(best_win, current_win)
            elif pnl < 0:
                current_loss += 1
                current_win = 0
                worst_loss = max(worst_loss, current_loss)
            else:
                current_win = 0
                current_loss = 0

        if current_win > 0:
            current = f"{current_win} wins 🔥"
        elif current_loss > 0:
            current = f"{current_loss} losses ⚠️"
        else:
            current = "neutral"

        return {
            "best_win": best_win,
            "worst_loss": worst_loss,
            "current": current,
        }

    def _risk_metrics(self, stats: Dict[str, Any]) -> str:
        win_rate = stats.get("win_rate", 0)
        pf = stats.get("profit_factor", 0)
        net = stats.get("net_points", 0)

        # Risk grade
        score = 0
        if win_rate >= 60:
            score += 2
        elif win_rate >= 50:
            score += 1
        if pf >= 2.0:
            score += 2
        elif pf >= 1.5:
            score += 1
        if net > 0:
            score += 1

        grades = {5: "A+", 4: "A", 3: "B", 2: "C", 1: "D", 0: "F"}
        grade = grades.get(score, "F")

        # Expectancy
        avg_win = stats.get("avg_win", 0)
        avg_loss = stats.get("avg_loss", 0)
        wr = win_rate / 100
        expectancy = (wr * avg_win) - ((1 - wr) * avg_loss) if avg_loss > 0 else avg_win * wr

        lines = [
            "RISK METRICS",
            f"  Risk Grade: {grade}",
            f"  Expectancy: {expectancy:+.1f} pts/trade",
            f"  Profit Factor: {pf:.2f}" if pf < 99 else f"  Profit Factor: ∞",
        ]

        if expectancy > 0:
            lines.append("  System is profitable")
        else:
            lines.append("  System needs review")

        return "\n".join(lines)

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

    def _by_instrument(self, trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "net": 0.0})
        for trade in trades:
            symbol = str(trade.get("symbol") or "XAU/USD")
            pnl = self._pnl(trade)
            out[symbol]["count"] += 1
            out[symbol]["net"] = round(out[symbol]["net"] + pnl, 1)
            if pnl > 0:
                out[symbol]["wins"] += 1
            elif pnl < 0:
                out[symbol]["losses"] += 1
        for value in out.values():
            closed = value["wins"] + value["losses"]
            value["win_rate"] = round(value["wins"] / closed * 100, 1) if closed else 0
        return dict(out)

    def _recommendations(self, total: int, win_rate: float, net: float, profit_factor: float, by_agent: Dict[str, Any], by_direction: Dict[str, Any]) -> List[str]:
        recs: List[str] = []
        if total < 5:
            recs.append("Sample is small; keep paper trading before judging performance.")
        if win_rate < 45 and total >= 5:
            recs.append("Low win rate; raise confidence/quality thresholds or block D/E signals.")
        if net < 0:
            recs.append("Net points negative; review losing setups before adding risk.")
        if profit_factor and profit_factor < 1:
            recs.append("Profit Factor below 1; reduce experimental signals or tighten Risk Grade.")
        if by_agent:
            worst = sorted(by_agent.items(), key=lambda x: x[1].get("net", 0))[0]
            best = sorted(by_agent.items(), key=lambda x: x[1].get("net", 0), reverse=True)[0]
            recs.append(f"Best source so far: {best[0]} | Net {best[1].get('net', 0):+}")
            if worst[1].get("count", 0) >= 2 and worst[1].get("net", 0) < 0:
                recs.append(f"Watch the weak source: {worst[0]} | Net {worst[1].get('net', 0):+}")
        if by_direction.get("BUY", {}).get("net", 0) < 0 and by_direction.get("SELL", {}).get("net", 0) > 0:
            recs.append("SELL outperforms BUY in the current sample; check Daily Bias before buying.")
        return recs[:8]

    def _pnl(self, trade: Dict[str, Any]) -> float:
        for key in ("final_pnl", "current_pnl", "current_pnl_points"):
            value = trade.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def _f(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
