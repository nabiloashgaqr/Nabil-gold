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
from typing import Any, Callable
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.daily_report_agent import DailyReportAgent
from services.analyst_distillation import AnalystDistillationService
from services.database import DatabaseService
from services.day_map_metrics import summarize_day_map_execution
from services.llm_review import get_gemini_review_service
from services.tuning_advisor import TuningAdvisor
from services.telegram_bot import TelegramService
from utils.helpers import calculate_pips, load_config, setup_logging
from utils.instruments import price_to_points
from utils.sessions import session_label_from_trade

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


def _snapshot(trade: dict[str, Any]) -> dict[str, Any]:
    """Return signal_snapshot as dict, accepting JSON strings from Supabase."""
    snap = trade.get("signal_snapshot") or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except Exception:  # noqa: BLE001
            snap = {}
    return snap if isinstance(snap, dict) else {}


def _trade_points(trade: dict[str, Any]) -> float:
    """Extract realized/floating PnL in points with a price-based fallback."""
    status = str(trade.get("status", "")).upper()
    is_closed = status not in {"OPEN", "TP1_HIT", "PARTIAL", "PENDING"}
    keys = (
        ("final_pnl_points", "final_pnl", "current_pnl_points", "current_pnl")
        if is_closed else
        ("current_pnl_points", "current_pnl", "final_pnl_points", "final_pnl")
    )
    for key in keys:
        value = trade.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    try:
        typ = str(trade.get("type") or trade.get("trade_type") or "BUY").upper()
        entry = float(trade.get("entry_price", 0) or 0)
        px = float(trade.get("close_price") or trade.get("current_price") or entry or 0)
        symbol = str(trade.get("symbol") or "XAU/USD")
        return calculate_pips(entry, px, typ, symbol)
    except Exception:  # noqa: BLE001
        return 0.0


def _planned_risk_points(trade: dict[str, Any]) -> float:
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
            return abs(price_to_points(entry - sl, symbol=trade.get("symbol") or "XAU/USD"))
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _planned_rr(trade: dict[str, Any]) -> float:
    for key in ("planned_rr", "rr_ratio"):
        try:
            value = trade.get(key)
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    sig = _snapshot(trade).get("signal") or {}
    for key in ("rr_ratio", "tp2_rr"):
        try:
            value = sig.get(key)
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return 0.0


def _bucket_metric(trades: list[dict[str, Any]], key_func: Callable[[dict[str, Any]], str]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for trade in trades:
        key = str(key_func(trade) or "UNKNOWN")
        pnl = _trade_points(trade)
        bucket = buckets.setdefault(key, {"count": 0, "pnl": 0.0, "wins": 0, "losses": 0})
        bucket["count"] += 1
        bucket["pnl"] += pnl
        if pnl > 0:
            bucket["wins"] += 1
        elif pnl < 0:
            bucket["losses"] += 1
    return {
        key: {
            **value,
            "pnl": round(value["pnl"], 1),
            "win_rate_pct": round(value["wins"] / value["count"] * 100, 1) if value["count"] else 0.0,
        }
        for key, value in buckets.items()
    }


def _trade_session_label(trade: dict[str, Any]) -> str:
    """Return a standardised session label for a trade.

    Uses the unified session classifier from utils.sessions so that
    all reports show consistent names (e.g. "Asia Morning") instead
    of the raw config name (e.g. "Main Trading Session").
    """
    return session_label_from_trade(trade)


def _trade_news_label(trade: dict[str, Any]) -> str:
    news_context = _snapshot(trade).get("news_context") or {}
    rule = news_context.get("rule_based") or {}
    return str(
        trade.get("news_status_at_entry")
        or rule.get("market_status")
        or rule.get("status")
        or "UNKNOWN"
    ).upper()


def _trade_regime_label(trade: dict[str, Any]) -> str:
    market_context = _snapshot(trade).get("market_context") or {}
    tech = market_context.get("technical_regime") or {}
    return str(trade.get("volatility_regime") or tech.get("volatility_regime") or "UNKNOWN").upper()


def _daily_enrichment_summary(closed_trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact Phase 5 quality metrics for the daily Telegram/Gemini report."""
    actual_r: list[float] = []
    planned: list[float] = []
    for trade in closed_trades:
        risk = _planned_risk_points(trade)
        if risk <= 0:
            continue
        actual_r.append(_trade_points(trade) / risk)
        rr = _planned_rr(trade)
        if rr > 0:
            planned.append(rr)

    rr_efficiency: dict[str, Any] = {"sample": 0}
    # RR capture only on WINNING trades — losses are about stop-loss doing
    # its job, not about capturing planned reward.
    winners_r = [a for a in actual_r if a > 0]
    if winners_r:
        avg_actual = sum(winners_r) / len(winners_r)
        avg_planned = sum(planned) / len(planned) if planned else 0.0
        rr_efficiency = {
            "sample": len(winners_r),
            "avg_actual_r": round(avg_actual, 2),
            "avg_planned_rr": round(avg_planned, 2),
            "rr_capture_pct": round(avg_actual / avg_planned * 100, 1) if avg_planned else 0.0,
        }

    session_breakdown = _bucket_metric(closed_trades, _trade_session_label)
    news_proximity = _bucket_metric(closed_trades, _trade_news_label)
    regime_fit = _bucket_metric(closed_trades, _trade_regime_label)
    return {
        "rr_efficiency": rr_efficiency,
        "session_breakdown": session_breakdown,
        "news_proximity": news_proximity,
        "regime_fit": regime_fit,
    }


def _best_bucket(buckets: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    if not buckets:
        return None
    return max(buckets.items(), key=lambda item: (float(item[1].get("pnl", 0) or 0), int(item[1].get("count", 0) or 0)))


def _worst_bucket(buckets: dict[str, dict[str, Any]], skip_unknown: bool = False) -> tuple[str, dict[str, Any]] | None:
    items = [(k, v) for k, v in buckets.items() if not (skip_unknown and str(k).upper() == "UNKNOWN")]
    if not items:
        return None
    return min(items, key=lambda item: (float(item[1].get("pnl", 0) or 0), -int(item[1].get("count", 0) or 0)))


def _quality_snapshot_lines(enrichment: dict[str, Any]) -> list[str]:
    """Return a very short section; avoids making the daily Telegram too long."""
    lines: list[str] = []
    rr = enrichment.get("rr_efficiency") or {}
    if rr.get("sample"):
        lines.append(
            f"• RR Capture: {float(rr.get('avg_actual_r', 0)):+.2f}R vs planned "
            f"{float(rr.get('avg_planned_rr', 0)):.2f}R ({float(rr.get('rr_capture_pct', 0)):.1f}%)"
        )
    best_session = _best_bucket(enrichment.get("session_breakdown") or {})
    worst_session = _worst_bucket(enrichment.get("session_breakdown") or {})
    if best_session:
        label, data = best_session
        line = f"• Best session: {label} {float(data.get('pnl', 0)):+.0f} pts"
        if worst_session and worst_session[0] != label and float((worst_session[1] or {}).get("pnl", 0) or 0) < 0:
            line += f" | Worst: {worst_session[0]} {float(worst_session[1].get('pnl', 0)):+.0f}"
        lines.append(line)
    weak_news = _worst_bucket(enrichment.get("news_proximity") or {}, skip_unknown=True)
    if weak_news:
        label, data = weak_news
        lines.append(f"• News impact: {label} {float(data.get('pnl', 0)):+.0f} pts ({data.get('count', 0)} trades)")
    best_regime = _best_bucket(enrichment.get("regime_fit") or {})
    if best_regime:
        label, data = best_regime
        lines.append(f"• Best regime: {label} {float(data.get('pnl', 0)):+.0f} pts · WR {float(data.get('win_rate_pct', 0)):.0f}%")
    return lines[:4]


def _daily_management_brief_lines(config: dict, day_map_execution: dict[str, Any], analyst_overlap: dict[str, Any] | None = None) -> list[str]:
    memo = TuningAdvisor(config).build_live_operator_memo(
        day_map_execution=day_map_execution,
        analyst_overlap=analyst_overlap or {},
    )
    lines = [
        f"• Priority: {memo.get('priority', 'NORMAL')}",
        f"• {memo.get('headline', 'No operator headline')}",
    ]
    focus = memo.get('next_round_focus', []) or []
    if focus:
        lines.append(f"• Next move: {focus[0]}")
    return lines[:3]


def _daily_operator_focus_lines(config: dict, day_map_execution: dict[str, Any], analyst_overlap: dict[str, Any] | None = None) -> list[str]:
    memo = TuningAdvisor(config).build_live_operator_memo(
        day_map_execution=day_map_execution,
        analyst_overlap=analyst_overlap or {},
    )
    lines = []
    for item in (memo.get('findings', []) or [])[:3]:
        lines.append(f"• {item}")
    for item in (memo.get('suggested_config_changes', []) or [])[:2]:
        lines.append(f"• Tune: {item}")
    return lines[:5]


def _day_map_execution_lines(summary: dict[str, Any]) -> list[str]:
    if not summary or int(summary.get("tracked_trade_count", 0) or 0) <= 0:
        return []
    metrics = summary.get("scenario_metrics") or {}
    roles = summary.get("role_breakdown") or {}
    main_count = int(((roles.get("PRIMARY") or {}).get("count", 0) or 0) + ((roles.get("STARTER") or {}).get("count", 0) or 0))
    add_count = int(((roles.get("STANDBY") or {}).get("count", 0) or 0) + ((roles.get("ADD_ON") or {}).get("count", 0) or 0))
    lines = [
        f"• Main worked: {int(metrics.get('main_worked_count', 0) or 0)} | Add needed: {int(metrics.get('add_needed_count', 0) or 0)}",
        f"• Starter survived alone: {int(metrics.get('starter_survived_alone_count', 0) or 0)} | Day-map failed: {int(metrics.get('day_map_failed_count', 0) or 0)}",
        f"• Mapped legs tracked: Main {main_count} | Add {add_count}",
    ]
    map_changed = int(metrics.get("map_changed_cancelled_count", 0) or 0)
    if map_changed:
        lines.append(f"• Map-changed cancellations: {map_changed}")
    return lines[:4]


def _analyst_overlap_lines(summary: dict[str, Any]) -> list[str]:
    """Compact analyst-vs-bot overlap lines for the daily report."""
    if not summary or int(summary.get("labels_considered", 0) or 0) <= 0:
        return []
    lines = [
        f"• Labels: {summary.get('labels_considered', 0)} | Matched {summary.get('matched_labels', 0)} | Partial {summary.get('partial_matches', 0)} | Missed {summary.get('missed_labels', 0)}",
        f"• Coverage: {summary.get('coverage_rate_pct', 0)}% | Match: {summary.get('match_rate_pct', 0)}%",
    ]
    if summary.get("avg_entry_distance_points") is not None:
        lines.append(f"• Avg entry distance: {summary.get('avg_entry_distance_points')} pts")
    reasons = summary.get("top_missed_reasons") or []
    if reasons:
        top = ", ".join(f"{r.get('reason_code')} ({r.get('count', 0)})" for r in reasons[:2])
        lines.append(f"• Miss reasons: {top}")
    return lines[:4]


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
        daily_enrichment = _daily_enrichment_summary(closed_today)
        stats.update(daily_enrichment)
        day_map_execution = summarize_day_map_execution(today_trades)
        stats["day_map_execution"] = day_map_execution
        
        open_trades = open_at_that_time
        stats["open"] = len(open_trades)
        
        def _pts(trade) -> float:
            return _trade_points(trade)

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

        quality_lines = _quality_snapshot_lines(daily_enrichment)
        if quality_lines:
            lines.append("🧩 <b>Quality Snapshot</b>")
            lines.extend(quality_lines)
            lines.append("")

        day_map_lines = _day_map_execution_lines(day_map_execution)
        if day_map_lines:
            lines.append("🗺️ <b>Day-Map Execution</b>")
            lines.extend(day_map_lines)
            lines.append("")

        analyst_lines: list[str] = []
        try:
            distill = AnalystDistillationService(database, config)
            if distill.enabled:
                compare_limit = int((config.get("analyst_distillation", {}) or {}).get("daily_compare_limit", 20) or 20)
                analyst_summary = distill.compare_recent(symbol=config.get("symbol", "XAU/USD"), limit=compare_limit)
                stats["analyst_overlap"] = analyst_summary
                analyst_lines = _analyst_overlap_lines(analyst_summary)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analyst overlap summary unavailable for daily report: %s", exc)
        if analyst_lines:
            lines.append("🧠 <b>Analyst Overlap</b>")
            lines.extend(analyst_lines)
            lines.append("")

        management_brief_lines = _daily_management_brief_lines(config, day_map_execution, stats.get("analyst_overlap") or {})
        operator_focus_lines = _daily_operator_focus_lines(config, day_map_execution, stats.get("analyst_overlap") or {})
        if management_brief_lines:
            lines.append("📌 <b>Management Brief</b>")
            lines.extend(management_brief_lines)
            lines.append("")
        if operator_focus_lines:
            lines.append("🧭 <b>Operator Focus</b>")
            lines.extend(operator_focus_lines)
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
            compact_learning = _compact_section(learning_section, max_lines=15)
            if compact_learning.strip():
                lines.append("🧠 <b>Learning Update</b>")
                lines.append(compact_learning)
                lines.append("")

        # Optional Gemini
        try:
            gemini = get_gemini_review_service(config)
            if not gemini.enabled:
                logger.info("🧠 Gemini daily review skipped: API key not configured")
            else:
                daily_review = gemini.summarize_daily_report({
                    "report_date": report_date, "stats": stats, "closed_trades_count": len(closed_today),
                    "open_trades_count": len(open_trades), "closed_net_points": net_pts, "floating_net_points": open_pts,
                    "learning_excerpt": learning_section,
                    "rr_efficiency": daily_enrichment.get("rr_efficiency"),
                    "session_breakdown": daily_enrichment.get("session_breakdown"),
                    "news_proximity": daily_enrichment.get("news_proximity"),
                    "regime_fit": daily_enrichment.get("regime_fit"),
                })
                if daily_review.get("available"):
                    logger.info("🧠 Gemini daily review added: verdict=%s quality=%s", daily_review.get("verdict"), daily_review.get("quality", "ok"))
                    lines.append("🧠 <b>Gemini Independent Daily Review</b>")
                    if daily_review.get("summary"): lines.append(f"• Summary: {daily_review.get('summary')}")
                    for p in (daily_review.get("key_points") or [])[:3]: lines.append(f"• {p}")
                    if daily_review.get("verdict"): lines.append(f"• Verdict: {daily_review.get('verdict')}")
                    lines.append("")
                elif daily_review.get("suppressed"):
                    logger.info("🧠 Gemini daily review suppressed: %s", daily_review.get("suppress_reason", "generic"))
                else:
                    logger.warning("🧠 Gemini daily review unavailable: %s", daily_review.get("summary") or daily_review.get("reason"))
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
