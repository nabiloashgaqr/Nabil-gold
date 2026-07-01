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
    by_instrument: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    halt_activations: int = 0
    caution_activations: int = 0
    news_blocked_signals: int = 0
    duplicate_blocked_signals: int = 0
    time_of_week: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    rr_efficiency: Dict[str, Any] = field(default_factory=dict)
    regime_fit: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    news_proximity: Dict[str, Dict[str, Any]] = field(default_factory=dict)

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
            "by_instrument": self.by_instrument,
            "halt_activations": self.halt_activations,
            "caution_activations": self.caution_activations,
            "news_blocked_signals": self.news_blocked_signals,
            "duplicate_blocked_signals": self.duplicate_blocked_signals,
            "time_of_week": self.time_of_week,
            "rr_efficiency": self.rr_efficiency,
            "regime_fit": self.regime_fit,
            "news_proximity": self.news_proximity,
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
            # SL_HIT can be a loss, breakeven, or SL+ after stop was moved.
            # Use actual PnL sign for performance classification.
            is_loss = pnl < 0
            is_be = abs(pnl) < 0.5 or (status in {"BE_HIT", "EXPIRED"} and abs(pnl) < 0.5)
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

        # ⚖️ Profit Factor: 99.9 / ∞ for no-loss case (consistent with dashboard / daily)
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
            if pnl < 0:
                bucket["losses"] += 1
            elif pnl > 0:
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
            is_win = pnl > 0
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
            if pnl > 0:
                bucket["wins"] += 1
        stats.by_session = {
            s: {**v, "pnl": round(v["pnl"], 2),
                "win_rate_pct": round(v["wins"] / v["count"] * 100.0, 1) if v["count"] else 0.0}
            for s, v in session_buckets.items()
        }

        # ---- Per instrument ------------------------------------------------ #
        instrument_buckets: Dict[str, Dict[str, Any]] = {}
        for trade in closed:
            symbol = str(trade.get("symbol") or "XAU/USD")
            pnl = self._trade_pnl(trade)
            status = str(trade.get("status", "")).upper()
            is_win = pnl > 0
            bucket = instrument_buckets.setdefault(
                symbol, {"count": 0, "wins": 0, "losses": 0, "pnl": 0.0})
            bucket["count"] += 1
            bucket["pnl"] += pnl
            if is_win:
                bucket["wins"] += 1
            else:
                bucket["losses"] += 1
        stats.by_instrument = {
            s: {**v, "pnl": round(v["pnl"], 2),
                "win_rate_pct": round(v["wins"] / v["count"] * 100.0, 1) if v["count"] else 0.0}
            for s, v in instrument_buckets.items()
        }

        # ---- Phase 5 enrichment: timing, R:R efficiency, regime/news fit -- #
        stats.time_of_week = self._time_of_week_breakdown(closed)
        stats.rr_efficiency = self._rr_efficiency(closed)
        stats.regime_fit = self._regime_fit(closed)
        stats.news_proximity = self._news_proximity(closed)

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
            self.save_to_database(result)
            return result

        message = self._fallback_message(stats)
        result = {
            "status": "ok",
            "stats": stats.to_prompt_dict(),
            "report_text": message,
            "recommendations": [],
        }
        self._save(result)
        self.save_to_database(result)
        return result



    def save_to_database(self, result: Dict[str, Any]) -> None:
        """Persist weekly report into Supabase weekly_reports for dashboard use.

        The local JSON artifact is still written by _save(). This method archives
        the report in Supabase. It upserts by (week_start, week_end) using a
        select-then-update flow to avoid requiring a unique constraint.
        """
        stats = result.get("stats") or {}
        week_text = str(stats.get("week") or "")
        if "→" in week_text:
            week_start, week_end = [x.strip() for x in week_text.split("→", 1)]
        else:
            week_start = str(stats.get("week_start") or "")
            week_end = str(stats.get("week_end") or "")
        if not week_start or week_start == "—":
            week_start = datetime.now(self.tz).date().isoformat()
        if not week_end or week_end == "—":
            week_end = datetime.now(self.tz).date().isoformat()

        payload = {
            "week_start": week_start,
            "week_end": week_end,
            "stats_json": stats,
            "report_text": str(result.get("report_text") or ""),
            "recommendations": result.get("recommendations") or [],
            "tokens_used": int(result.get("tokens_used", 0) or 0),
            "cost": float(result.get("cost", 0) or 0),
            "status": str(result.get("status") or "ok"),
        }

        client = getattr(self.database, "client", None)
        if not (getattr(self.database, "use_supabase", False) and client is not None):
            logger.info("Supabase unavailable; weekly report saved only as local JSON artifact.")
            return

        try:
            existing = (
                client.table("weekly_reports")
                .select("id")
                .eq("week_start", week_start)
                .eq("week_end", week_end)
                .limit(1)
                .execute()
            )
            rows = list(existing.data or [])
            if rows:
                client.table("weekly_reports").update(payload).eq("id", rows[0]["id"]).execute()
                logger.info("Saved weekly report to Supabase: updated %s → %s", week_start, week_end)
            else:
                client.table("weekly_reports").insert(payload).execute()
                logger.info("Saved weekly report to Supabase: inserted %s → %s", week_start, week_end)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to save weekly report to Supabase: %s", exc)


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
        gross_profit = stats.avg_win_points * stats.wins if stats.wins else 0
        gross_loss = abs(stats.avg_loss_points * stats.losses) if stats.losses else 0

        # Profit Factor display
        if stats.losses > 0 and gross_loss > 0:
            pf_display = f"{pf:.2f}"
            pf_note = f"Gross +{gross_profit:.0f} / Loss -{gross_loss:.0f}"
        elif gross_profit > 0:
            pf_display = "∞"
            pf_note = "No losses"
        else:
            pf_display = "0.00"
            pf_note = "No trades"

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

        def _best_bucket(items: Dict[str, Dict[str, Any]]) -> tuple[str, Dict[str, Any]] | None:
            return max(items.items(), key=lambda x: x[1].get("pnl", 0)) if items else None

        def _worst_bucket(items: Dict[str, Dict[str, Any]]) -> tuple[str, Dict[str, Any]] | None:
            return min(items.items(), key=lambda x: x[1].get("pnl", 0)) if items else None

        best_instrument = _best_bucket(stats.by_instrument)
        best_agent = _best_bucket(stats.by_agent)
        weak_agent = _worst_bucket(stats.by_agent)
        best_session = _best_bucket(stats.by_session or stats.time_of_week)
        weak_session = _worst_bucket(stats.by_session or stats.time_of_week)
        best_regime = _best_bucket(stats.regime_fit)
        weak_news = _worst_bucket({k: v for k, v in stats.news_proximity.items() if str(k).upper() != "UNKNOWN"})
        rr = stats.rr_efficiency or {}

        def _bucket_line(item: tuple[str, Dict[str, Any]] | None, empty: str = "—") -> str:
            if not item:
                return empty
            label, data = item
            return f"{label} {float(data.get('pnl', 0) or 0):+.0f} pts · WR {float(data.get('win_rate_pct', 0) or 0):.0f}% · {data.get('count', 0)} trades"

        agent_lines = []
        for agent, data in sorted(stats.by_agent.items(), key=lambda x: x[1].get("pnl", 0), reverse=True)[:4]:
            agent_lines.append(f"  • {agent}: {data.get('pnl', 0):+.0f} pts · WR {data.get('win_rate_pct', 0)}% · {data.get('count', 0)} trades")
        agent_section = "\n".join(agent_lines) if agent_lines else "  • No agent data"

        daily_lines = []
        for day, data in sorted(stats.by_day.items(), key=lambda x: x[1].get("pnl", 0), reverse=True)[:5]:
            daily_lines.append(f"  • {day}: {data.get('pnl', 0):+.0f} pts · W {data.get('wins', 0)} / L {data.get('losses', 0)} · {data.get('count', 0)} trades")
        daily_section = "\n".join(daily_lines) if daily_lines else "  • No daily data"

        recommendations = []
        if stats.net_pnl_points < 0:
            recommendations.append("Review entry filters before increasing risk next week.")
        if rr.get("sample") and float(rr.get("rr_capture_pct", 0) or 0) < 45:
            recommendations.append(f"Improve exit management: RR capture is only {rr.get('rr_capture_pct')}%.")
        if weak_session and float(weak_session[1].get("pnl", 0) or 0) < 0:
            recommendations.append(f"Reduce aggression during {weak_session[0]} until it recovers.")
        if weak_news and float(weak_news[1].get("pnl", 0) or 0) < 0:
            recommendations.append(f"Tighten news filter around {weak_news[0]} conditions.")
        if not recommendations:
            recommendations.append("Keep current risk profile; monitor for regime changes.")

        separator = "────────────────────"
        lines = [
            "📊 SmartSignal — Weekly Executive Report",
            f"Week: {stats.week_start} → {stats.week_end}",
            separator,
            "📌 EXECUTIVE SUMMARY",
            f"• Net: {stats.net_pnl_points:+.0f} pts (${stats.net_pnl_points / 10:+.1f}) · WR {stats.win_rate:.1f}% · PF {pf_display}",
            f"• Total trades: {stats.total_trades} · {stats.closed_trades} closed · {stats.open_trades} open",
            f"• Best/Worst: {stats.largest_win_points:+.0f} / {stats.largest_loss_points:+.0f} pts · Expectancy {expectancy:+.1f} pts",
            f"• Grade: {grade} — {verdict}",
            separator,
            "🧩 EDGE QUALITY",
            f"• RR Capture: {float(rr.get('avg_actual_r', 0) or 0):+.2f}R vs planned {float(rr.get('avg_planned_rr', 0) or 0):.2f}R ({float(rr.get('rr_capture_pct', 0) or 0):.1f}%)" if rr.get("sample") else "• RR Capture: not enough enriched trades",
            f"• Best session: {_bucket_line(best_session)}",
            f"• Weak session: {_bucket_line(weak_session)}",
            f"• Best regime: {_bucket_line(best_regime)}",
            f"• News impact: {_bucket_line(weak_news)}",
            separator,
            "🤖 AGENT SNAPSHOT",
            f"• Best: {_bucket_line(best_agent)}",
            f"• Weakest: {_bucket_line(weak_agent)}",
            agent_section,
            separator,
            "📅 BEST/WORST DAYS",
            f"• Best day: {stats.best_day} {stats.best_day_pnl:+.0f} pts" if stats.best_day != "—" else "• Best day: —",
            f"• Worst day: {stats.worst_day} {stats.worst_day_pnl:+.0f} pts" if stats.worst_day != "—" else "• Worst day: —",
            daily_section,
            separator,
            "🎯 NEXT WEEK ACTIONS",
            *[f"• {rec}" for rec in recommendations[:4]],
            separator,
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
        # Use the actual open/entry time first so daily/session reports match when the trade was opened,
        # not when it was eventually closed.
        for key in ("entry_time", "opened_at", "created_at", "updated_at"):
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
        """Extract agent names from trade data or signal_snapshot."""
        # Direct fields
        agents = trade.get("agents") or trade.get("signals_agents") or []
        if isinstance(agents, str):
            return [a.strip() for a in agents.split(",") if a.strip()]
        if isinstance(agents, list) and agents:
            return [str(a) for a in agents if a]

        # Extract from signal_snapshot (where decision data is stored)
        snapshot = trade.get("signal_snapshot") or {}
        agent_context = snapshot.get("agent_context") or {}
        if agent_context.get("agent"):
            return [str(agent_context["agent"])]

        # Try to find from votes
        votes = snapshot.get("votes") or {}
        agent_names = []
        for side in ("BUY", "SELL"):
            for vote in votes.get(side, []) or []:
                name = str(vote.get("agent", "")).strip()
                if name and name not in agent_names:
                    agent_names.append(name)
        if agent_names:
            return agent_names

        return ["consensus"]

    @staticmethod
    def _trade_session(trade: Dict[str, Any]) -> str:
        """Return an ordered, human-friendly trading session by open time.

        Labels are based on Asia/Jerusalem local time so the weekly Telegram
        report matches the dashboard:
        - Asia Morning
        - London / Europe Midday
        - London + New York Afternoon
        - New York Evening
        - Late New York Night
        """
        ts = WeeklyReportService._trade_time_text(trade)
        try:
            text = str(ts).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local = dt.astimezone(ZoneInfo("Asia/Jerusalem"))
            h = local.hour
        except Exception:  # noqa: BLE001
            # Fallback to stored session label if timestamp parsing fails.
            snapshot = trade.get("signal_snapshot") or {}
            session_info = snapshot.get("session_info") or {}
            raw = str(trade.get("session") or trade.get("current_session") or session_info.get("current_session") or "unknown")
            raw_l = raw.lower()
            if "asian" in raw_l:
                return "Asia Morning"
            if "london-ny" in raw_l or "overlap" in raw_l:
                return "London + New York Afternoon"
            if "london" in raw_l:
                return "London / Europe Midday"
            if "new york" in raw_l or "ny" in raw_l:
                return "New York Evening"
            return "Late New York Night"

        if 3 <= h < 10:
            return "Asia Morning"
        if 10 <= h < 15:
            return "London / Europe Midday"
        if 15 <= h < 19:
            return "London + New York Afternoon"
        if 19 <= h < 24:
            return "New York Evening"
        return "Late New York Night"

    @staticmethod
    def _snapshot(trade: Dict[str, Any]) -> Dict[str, Any]:
        snap = trade.get("signal_snapshot") or {}
        return snap if isinstance(snap, dict) else {}

    @staticmethod
    def _planned_rr(trade: Dict[str, Any]) -> float:
        for key in ("planned_rr", "rr_ratio"):
            try:
                value = trade.get(key)
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                pass
        snap = WeeklyReportService._snapshot(trade)
        sig = snap.get("signal") or {}
        for key in ("rr_ratio", "tp2_rr"):
            try:
                value = sig.get(key)
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                pass
        return 0.0

    @staticmethod
    def _planned_risk_points(trade: Dict[str, Any]) -> float:
        try:
            value = trade.get("planned_risk_points")
            if value is not None:
                return abs(float(value))
        except (TypeError, ValueError):
            pass
        try:
            entry = float(trade.get("entry_price") or 0)
            sl = float(trade.get("initial_stop_loss") or trade.get("stop_loss") or 0)
            if entry and sl:
                # Stored points are broker points. For the weekly enrichment a
                # fallback price delta is enough to calculate actual/planned R.
                return abs(entry - sl) * 10.0
        except (TypeError, ValueError):
            pass
        return 0.0

    @staticmethod
    def _trade_local_dt(trade: Dict[str, Any]) -> datetime | None:
        ts = WeeklyReportService._trade_time_text(trade)
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(ZoneInfo("Asia/Jerusalem"))
        except Exception:  # noqa: BLE001
            return None

    def _time_of_week_breakdown(self, trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        buckets: Dict[str, Dict[str, Any]] = {}
        for trade in trades:
            dt = self._trade_local_dt(trade)
            label = str(trade.get("entry_day_of_week") or (dt.strftime("%A") if dt else "unknown"))
            pnl = self._trade_pnl(trade)
            bucket = buckets.setdefault(label, {"count": 0, "pnl": 0.0, "wins": 0})
            bucket["count"] += 1
            bucket["pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
        return {k: {**v, "pnl": round(v["pnl"], 1), "win_rate_pct": round(v["wins"] / v["count"] * 100, 1) if v["count"] else 0.0} for k, v in buckets.items()}

    def _rr_efficiency(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        actual_r: List[float] = []
        planned: List[float] = []
        for trade in trades:
            risk = self._planned_risk_points(trade)
            if risk <= 0:
                continue
            actual = self._trade_pnl(trade) / risk
            actual_r.append(actual)
            rr = self._planned_rr(trade)
            if rr > 0:
                planned.append(rr)
        if not actual_r:
            return {"sample": 0}
        wins = [x for x in actual_r if x > 0]
        return {
            "sample": len(actual_r),
            "avg_actual_r": round(sum(actual_r) / len(actual_r), 2),
            "avg_winner_r": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_planned_rr": round(sum(planned) / len(planned), 2) if planned else 0.0,
            "rr_capture_pct": round((sum(actual_r) / len(actual_r)) / (sum(planned) / len(planned)) * 100, 1) if planned and sum(planned) else 0.0,
        }

    def _regime_fit(self, trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        buckets: Dict[str, Dict[str, Any]] = {}
        for trade in trades:
            snap = self._snapshot(trade)
            mc = snap.get("market_context") or {}
            regime = str(trade.get("volatility_regime") or (mc.get("technical_regime") or {}).get("volatility_regime") or "unknown").upper()
            pnl = self._trade_pnl(trade)
            bucket = buckets.setdefault(regime, {"count": 0, "pnl": 0.0, "wins": 0})
            bucket["count"] += 1
            bucket["pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
        return {k: {**v, "pnl": round(v["pnl"], 1), "win_rate_pct": round(v["wins"] / v["count"] * 100, 1) if v["count"] else 0.0} for k, v in buckets.items()}

    def _news_proximity(self, trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        buckets: Dict[str, Dict[str, Any]] = {}
        for trade in trades:
            snap = self._snapshot(trade)
            nc = snap.get("news_context") or {}
            rule = nc.get("rule_based") or {}
            status = str(trade.get("news_status_at_entry") or rule.get("market_status") or rule.get("status") or "unknown").upper()
            pnl = self._trade_pnl(trade)
            bucket = buckets.setdefault(status, {"count": 0, "pnl": 0.0, "wins": 0})
            bucket["count"] += 1
            bucket["pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
        return {k: {**v, "pnl": round(v["pnl"], 1), "win_rate_pct": round(v["wins"] / v["count"] * 100, 1) if v["count"] else 0.0} for k, v in buckets.items()}

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
