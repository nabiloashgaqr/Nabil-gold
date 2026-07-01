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


def send_open_trades_report(db: DatabaseService, telegram: TelegramService) -> None:
    """إرسال تقرير الصفقات المفتوحة بدون الاعتماد على execute_query."""
    try:
        trades = db.get_open_trades()

        if not trades:
            telegram.send_message(
                "📊 <b>Open Positions</b>\n\n"
                "❌ No open trades currently"
            )
            return

        lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            "📊 <b>Open Positions</b>",
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


def save_daily_report_to_database(
    db: DatabaseService,
    *,
    report_date: str,
    stats: dict,
    report_text: str,
    closed_trades_count: int,
) -> None:
    """Persist the daily report into Supabase so the dashboard can read it.

    This is intentionally best-effort: report delivery to Telegram should not fail
    just because the archive insert/update failed. In production with Supabase it
    upserts by report_date (select existing row then update, otherwise insert).
    In local fallback it writes storage/daily_report.json for debugging.
    """
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
                logger.info("Saved daily report to Supabase: updated report_date=%s", report_date)
            else:
                client.table("daily_reports").insert(payload).execute()
                logger.info("Saved daily report to Supabase: inserted report_date=%s", report_date)
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
        logger.info("Saved daily report local fallback: storage/daily_report.json")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to save local daily report JSON: %s", exc)


def main() -> None:
    """Generate and send a SINGLE consolidated daily summary report.

    We merge:
    - Performance stats
    - Open trades
    - Key learning insights (if any)

    This avoids sending 4 separate messages.
    """
    logger.info("بدء التقرير اليومي المدمج: %s", datetime.now(timezone.utc).isoformat())

    config = load_config()
    telegram = TelegramService(config)
    database = DatabaseService(config)
    timezone_name = _report_timezone(config)
    report_date = _resolve_report_date(os.environ.get("REPORT_DATE"), timezone_name)
    current_local_date = datetime.now(ZoneInfo(timezone_name)).date().isoformat()

    try:
        # 1. Get trades related to this local date (created OR closed)
        today_trades = database.get_trades_for_date(report_date, timezone_name)
        
        # 2. Logic for historical accuracy:
        # - A trade is CLOSED TODAY if its closed_at matches report_date.
        # - A trade is OPEN TODAY if it was created on or before report_date 
        #   AND (it's currently open OR it was closed AFTER report_date).
        
        closed_today = []
        open_at_that_time = []
        
        for t in today_trades:
            status = str(t.get("status", "")).upper()
            created_at = str(t.get("created_at") or t.get("entry_time") or "")
            closed_at = str(t.get("closed_at") or t.get("close_time") or "")
            
            # Is it closed on the report_date?
            is_closed_today = closed_at.startswith(report_date)
            
            # Was it open during the report_date?
            # (Created today or before) AND (Not closed yet OR closed after today)
            was_open_then = created_at.startswith(report_date) and (not closed_at or not is_closed_today)

            if is_closed_today and status not in {"CANCELLED", "PENDING"}:
                closed_today.append(t)
            elif was_open_then and status in {"OPEN", "TP1_HIT", "PARTIAL", "SL_HIT", "TP2_HIT", "BE_HIT", "MANUAL_CLOSE"}:
                # If it's currently closed but was open then, we show it as open in that day's report
                open_at_that_time.append(t)

        agent = DailyReportAgent(config)
        # Generate stats based on what was actually CLOSED today
        perf_report = agent.generate(closed_today)
        stats = perf_report.get("stats", {})
        
        # Override open trades count for the report header
        open_trades = open_at_that_time
        stats["open"] = len(open_trades)

        # ... (rest of the logic remains the same)
        
        def _pts(trade) -> float:
            """Realized/floating PnL in POINTS (gold: 1 USD = 10 points).

            Important: an ``SL_HIT`` row is not automatically a loss. After
            breakeven/trailing, ``SL_HIT`` can mean a profitable protected exit
            (SL+). For closed trades, prefer FINAL realized PnL over any stale
            floating/current PnL fields left from an earlier update. This keeps
            the daily report consistent with the Performance block.
            """
            status = str(trade.get("status", "")).upper()
            is_closed = status not in {"OPEN", "TP1_HIT", "PARTIAL", "PENDING"}
            keys = (
                ("final_pnl", "final_pnl_points", "current_pnl", "current_pnl_points")
                if is_closed
                else ("current_pnl", "current_pnl_points", "final_pnl", "final_pnl_points")
            )
            for key in keys:
                v = trade.get(key)
                if v is not None:
                    try:
                        return float(v)  # already points
                    except (TypeError, ValueError):
                        pass
            # Last resort: derive from entry vs close/current price (USD ×10).
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

        # Build one clean consolidated message.
        lines = [
            "📊 <b>SmartSignal — Daily Summary</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"📅 {report_date} ({timezone_name})",
            "",
        ]

        # ── Performance snapshot (today) ────────────────────────────────────
        net_pts = float(stats.get("net_points", 0) or 0)
        open_pts = sum(calculate_pips(float(t.get("entry_price", 0) or 0), float(t.get("current_price", t.get("entry_price", 0)) or 0), str(t.get("type") or t.get("trade_type", "BUY")).upper(), str(t.get("symbol") or "XAU/USD")) for t in open_trades) if open_trades else 0.0
        combined_net = net_pts + open_pts

        lines.append("📊 <b>Performance (today)</b>")
        lines.append(
            f"• Trades: {stats.get('total', 0)} "
            f"(✅ {stats.get('wins', 0)} · ❌ {stats.get('losses', 0)} · "
            f"➖ {stats.get('breakeven', 0)} · 🔄 {stats.get('open', 0)})"
        )
        lines.append(f"• Win rate: {stats.get('win_rate', 0)}%")
        lines.append(f"• Closed Net: {net_pts:+.0f} pts ({_usd(net_pts):+.1f}$)")
        if open_trades:
            lines.append(f"• Floating Net: {open_pts:+.0f} pts ({_usd(open_pts):+.1f}$)")
            lines.append(f"• Combined Net: {combined_net:+.0f} pts ({_usd(combined_net):+.1f}$)")
        
        pf = stats.get("profit_factor", 0)
        pf_display = "∞" if pf >= 99 or (pf in (0, 99.9) and stats.get("losses", 0) == 0 and stats.get("wins", 0) > 0) else pf
        if stats.get("total", 0):
            lines.append(
                f"• Best: {float(stats.get('best_trade', 0)):+.0f} pts | "
                f"Worst: {float(stats.get('worst_trade', 0)):+.0f} pts | "
                f"PF: {pf_display}"
            )
        if stats.get("losses", 0) == 0 and stats.get("wins", 0) > 0:
            lines.append("• Note: All trades profitable → PF shown as ∞ (no gross loss)")
        lines.append("")

        # ── Closed trades today ─────────────────────────────────────────────
        if closed_today:
            wins = [t for t in closed_today if _pts(t) > 0]
            losses = [t for t in closed_today if _pts(t) < 0]
            flat = [t for t in closed_today if _pts(t) == 0]
            closed_net = sum(_pts(t) for t in closed_today)
            lines.append(f"📕 <b>Closed Trades:</b> {len(closed_today)}  (✅ {len(wins)} · ❌ {len(losses)} · ➖ {len(flat)})")
            for t in sorted(closed_today, key=_pts, reverse=True)[:8]:
                typ = str(t.get("type") or t.get("trade_type", "BUY")).upper()
                p = _pts(t)
                sign = "🟢" if p > 0 else "🔴" if p < 0 else "➖"
                status = _status_label(t, p)
                lines.append(f"{sign} {typ} {p:+.0f} pts ({_usd(p):+.1f}$) · {status}")
            if len(closed_today) > 8:
                lines.append(f"• … and {len(closed_today) - 8} more")
            lines.append(f"• Closed Net: {closed_net:+.0f} pts ({_usd(closed_net):+.1f}$)")
            lines.append("")
        else:
            lines.append("📕 <b>Closed Trades:</b> none today")
            lines.append("")

        # ── Open trades (live floating PnL) ─────────────────────────────────
        if open_trades:
            lines.append(f"🔄 <b>Open Trades:</b> {len(open_trades)}")
            total_pts = 0.0
            for t in open_trades[:8]:
                typ = str(t.get("type") or t.get("trade_type", "BUY")).upper()
                entry = float(t.get("entry_price", 0) or 0)
                curr = float(t.get("current_price", entry) or entry)
                symbol = str(t.get("symbol") or "XAU/USD")
                p = calculate_pips(entry, curr, typ, symbol)
                total_pts += p
                sign = "🟢" if p > 0 else "🔴" if p < 0 else "➖"
                lines.append(f"{sign} {typ} @ {entry:.2f} → {curr:.2f}  {p:+.0f} pts ({_usd(p):+.1f}$)")
            if len(open_trades) > 8:
                lines.append(f"• … and {len(open_trades) - 8} more")
            lines.append(f"• Floating Net: {total_pts:+.0f} pts ({_usd(total_pts):+.1f}$)")
            lines.append("")
        else:
            lines.append("🔄 <b>Open Trades:</b> none")
            lines.append("")

        # ── By direction (today) ────────────────────────────────────────────
        direction = stats.get("by_direction", {}) or {}
        buy = direction.get("BUY", {}) or {}
        sell = direction.get("SELL", {}) or {}
        if buy.get("count") or sell.get("count"):
            bnet = float(buy.get("net", 0) or 0)
            snet = float(sell.get("net", 0) or 0)
            lines.append("🧭 <b>By Direction</b>")
            lines.append(f"• BUY: {buy.get('count', 0)} · Net {bnet:+.0f} pts")
            lines.append(f"• SELL: {sell.get('count', 0)} · Net {snet:+.0f} pts")
            lines.append("")

        learning_insight = ""

        # ── Merge end-of-day sections produced by the quiet sub-scripts ──────
        # run_learning.py (with EOD_QUIET=true) writes
        # its summary to storage/eod_*.txt instead of sending its own
        # Telegram message. We fold them into this single consolidated report.
        learning_section = _read_eod_section("learning")

        # Only show the lightweight insight when the richer learning section is
        # absent (avoids two "Learning" blocks).
        if learning_insight and not learning_section:
            lines.append(f"🧠 <b>Learning:</b> {learning_insight}")
            lines.append("")

        if learning_section:
            compact_learning = _compact_section(learning_section, max_lines=8)
            if compact_learning.strip():
                lines.append("🧠 <b>Learning Update</b>")
                lines.append(compact_learning)
                lines.append("")

        closed_sample = []
        for t in sorted(closed_today, key=_pts, reverse=True)[:8]:
            closed_sample.append({
                "type": str(t.get("type") or t.get("trade_type", "BUY")).upper(),
                "status": str(t.get("status") or ""),
                "points": _pts(t),
                "entry_price": t.get("entry_price"),
                "close_price": t.get("close_price") or t.get("current_price"),
            })

        open_sample = []
        for t in open_trades[:6]:
            typ = str(t.get("type") or t.get("trade_type", "BUY")).upper()
            entry = float(t.get("entry_price", 0) or 0)
            curr = float(t.get("current_price", entry) or entry)
            symbol = str(t.get("symbol") or "XAU/USD")
            open_sample.append({
                "type": typ,
                "status": str(t.get("status") or "OPEN"),
                "floating_points": calculate_pips(entry, curr, typ, symbol),
                "entry_price": entry,
                "current_price": curr,
            })

        # ── Optional Gemini daily report overlay ──────────────────────────
        try:
            gemini = get_gemini_review_service(config)
            if not gemini.enabled:
                logger.info("🧠 Gemini Daily Review skipped: API key not configured")
            else:
                daily_review = gemini.summarize_daily_report({
                    "report_date": report_date,
                    "stats": stats,
                    "closed_trades_count": len(closed_today),
                    "open_trades_count": len(open_trades),
                    "closed_net_points": sum(_pts(t) for t in closed_today) if closed_today else 0.0,
                    "floating_net_points": sum(calculate_pips(float(t.get("entry_price", 0) or 0), float(t.get("current_price", t.get("entry_price", 0)) or 0), str(t.get("type") or t.get("trade_type", "BUY")).upper(), str(t.get("symbol") or "XAU/USD")) for t in open_trades) if open_trades else 0.0,
                    "learning_excerpt": _compact_section(learning_section, max_lines=6) if learning_section else "",
                    "closed_trades_sample": closed_sample,
                    "open_trades_sample": open_sample,
                })
                
                if daily_review.get("available"):
                    lines.append("🧠 <b>Gemini Daily Review</b>")
                    if daily_review.get("summary"):
                        lines.append(f"• Summary: {daily_review.get('summary')}")
                    for key, label in (("strengths", "Strengths"), ("warnings", "Warnings"), ("tomorrow_focus", "Tomorrow")):
                        values = [str(x) for x in (daily_review.get(key) or []) if str(x).strip() and str(x).strip() != "…"]
                        if values:
                            lines.append(f"• <b>{label}:</b> " + " | ".join(values[:2]))
                    lines.append("")
                    logger.info("✅ Gemini Daily Review added to report")
                else:
                    logger.warning("🧠 Gemini Daily Review unavailable: %s", daily_review.get("summary"))
        except Exception as gemini_exc:
            logger.exception("🧠 Gemini daily report failed with exception")

        lines.append("⚠️ Paper-trading only • Educational")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        message = "\n".join(lines)
        # Telegram hard limit is 4096 chars; trim defensively.
        if len(message) > 3900:
            message = message[:3850].rstrip() + "\n…\n━━━━━━━━━━━━━━━━━━━━━"
        telegram.send_message(message)
        save_daily_report_to_database(
            database,
            report_date=report_date,
            stats=stats,
            report_text=message,
            closed_trades_count=len(closed_today),
        )
        _cleanup_eod_sections()
        logger.info("✅ Sent consolidated daily summary (single message)")

    except Exception as exc:  # noqa: BLE001
        logger.exception("خطأ في التقرير اليومي المدمج")
        telegram.send_error_alert(f"Daily summary failed: {exc}")

    # We no longer send a separate Open Trades report (it is now inside the summary)


if __name__ == "__main__":
    main()
