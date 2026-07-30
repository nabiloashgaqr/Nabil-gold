"""Quality metrics derived from the decision audit trail.

Each metric here exists because a real fault hid behind its absence:

  plan_to_order_rate      a confirmed day map produced no order for a whole
                          session, because a leg priced as MARKET was
                          abandoned rather than executed.
  breakeven_exit_rate     correct calls closed flat, because touching a token
                          TP1 armed the stop at entry.
  median_tp1_rr           first targets shipped at 0.03R and nobody noticed.
  entry_slippage_points   fills drifted from the planned entry.
  opposed_signal_rate     orders went out under an "aligned" banner while
                          qualified agents argued the other way.

A number that only appears after the damage is not a metric, it is a
post-mortem. These are meant to be read weekly, before the damage.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

BLOCK_STAGES_PLANNING = {"final validation", "dynamic risk"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct(part: int, whole: int) -> float:
    return round((part / whole) * 100.0, 1) if whole else 0.0


def build_execution_metrics(
    database: Any,
    *,
    days: int = 7,
    symbol: str | None = None,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Summarise the last `days` of decisions and closed trades."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).replace(microsecond=0).isoformat()

    try:
        audits = database.get_recent_decision_audits(limit=2000, symbol=symbol, since=since)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Execution metrics: audit trail unavailable: %s", exc)
        audits = []

    try:
        trades = database.get_recent_trades(limit=500)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Execution metrics: trade history unavailable: %s", exc)
        trades = []

    sent = [row for row in audits if str(row.get("outcome") or "").upper() == "SENT"]
    blocked = [row for row in audits if str(row.get("outcome") or "").upper() == "BLOCKED"]

    # Which filter is doing the stopping? A stage that blocks everything and a
    # stage that never fires are both worth seeing.
    by_stage: Dict[str, int] = {}
    for row in blocked:
        stage = str(row.get("stage") or "unknown")
        by_stage[stage] = by_stage.get(stage, 0) + 1

    decided = len(sent) + len(blocked)

    tp1_rrs = [_f(row.get("tp1_rr")) for row in sent if row.get("tp1_rr") is not None]
    median_tp1_rr = round(statistics.median(tp1_rrs), 2) if tp1_rrs else None
    weak_tp1 = len([rr for rr in tp1_rrs if rr < 0.8])

    opposed = len([row for row in sent if int(_f(row.get("oppose_count"))) > 0])

    closed = [
        t for t in trades
        if str(t.get("status") or "").upper() in {"TP2_HIT", "SL_HIT", "BE_HIT", "THESIS_EXIT", "MANUAL_CLOSE", "EXPIRED"}
    ]
    recent_closed = []
    for trade in closed:
        stamp = str(trade.get("closed_at") or trade.get("close_time") or trade.get("updated_at") or "")
        if not stamp or stamp >= since:
            recent_closed.append(trade)

    breakeven_exits = len([
        t for t in recent_closed
        if str(t.get("status") or "").upper() == "BE_HIT"
        or str(t.get("result") or "").upper() == "BREAKEVEN"
    ])

    slippages: List[float] = []
    for trade in recent_closed:
        planned = _f(trade.get("planned_entry_price"))
        actual = _f(trade.get("entry_price"))
        if planned > 0 and actual > 0:
            slippages.append(abs(actual - planned) * 10.0)
    median_slippage = round(statistics.median(slippages), 1) if slippages else None

    return {
        "window_days": days,
        "since": since,
        "decisions_recorded": decided,
        "signals_sent": len(sent),
        "signals_blocked": len(blocked),
        "plan_to_order_rate_pct": _pct(len(sent), decided),
        "blocks_by_stage": dict(sorted(by_stage.items(), key=lambda kv: kv[1], reverse=True)),
        "median_tp1_rr": median_tp1_rr,
        "weak_tp1_count": weak_tp1,
        "opposed_signal_rate_pct": _pct(opposed, len(sent)),
        "closed_trades": len(recent_closed),
        "breakeven_exit_rate_pct": _pct(breakeven_exits, len(recent_closed)),
        "median_entry_slippage_points": median_slippage,
    }


def format_execution_metrics(metrics: Dict[str, Any]) -> List[str]:
    """Render the metrics as Telegram-ready lines."""
    if not metrics or not metrics.get("decisions_recorded"):
        return ["📐 <b>Execution Quality</b>", "• No decisions recorded in this window."]

    lines = [
        "📐 <b>Execution Quality</b>",
        f"• Decisions: {metrics['decisions_recorded']} "
        f"({metrics['signals_sent']} sent / {metrics['signals_blocked']} blocked)",
        f"• Plan → order rate: {metrics['plan_to_order_rate_pct']:.1f}%",
    ]

    tp1 = metrics.get("median_tp1_rr")
    if tp1 is not None:
        warn = "  ⚠️ below 0.80R" if tp1 < 0.8 else ""
        lines.append(f"• Median TP1: {tp1:.2f}R{warn}")
    if metrics.get("weak_tp1_count"):
        lines.append(f"• Signals with TP1 under 0.80R: {metrics['weak_tp1_count']}")

    if metrics.get("closed_trades"):
        be_rate = metrics["breakeven_exit_rate_pct"]
        warn = "  ⚠️ protection may be arming early" if be_rate > 40 else ""
        lines.append(f"• Breakeven exits: {be_rate:.1f}% of {metrics['closed_trades']} closed{warn}")

    if metrics.get("opposed_signal_rate_pct"):
        lines.append(f"• Sent against dissent: {metrics['opposed_signal_rate_pct']:.1f}%")

    slip = metrics.get("median_entry_slippage_points")
    if slip is not None:
        lines.append(f"• Median entry slippage: {slip:.1f} pts")

    stages = metrics.get("blocks_by_stage") or {}
    if stages:
        top = list(stages.items())[:3]
        rendered = " · ".join(f"{name} ×{count}" for name, count in top)
        lines.append(f"• Top blockers: {rendered}")

    return lines
