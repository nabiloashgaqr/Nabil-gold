"""سكريبت التقرير اليومي v2.0.

يعمل يومياً عبر GitHub Actions الساعة 23:00 UTC:
1. إرسال تقرير أداء اليوم
2. إرسال تقرير الصفقات المفتوحة

ملاحظة: لا يعتمد هذا السكريبت على SQL raw حتى يعمل مع DatabaseService الحالي
سواءً كان التخزين Supabase أو JSON fallback.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.daily_report_agent import DailyReportAgent
from services.database import DatabaseService
from services.llm_review import get_gemini_review_service
from services.telegram_bot import TelegramService
from utils.helpers import calculate_pips, load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def _report_timezone(config: dict) -> str:
    schedule = config.get("schedule", {}) or {}
    trading_hours = config.get("trading_hours", {}) or {}
    return str(schedule.get("timezone") or trading_hours.get("timezone") or "Asia/Hebron")


def _resolve_report_date(value: str | None, timezone_name: str) -> str:
    """Resolve REPORT_DATE input. Supports YYYY-MM-DD, today, yesterday."""
    now_local = datetime.now(ZoneInfo(timezone_name))
    text = str(value or "").strip().lower()
    if not text or text == "today":
        return now_local.date().isoformat()
    if text in {"yesterday", "yday", "prev", "previous"}:
        return (now_local.date() - timedelta(days=1)).isoformat()
    # Validate ISO date early so GitHub logs show a clear error.
    datetime.strptime(text, "%Y-%m-%d")
    return text


def _eod_dir():
    return Path(__file__).resolve().parents[1] / "storage"


def _read_eod_section(name: str) -> str:
    """Read a section written by a quiet sub-script (learning/review), or ''."""
    try:
        path = _eod_dir() / f"eod_{name}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read EOD section %s: %s", name, exc)
    return ""


def _cleanup_eod_sections() -> None:
    """Remove EOD handoff files after merging so they don't leak to next day."""
    for name in ("learning", "review"):
        try:
            path = _eod_dir() / f"eod_{name}.txt"
            if path.exists():
                path.unlink()
        except Exception:  # noqa: BLE001
            pass


def _compact_section(text: str, max_lines: int = 8, skip_first_title: bool = True) -> str:
    """Strip decorative divider lines and cap the number of lines so a merged
    section stays short inside the single consolidated message.

    ``skip_first_title`` drops the section's own leading title line (e.g.
    "📊 Learning Update") because the daily
    report already prints its own header above it — avoids a doubled heading.
    """
    out = []
    title_skipped = not skip_first_title
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        # Drop pure divider lines.
        if set(line) <= set("━─=-_ "):
            continue
        # Drop the first non-divider line if it is the section's own title.
        if not title_skipped:
            title_skipped = True
            continue
        out.append(line)
        if len(out) >= max_lines:
            out.append("…")
            break
    return "\n".join(out)


def _trade_value(trade: dict, *keys: str, default=None):
    """Return the first existing/non-empty value from possible schema aliases."""
    for key in keys:
        value = trade.get(key)
        if value is not None and value != "":
            return value
    return default


def save_daily_report_to_database(
    db: DatabaseService,
    *,
    report_date: str,
    stats: dict,
    report_text: str,
    closed_trades_count: int,
) -> None:
    """Persist the daily report into Supabase so the dashboard can read it."""
    recommendations = stats.get("recommendations") or []
    payload = {
        "report_date": report_date,
        "total_signals": int(stats.get("total", 0) or 0),
        "new_trades": int(stats.get("total", 0) or 0),
        "closed_trades": int(closed_trades_count or 0),
        "winning_trades": int(stats.get("wins", 0) or 0),
        "losing_trades": int(stats.get("losses", 0) or 0),
        "daily_pnl": float(stats.get("net_points", 0) or 0),
        "win_rate": float(stats.get("win_rate", 0) or 0),
        "market_summary": "Generated from SmartSignal closed/open trades.",
        "technical_summary": f"PF={stats.get('profit_factor', 0)} | Best={stats.get('best_trade', 0)} | Worst={stats.get('worst_trade', 0)}",
        "recommendations": "\n".join(str(x) for x in recommendations[:8]),
        "report_text": report_text,
        "stats_json": stats,
        "recommendations_json": recommendations,
        "status": "ok",
    }

    client = getattr(db, "client", None)
    if getattr(db, "use_supabase", False) and client is not None:
        try:
            existing = client.table("daily_reports").select("id").eq("report_date", report_date).limit(1).execute()
            rows = list(existing.data or [])
            if rows:
                client.table("daily_reports").update(payload).eq("id", rows[0]["id"]).execute()
            else:
                client.table("daily_reports").insert(payload).execute()
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to save daily report to Supabase: %s", exc)

    # Local/debug fallback.
    try:
        storage = Path(__file__).resolve().parents[1] / "storage"
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "daily_report.json").write_text(
            json.dumps({**payload, "saved_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to save local daily report JSON: %s", exc)


def main() -> None:
    """Generate and send a SINGLE consolidated daily summary report."""
    logger.info("بدء التقرير اليومي المدمج: %s", datetime.now(timezone.utc).isoformat())

    config = load_config()
    telegram = TelegramService(config)
    database = DatabaseService(config)
    timezone_name = _report_timezone(config)
    report_date = _resolve_report_date(os.environ.get("REPORT_DATE"), timezone_name)

    try:
        # 1. Get trades related to this local date (created OR closed)
        today_trades = database.get_trades_for_date(report_date, timezone_name)
        
        closed_today = []
        open_at_that_time = []
        
        for t in today_trades:
            status = str(t.get("status", "")).upper()
            created_at = str(t.get("created_at") or t.get("entry_time") or t.get("opened_at") or "")
            closed_at = str(t.get("closed_at") or t.get("close_time") or "")
            
            is_resolved = status not in {"OPEN", "TP1_HIT", "PARTIAL", "PENDING"}
            
            # For testing and historical logic
            is_test_today = (not created_at and not closed_at)
            
            is_closed_today = False
            if closed_at.startswith(report_date):
                is_closed_today = True
            elif is_resolved and (not closed_at or is_test_today) and (not created_at or created_at.startswith(report_date)):
                is_closed_today = True

            if is_closed_today and status != "CANCELLED":
                closed_today.append(t)
            else:
                is_open_status = status in {"OPEN", "TP1_HIT", "PARTIAL", "PENDING"}
                created_match = not created_at or created_at.startswith(report_date) or (created_at < report_date and created_at != "")
                not_closed_match = not closed_at or closed_at > report_date or (closed_at.startswith(report_date) and is_open_status)
                
                if created_match and not_closed_match and status != "CANCELLED":
                    open_at_that_time.append(t)

        agent = DailyReportAgent(config)
        perf_report = agent.generate(closed_today)
        stats = perf_report.get("stats", {})
        
        open_trades = open_at_that_time
        stats["open"] = len(open_trades)
        
        def _pts(trade) -> float:
            status = str(trade.get("status", "")).upper()
            is_closed = status not in {"OPEN", "TP1_HIT", "PARTIAL", "PENDING"}
            keys = (("final_pnl", "final_pnl_points", "current_pnl", "current_pnl_points")
                    if is_closed else ("current_pnl", "current_pnl_points", "final_pnl", "final_pnl_points"))
            for key in keys:
                v = trade.get(key)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
            typ = str(trade.get("type") or trade.get("trade_type") or "BUY").upper()
            entry = float(trade.get("entry_price", 0) or 0)
            px = float(trade.get("close_price") or trade.get("current_price") or entry or 0)
            symbol = str(trade.get("symbol") or "XAU/USD")
            return calculate_pips(entry, px, typ, symbol)

        def _status_label(trade, points: float) -> str:
            status = str(trade.get("status", "")).upper()
            if status == "SL_HIT" and points > 0:
                return "SL+ / Profit Locked"
            if status == "SL_HIT" and points == 0:
                return "SL / Breakeven"
            return status

        def _usd(points: float) -> float:
            return points / 10.0

        lines = [
            "📊 <b>SmartSignal — Daily Summary</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"📅 {report_date} ({timezone_name})",
            "",
        ]

        net_pts = float(stats.get("net_points", 0) or 0)
        open_pts = sum(calculate_pips(float(t.get("entry_price", 0) or 0), float(t.get("current_price", t.get("entry_price", 0)) or 0), 
                                      str(t.get("type") or t.get("trade_type", "BUY")).upper(), str(t.get("symbol") or "XAU/USD")) 
                       for t in open_trades) if open_trades else 0.0
        combined_net = net_pts + open_pts

        lines.append("📊 <b>Performance (today)</b>")
        lines.append(f"• Trades: {stats.get('total', 0)} (✅ {stats.get('wins', 0)} · ❌ {stats.get('losses', 0)} · ➖ {stats.get('breakeven', 0)} · 🔄 {stats.get('open', 0)})")
        lines.append(f"• Win rate: {stats.get('win_rate', 0)}%")
        lines.append(f"• Closed Net: {net_pts:+.0f} pts ({_usd(net_pts):+.1f}$)")
        if open_trades:
            lines.append(f"• Floating Net: {open_pts:+.0f} pts ({_usd(open_pts):+.1f}$)")
            lines.append(f"• Combined Net: {combined_net:+.0f} pts ({_usd(combined_net):+.1f}$)")
        
        pf = stats.get("profit_factor", 0)
        pf_display = "∞" if pf >= 99 or (pf in (0, 99.9) and stats.get("losses", 0) == 0 and stats.get("wins", 0) > 0) else pf
        if stats.get("total", 0):
            lines.append(f"• Best: {float(stats.get('best_trade', 0)):+.0f} pts | Worst: {float(stats.get('worst_trade', 0)):+.0f} pts | PF: {pf_display}")
        lines.append("")

        if closed_today:
            lines.append(f"📕 <b>Closed Trades:</b> {len(closed_today)}  (✅ {len([t for t in closed_today if _pts(t) > 0])} · ❌ {len([t for t in closed_today if _pts(t) < 0])} · ➖ {len([t for t in closed_today if _pts(t) == 0])})")
            for t in sorted(closed_today, key=_pts, reverse=True)[:8]:
                p = _pts(t)
                sign = "🟢" if p > 0 else "🔴" if p < 0 else "➖"
                lines.append(f"{sign} {str(t.get('type') or t.get('trade_type', 'BUY')).upper()} {p:+.0f} pts ({_usd(p):+.1f}$) · {_status_label(t, p)}")
            if len(closed_today) > 8: lines.append(f"• … and {len(closed_today) - 8} more")
            lines.append(f"• Closed Net: {sum(_pts(t) for t in closed_today):+.0f} pts ({_usd(sum(_pts(t) for t in closed_today)):+.1f}$)")
            lines.append("")
        else:
            lines.append("📕 <b>Closed Trades:</b> none today")
            lines.append("")

        if open_trades:
            lines.append(f"🔄 <b>Open Trades:</b> {len(open_trades)}")
            for t in open_trades[:8]:
                entry = float(t.get("entry_price", 0) or 0)
                curr = float(t.get("current_price", entry) or entry)
                typ = str(t.get("type") or t.get("trade_type", "BUY")).upper()
                p = calculate_pips(entry, curr, typ, str(t.get("symbol") or "XAU/USD"))
                sign = "🟢" if p > 0 else "🔴" if p < 0 else "➖"
                lines.append(f"{sign} {typ} @ {entry:.2f} → {curr:.2f}  {p:+.0f} pts ({_usd(p):+.1f}$)")
            lines.append("")
        else:
            lines.append("🔄 <b>Open Trades:</b> none")
            lines.append("")

        learning_section = _read_eod_section("learning")
        if learning_section:
            compact_learning = _compact_section(learning_section, max_lines=8)
            if compact_learning.strip():
                lines.append("🧠 <b>Learning Update</b>")
                lines.append(compact_learning)
                lines.append("")

        # Optional Gemini
        try:
            gemini = get_gemini_review_service(config)
            if gemini.enabled:
                daily_review = gemini.summarize_daily_report({
                    "report_date": report_date, "stats": stats, "closed_trades_count": len(closed_today),
                    "open_trades_count": len(open_trades), "closed_net_points": net_pts, "floating_net_points": open_pts,
                    "learning_excerpt": learning_section,
                })
                if daily_review.get("available"):
                    lines.append("🧠 <b>Gemini Independent Daily Review</b>")
                    if daily_review.get("summary"): lines.append(f"• Summary: {daily_review.get('summary')}")
                    for p in (daily_review.get("key_points") or [])[:3]: lines.append(f"• {p}")
                    if daily_review.get("verdict"): lines.append(f"• Verdict: {daily_review.get('verdict')}")
                    lines.append("")
        except Exception: logger.exception("Gemini daily report failed")

        lines.append("⚠️ Paper-trading only • Educational")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        message = "\n".join(lines)
        telegram.send_message(message)
        save_daily_report_to_database(database, report_date=report_date, stats=stats, report_text=message, closed_trades_count=len(closed_today))
        _cleanup_eod_sections()
        logger.info("✅ Sent consolidated daily summary")

    except Exception as exc:
        logger.exception("خطأ في التقرير اليومي المدمج")
        telegram.send_error_alert(f"Daily summary failed: {exc}")


if __name__ == "__main__":
    main()
