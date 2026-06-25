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


def _eod_dir():
    from pathlib import Path
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
    "📊 Learning Update" / "🧠 AI Trade Review (Losses)") because the daily
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
    """Generate and send a SINGLE consolidated daily summary report.

    We merge:
    - Performance stats
    - Open trades
    - Key learning insights (if any)
    - Recent AI Trade Review highlights (if any)

    This avoids sending 4 separate messages.
    """
    logger.info("بدء التقرير اليومي المدمج: %s", datetime.now(timezone.utc).isoformat())

    config = load_config()
    telegram = TelegramService(config)
    database = DatabaseService(config)

    try:
        # 1. Get today's trades (open + closed) and compute rich stats.
        today_trades = database.get_today_trades()
        agent = DailyReportAgent(config)
        perf_report = agent.generate(today_trades)
        stats = perf_report.get("stats", {})

        # 2. Open trades (live).
        open_trades = database.get_open_trades()

        # 3. Split today's trades into CLOSED vs OPEN for clear reporting.
        # OPEN = not filled yet; CANCELLED = never traded. Neither is a live
        # position nor a realized (closed) trade, so exclude both from stats.
        open_statuses = {"OPEN", "TP1_HIT", "PARTIAL"}
        non_trade_statuses = {"CANCELLED"}
        closed_today = [
            t for t in today_trades
            if str(t.get("status", "")).upper() not in open_statuses | non_trade_statuses
        ]
        
        def _pts(trade) -> float:
            """Realized/floating PnL in POINTS (gold: 1 USD = 10 points).

            OpenTradesManager already stores final_pnl/current_pnl/* in POINTS
            (from calculate_pips), so those are used as-is. Only when deriving
            from raw entry/close prices do we apply the ×10 conversion.
            """
            for key in ("final_pnl_points", "current_pnl_points", "final_pnl", "current_pnl"):
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
            move = (px - entry) if typ == "BUY" else (entry - px)
            return move * 10.0

        def _usd(points: float) -> float:
            return points / 10.0

        # Build one clean consolidated message.
        lines = [
            "📊 <b>Gold AI Signals — Daily Summary</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"📅 {datetime.now(timezone.utc).strftime('%Y-%m-%d')} (Asia/Hebron)",
            "",
        ]

        # ── Performance snapshot (today) ────────────────────────────────────
        net_pts = float(stats.get("net_points", 0) or 0)
        lines.append("📊 <b>Performance (today)</b>")
        lines.append(
            f"• Trades: {stats.get('total', 0)} "
            f"(✅ {stats.get('wins', 0)} · ❌ {stats.get('losses', 0)} · "
            f"➖ {stats.get('breakeven', 0)} · 🔄 {stats.get('open', 0)})"
        )
        lines.append(f"• Win rate: {stats.get('win_rate', 0)}%")
        lines.append(f"• Net: {net_pts:+.0f} pts ({_usd(net_pts):+.1f}$)")
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
                status = str(t.get("status", "")).upper()
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
                p = (curr - entry) * 10.0 if typ == "BUY" else (entry - curr) * 10.0
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
        # run_learning.py and run_trade_review.py (with EOD_QUIET=true) write
        # their summaries to storage/eod_*.txt instead of sending their own
        # Telegram message. We fold them into this single consolidated report.
        learning_section = _read_eod_section("learning")
        review_section = _read_eod_section("review")

        # Only show the lightweight insight when the richer learning section is
        # absent (avoids two "Learning" blocks).
        if learning_insight and not learning_section:
            lines.append(f"🧠 <b>Learning:</b> {learning_insight}")
            lines.append("")

        if learning_section:
            lines.append("🧠 <b>Learning Update</b>")
            lines.append(_compact_section(learning_section, max_lines=8))
            lines.append("")
        if review_section:
            lines.append("🔎 <b>AI Trade Review</b>")
            lines.append(_compact_section(review_section, max_lines=10))
            lines.append("")

        lines.append("⚠️ Paper-trading only • Educational")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        message = "\n".join(lines)
        # Telegram hard limit is 4096 chars; trim defensively.
        if len(message) > 3900:
            message = message[:3850].rstrip() + "\n…\n━━━━━━━━━━━━━━━━━━━━━"
        telegram.send_message(message)
        _cleanup_eod_sections()
        logger.info("✅ Sent consolidated daily summary (single message)")

    except Exception as exc:  # noqa: BLE001
        logger.exception("خطأ في التقرير اليومي المدمج")
        telegram.send_error_alert(f"Daily summary failed: {exc}")

    # We no longer send a separate Open Trades report (it is now inside the summary)


if __name__ == "__main__":
    main()
