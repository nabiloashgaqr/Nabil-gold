"""Weekly performance report service.

Generates a deterministic structured weekly report from real trade statistics and
sends it to Telegram. No external decision service is used.
"""
from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Telegram hard limit (https://core.telegram.org/bots/api#sendmessage)
TELEGRAM_MAX_CHARS = 4096

# Statuses considered as a closed (resolved) trade
CLOSED_STATUSES = {"CLOSED_TP1", "CLOSED_TP2", "CLOSED_SL", "EXPIRED", "BE_HIT",
                   "TP2_HIT", "SL_HIT", "MANUAL_CLOSE"}


@dataclass
class WeeklyStats:
    """Aggregated weekly numbers used by the weekly report."""
    lookback_days: int = 7
    week_start: str = ""
    week_end: str = ""
    total_trades: int = 0
    closed_trades: int = 0
    open_trades: int = 0
    wins: int = 0
    losses: int = 0
    break_even: int = 0
    win_rate: float = 0.0
    net_pnl_points: float = 0.0
    avg_win_points: float = 0.0
    avg_loss_points: float = 0.0
    largest_win_points: float = 0.0
    largest_loss_points: float = 0.0
    profit_factor: float = 99.9
    best_day: str = "—"
    best_day_pnl: float = 0.0
    worst_day: str = "—"
    worst_day_pnl: float = 0.0
    by_day: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_agent: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_session: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    halt_activations: int = 0
    caution_activations: int = 0
    news_blocked_signals: int = 0
    duplicate_blocked_signals: int = 0

    def to_prompt_dict(self) -> Dict[str, Any]:
        pf_display = "∞" if self.profit_factor >= 99 else round(self.profit_factor, 2)
        return {
            "week": f"{self.week_start} → {self.week_end}",
            "lookback_days": self.lookback_days,
            "total_trades": self.total_trades,
            "closed_trades": self.closed_trades,
            "open_trades": self.open_trades,
            "wins": self.wins,
            "losses": self.losses,
            "break_even": self.break_even,
            "win_rate_pct": round(self.win_rate, 1),
            "net_pnl_points": round(self.net_pnl_points, 1),
            "profit_factor": pf_display,
            "avg_win_points": round(self.avg_win_points, 2),
            "avg_loss_points": round(self.avg_loss_points, 2),
            "largest_win_points": round(self.largest_win_points, 2),
            "largest_loss_points": round(self.largest_loss_points, 2),
            "best_day": self.best_day,
            "best_day_pnl": round(self.best_day_pnl, 2),
            "worst_day": self.worst_day,
            "worst_day_pnl": round(self.worst_day_pnl, 2),
            "by_day": self.by_day,
            "by_agent": self.by_agent,
            "by_session": self.by_session,
            "halt_activations": self.halt_activations,
            "caution_activations": self.caution_activations,
            "news_blocked_signals": self.news_blocked_signals,
            "duplicate_blocked_signals": self.duplicate_blocked_signals,
        }


class WeeklyReportService:
    """Build and send the weekly performance report."""

    def __init__(self, config: Dict[str, Any], database: Any, telegram: Any = None, **_kwargs) -> None:
        self.config = config
        self.database = database
        self.telegram = telegram
        wr_cfg = (config.get("weekly_report") or {})
        self.enabled = bool(wr_cfg.get("enabled", False))
        self.lookback_days = int(wr_cfg.get("lookback_days", 7) or 7)
        # Handle explicit 0 vs missing key correctly (don't fall back to 5).
        _min_trades_raw = wr_cfg.get("min_trades_for_report")
        self.min_trades = int(_min_trades_raw) if _min_trades_raw is not None else 5
        self.max_chars = int(wr_cfg.get("max_chars", 3500) or 3500)
        self.send_telegram = bool(wr_cfg.get("send_telegram", True))
        self.storage_path = Path(wr_cfg.get("storage_path", "storage/weekly_report.json"))
        self.tz_name = str(wr_cfg.get("timezone") or config.get("schedule", {}).get("timezone") or "Asia/Hebron")
        try:
            self.tz = ZoneInfo(self.tz_name)
        except Exception:  # noqa: BLE001
            self.tz = timezone.utc

    # ------------------------------------------------------------------ #
    # 1) Data collection
    # ------------------------------------------------------------------ #
    def collect_stats(self, *, now: Optional[datetime] = None) -> WeeklyStats:
        """Collect and aggregate last N days of data."""
        now = now or datetime.now(self.tz)
        week_start = (now - timedelta(days=self.lookback_days)).date()
        week_end = now.date()
        start_iso = datetime.combine(week_start, datetime.min.time()).astimezone(self.tz).isoformat()

        stats = WeeklyStats(lookback_days=self.lookback_days,
                            week_start=week_start.isoformat(),
                            week_end=week_end.isoformat())

        # ---- Trades ---------------------------------------------------- #
        all_trades = self._fetch_trades_since(start_iso)
        closed = []
        open_trades: List[Dict[str, Any]] = []
        for trade in all_trades:
            status = str(trade.get("status", "")).upper()
            if status in CLOSED_STATUSES:
                closed.append(trade)
            elif status in {"OPEN", "TP1_HIT"}:
                open_trades.append(trade)

        stats.total_trades = len(all_trades)
        stats.closed_trades = len(closed)
        stats.open_trades = len(open_trades)

        wins, losses, be = [], [], []
        pnl_total = 0.0
        largest_win = 0.0
        largest_loss = 0.0
        for trade in closed:
            pnl = self._trade_pnl(trade)
            pnl_total += pnl
            status = str(trade.get("status", "")).upper()
            is_loss = status == "SL_HIT" or pnl < 0
            is_be = status in {"BE_HIT", "EXPIRED"} and abs(pnl) < 0.5
            if is_loss:
                losses.append(pnl)
                largest_loss = min(largest_loss, pnl)
            elif is_be:
                be.append(pnl)
            else:
                wins.append(pnl)
                largest_win = max(largest_win, pnl)

        stats.wins = len(wins)
        stats.losses = len(losses)
        stats.break_even = len(be)
        stats.net_pnl_points = pnl_total
        stats.avg_win_points = (sum(wins) / len(wins)) if wins else 0.0
        stats.avg_loss_points = (sum(losses) / len(losses)) if losses else 0.0
        stats.largest_win_points = largest_win
        stats.largest_loss_points = largest_loss
        total_resolved = stats.wins + stats.losses + stats.break_even
        stats.win_rate = (stats.wins / total_resolved * 100.0) if total_resolved else 0.0

        # Profit Factor: 99.9 / ∞ for no-loss case (consistent with dashboard / daily)
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        if gross_loss > 0:
            stats.profit_factor = round(gross_profit / gross_loss, 2)
        else:
            stats.profit_factor = 99.9 if gross_profit > 0 else 0.0

        # ---- Per day ---------------------------------------------------- #
        day_buckets: Dict[str, Dict[str, Any]] = {}
        for trade in closed:
            day = self._trade_day(trade)
            bucket = day_buckets.setdefault(day, {"pnl": 0.0, "count": 0, "wins": 0, "losses": 0})
            pnl = self._trade_pnl(trade)
            bucket["pnl"] += pnl
            bucket["count"] += 1
            status = str(trade.get("status", "")).upper()
            if status == "SL_HIT" or pnl < 0:
                bucket["losses"] += 1
            elif status not in {"BE_HIT", "EXPIRED"} or pnl >= 0:
                bucket["wins"] += 1
        stats.by_day = {d: {**v, "pnl": round(v["pnl"], 2)} for d, v in day_buckets.items()}
        if day_buckets:
            best = max(day_buckets.items(), key=lambda kv: kv[1]["pnl"])
            worst = min(day_buckets.items(), key=lambda kv: kv[1]["pnl"])
            stats.best_day, stats.best_day_pnl = best[0], best[1]["pnl"]
            stats.worst_day, stats.worst_day_pnl = worst[0], worst[1]["pnl"]

        # ---- Per agent -------------------------------------------------- #
        agent_buckets: Dict[str, Dict[str, Any]] = {}
        for trade in closed:
            agents = self._trade_agents(trade)
            pnl = self._trade_pnl(trade)
            status = str(trade.get("status", "")).upper()
            is_win = status not in {"SL_HIT"} and pnl >= 0
            for agent in agents:
                bucket = agent_buckets.setdefault(
                    agent, {"count": 0, "wins": 0, "losses": 0, "pnl": 0.0})
                bucket["count"] += 1
                bucket["pnl"] += pnl
                if is_win:
                    bucket["wins"] += 1
                else:
                    bucket["losses"] += 1
        stats.by_agent = {
            a: {**v, "pnl": round(v["pnl"], 2),
                "win_rate_pct": round(v["wins"] / v["count"] * 100.0, 1) if v["count"] else 0.0}
            for a, v in agent_buckets.items()
        }

        # ---- Per session ------------------------------------------------ #
        session_buckets: Dict[str, Dict[str, Any]] = {}
        for trade in closed:
            session = self._trade_session(trade)
            pnl = self._trade_pnl(trade)
            bucket = session_buckets.setdefault(session, {"count": 0, "pnl": 0.0, "wins": 0})
            bucket["count"] += 1
            bucket["pnl"] += pnl
            status = str(trade.get("status", "")).upper()
            if status not in {"SL_HIT"} and pnl >= 0:
                bucket["wins"] += 1
        stats.by_session = {
            s: {**v, "pnl": round(v["pnl"], 2),
                "win_rate_pct": round(v["wins"] / v["count"] * 100.0, 1) if v["count"] else 0.0}
            for s, v in session_buckets.items()
        }

        # ---- Risk / memory / news counters (best-effort) ---------------- #
        stats.halt_activations = self._safe_count("session_log",
                                                   {"event": "HALT_ACTIVATED", "since": start_iso})
        stats.caution_activations = self._safe_count("session_log",
                                                      {"event": "CAUTION_ACTIVATED", "since": start_iso})
        stats.news_blocked_signals = self._safe_count("signals",
                                                       {"blocked_by": "news", "since": start_iso})
        stats.duplicate_blocked_signals = self._safe_count("signals",
                                                             {"blocked_by": "duplicate", "since": start_iso})
        return stats

    # ------------------------------------------------------------------ #
    # 2) Prompt
    # ------------------------------------------------------------------ #
    def build_prompt(self, stats: WeeklyStats) -> str:
        data = json.dumps(stats.to_prompt_dict(), ensure_ascii=False, indent=2)
        return (
            "You are the weekly performance analyst for a SmartSignal system "
            "(XAU/USD, Paper Trading).\n\n"
            "📊 Real data for the past week (do NOT invent numbers, use only this data):\n"
            f"```json\n{data}\n```\n\n"
            "✍️ Write a concise report in English with the following sections, in order:\n"
            "1) 📈 Performance summary (total, wins, net, largest win/loss, Profit Factor)\n"
            "2) 🤖 Agent performance (per agent: name + win rate + pnl + weight recommendation)\n"
            "3) 📅 Best and worst day (day + pnl + trade count)\n"
            "4) 🌍 Session performance (London / NY / Asian, best/worst)\n"
            "5) ⚠️ Risk (HALT/CAUTION count)\n"
            "7) 🎯 3-5 specific, actionable recommendations for config.json next week\n\n"
            f"⚠️ Strict rules:\n"
            f"- Do not exceed {self.max_chars} characters\n"
            "- Start each section with an emoji\n"
            "- Recommendations must be immediately actionable "
            "(e.g. 'lower classical_agent weight from 0.20 to 0.15')\n"
            "- Write in clear, plain English only\n"
            "- Start with a line '═══════════════════════════════════' and end with the same line\n"
        )

    # ------------------------------------------------------------------ #
    # 3) Generate deterministic report
    # ------------------------------------------------------------------ #
    async def generate_report(self, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        stats = self.collect_stats(now=now)
        # Gracefully handle weeks with too few trades
        if stats.total_trades < self.min_trades:
            message = (
                "═══════════════════════════════════\n"
                "📊 SmartSignal — Weekly Report\n"
                f"Week: {stats.week_start} → {stats.week_end}\n"
                "═══════════════════════════════════\n\n"
                f"⚪ Quiet week: only {stats.total_trades} trades total "
                f"(minimum {self.min_trades}).\n"
                "No recommendations this week.\n"
            )
            result = {
                "status": "ok_too_few_trades",
                "stats": stats.to_prompt_dict(),
                "report_text": message,
                "recommendations": [],
            }
            self._save(result)
            return result

        message = self._fallback_message(stats)
        result = {
            "status": "ok",
            "stats": stats.to_prompt_dict(),
            "report_text": message,
            "recommendations": [],
        }
        self._save(result)
        return result


    # ------------------------------------------------------------------ #
    # 4) Send to Telegram (split if needed)
    # ------------------------------------------------------------------ #
    def send_to_telegram(self, report_text: str) -> bool:
        if not self.telegram or not self.send_telegram:
            logger.info("Telegram disabled or unavailable; skipping send.")
            return False
        # Escape text before sending with Telegram HTML parse mode.
        safe_report_text = html.escape(str(report_text), quote=False)
        chunks = self.split_message(safe_report_text)
        all_ok = True
        for idx, chunk in enumerate(chunks, 1):
            if len(chunks) > 1:
                prefix = f"📄 (Part {idx}/{len(chunks)})\n\n"
                chunk = prefix + chunk
            ok = self.telegram.send_message(chunk, urgent=False)
            all_ok = all_ok and ok
        return all_ok

    @staticmethod
    def split_message(text: str, max_chars: int = TELEGRAM_MAX_CHARS) -> List[str]:
        """Split text into chunks <= max_chars, breaking at line boundaries."""
        if len(text) <= max_chars:
            return [text]
        chunks: List[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= max_chars:
                chunks.append(remaining)
                break
            # Try to break at the last newline before the limit
            cut = remaining.rfind("\n", 0, max_chars)
            if cut == -1 or cut < max_chars // 2:
                cut = max_chars
            chunks.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip("\n")
        return chunks

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _fallback_message(self, stats: WeeklyStats) -> str:
        """Professional weekly report with full details."""
        pf = stats.profit_factor
        pf_display = "∞" if pf >= 99 or (pf in (0, 99.9) and stats.losses == 0 and stats.wins > 0) else pf

        # Profit Factor calculation explanation
        if stats.losses > 0:
            pf_note = f"Gross Profit: +{stats.avg_win_points * stats.wins:.0f} / Gross Loss: {abs(stats.avg_loss_points * stats.losses):.0f}"
        else:
            pf_note = "No losses this week"

        # Expectancy
        wr = stats.win_rate / 100
        expectancy = (wr * stats.avg_win_points) - ((1 - wr) * abs(stats.avg_loss_points)) if stats.avg_loss_points != 0 else wr * stats.avg_win_points

        # Risk grade
        score = 0
        if stats.win_rate >= 60: score += 2
        elif stats.win_rate >= 50: score += 1
        if pf >= 2.0: score += 2
        elif pf >= 1.5: score += 1
        if stats.net_pnl_points > 0: score += 1
        grades = {5: "A+", 4: "A", 3: "B", 2: "C", 1: "D", 0: "F"}
        grade = grades.get(score, "F")

        # System verdict
        if expectancy > 0 and stats.win_rate >= 55:
            verdict = "✅ System is profitable"
        elif stats.net_pnl_points > 0:
            verdict = "⚠️ Profitable but needs monitoring"
        else:
            verdict = "❌ System needs review"

        # Per-agent breakdown
        agent_lines = []
        for agent, data in sorted(stats.by_agent.items(), key=lambda x: x[1].get("pnl", 0), reverse=True):
            emoji = "🟢" if data.get("pnl", 0) > 0 else "🔴" if data.get("pnl", 0) < 0 else "⚪"
            agent_lines.append(
                f"  {emoji} {agent}: {data.get('count', 0)} trades | "
                f"WR {data.get('win_rate_pct', 0)}% | "
                f"Net {data.get('pnl', 0):+.0f} pts"
            )
        agent_section = "\n".join(agent_lines) if agent_lines else "  • No agent data"

        # Per-session breakdown
        session_lines = []
        for session, data in sorted(stats.by_session.items(), key=lambda x: x[1].get("pnl", 0), reverse=True):
            emoji = "🟢" if data.get("pnl", 0) > 0 else "🔴" if data.get("pnl", 0) < 0 else "⚪"
            session_lines.append(
                f"  {emoji} {session}: {data.get('count', 0)} trades | "
                f"WR {data.get('win_rate_pct', 0)}% | "
                f"Net {data.get('pnl', 0):+.0f} pts"
            )
        session_section = "\n".join(session_lines) if session_lines else "  • No session data"

        # Best/Worst day
        best_day_line = f"Best: {stats.best_day} ({stats.best_day_pnl:+.0f} pts)" if stats.best_day != "—" else "Best: —"
        worst_day_line = f"Worst: {stats.worst_day} ({stats.worst_day_pnl:+.0f} pts)" if stats.worst_day != "—" else "Worst: —"

        lines = [
            "═══════════════════════════════════",
            "📊 SmartSignal — Weekly Report",
            f"Week: {stats.week_start} → {stats.week_end}",
            "═══════════════════════════════════",
            "",
            "📈 <b>Summary</b>",
            f"• Total trades: {stats.total_trades}",
            f"• Wins: {stats.wins}  |  Losses: {stats.losses}  |  BE: {stats.break_even}  |  Open: {stats.open_trades}",
            f"• Win Rate: {stats.win_rate:.1f}%",
            "",
            "💰 <b>Performance</b>",
            f"• Net: {stats.net_pnl_points:+.1f} pts (${stats.net_pnl_points / 10:+.1f})",
            f"• Profit Factor: {pf_display}  ({pf_note})",
            f"• Avg Win: +{stats.avg_win_points:.1f}  |  Avg Loss: {stats.avg_loss_points:.1f}",
            f"• Best Trade: {stats.largest_win_points:+.1f}  |  Worst: {stats.largest_loss_points:+.1f}",
            f"• Expectancy: {expectancy:+.1f} pts/trade",
            "",
            "🤖 <b>Agent Performance</b>",
            agent_section,
            "",
            "📅 <b>Daily Breakdown</b>",
            f"• {best_day_line}",
            f"• {worst_day_line}",
            "",
            "🌍 <b>Session Performance</b>",
            session_section,
            "",
            "🛡️ <b>Risk Grade</b>",
            f"• Grade: {grade}",
            f"• {verdict}",
            "",
            "⚠️ Paper trading only — not financial advice.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _extract_recommendations(text: str) -> List[str]:
        """Pull numbered recommendations out of the report (best-effort).

        Supports patterns: "1)", "1.", "1-", "-", "•"
        """
        lines = text.splitlines()
        recs: List[str] = []
        in_recs = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if any(kw in stripped for kw in ("التوصيات", "توصيات")) or "recommendation" in stripped.lower():
                in_recs = True
                continue
            if not in_recs:
                continue
            # Patterns: "1)", "1.", "1-", "1:", "- ", "• "
            first_char = stripped[0]
            if first_char in {"-", "•", "‣", "◦"}:
                recs.append(stripped)
                continue
            if first_char.isdigit():
                # Look at first 3 chars for digit + separator
                head = stripped[:3]
                if any(sep in head for sep in (")", ".", "-", ":", " ")):
                    recs.append(stripped)
        return recs[:10]

    def _save(self, payload: Dict[str, Any]) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload_to_save = dict(payload)
            payload_to_save["saved_at"] = datetime.now(self.tz).isoformat()
            with self.storage_path.open("w", encoding="utf-8") as fh:
                json.dump(payload_to_save, fh, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to save weekly report JSON: %s", exc)

    # ---------------- helpers for trade extraction ---------------- #
    def _fetch_trades_since(self, start_iso: str) -> List[Dict[str, Any]]:
        """Fetch all trades created on/after start_iso using public DB methods only."""
        try:
            recent = self.database.get_recent_trades(limit=500) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_recent_trades failed: %s", exc)
            return []
        return [t for t in recent if self._trade_time_text(t) >= start_iso]

    @staticmethod
    def _trade_time_text(trade: Dict[str, Any]) -> str:
        for key in ("created_at", "opened_at", "entry_time", "updated_at"):
            value = trade.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _trade_pnl(trade: Dict[str, Any]) -> float:
        for key in ("final_pnl", "current_pnl_points", "current_pnl", "pnl_points", "pnl"):
            value = trade.get(key)
            try:
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _trade_day(trade: Dict[str, Any]) -> str:
        ts = WeeklyReportService._trade_time_text(trade)
        if not ts:
            return "unknown"
        return ts[:10]

    @staticmethod
    def _trade_agents(trade: Dict[str, Any]) -> List[str]:
        agents = trade.get("agents") or trade.get("signals_agents") or []
        if isinstance(agents, str):
            return [a.strip() for a in agents.split(",") if a.strip()]
        if isinstance(agents, list):
            return [str(a) for a in agents if a]
        return ["unknown"]

    @staticmethod
    def _trade_session(trade: Dict[str, Any]) -> str:
        return str(trade.get("session") or trade.get("current_session") or "unknown")

    def _safe_count(self, table: str, filters: Dict[str, Any]) -> int:
        """Best-effort count; never raises."""
        try:
            since = filters.get("since")
            event = filters.get("event")
            blocked_by = filters.get("blocked_by")
            client = getattr(self.database, "client", None)
            use_sb = getattr(self.database, "use_supabase", False)
            if not (use_sb and client):
                return 0
            q = client.table(table).select("id")
            if since:
                q = q.gte("created_at", since)
            if event:
                q = q.eq("event", event)
            if blocked_by:
                q = q.eq("blocked_by", blocked_by)
            response = q.execute()
            return len(response.data or [])
        except Exception:  # noqa: BLE001
            return 0
