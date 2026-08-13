"""Public dashboard payload built exclusively from VPS-local JSON storage.

No Supabase, GitHub, Vercel database, or browser-side secret is involved.
The returned trade objects use an explicit allowlist so signal_snapshot and
proprietary planning internals never leave the VPS.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from utils.helpers import load_trades_checked

OUTCOME_STATUSES = {
    "TP2_HIT", "SL_HIT", "TRAILING_SL_HIT", "BE_HIT", "EXPIRED",
    "THESIS_EXIT", "MANUAL_CLOSE", "CLOSED",
}
LIVE_STATUSES = {"OPEN", "TP1_HIT", "PARTIAL"}
PENDING_STATUSES = {"PENDING"}

PUBLIC_TRADE_FIELDS = {
    "id", "symbol", "type", "side", "status", "result",
    "entry_price", "stop_loss", "initial_stop_loss", "tp1", "tp2",
    "confidence", "trading_mode", "paper_trading", "mt5_ticket",
    "current_price", "current_pnl", "current_pnl_points",
    "final_pnl", "final_pnl_points", "pnl_points", "close_price",
    "planned_rr", "planned_risk_points", "planned_tp2_points",
    "order_kind", "order_type", "entry_time", "created_at", "closed_at",
    "close_time", "updated_at", "last_updated", "session_label",
    "news_status_at_entry", "regime_composite", "volatility_regime",
    "market_phase", "sl_moved_to_entry", "partial_close", "pending_cycles",
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pnl(row: Dict[str, Any]) -> float:
    for key in (
        "pnl_points", "final_pnl_points", "final_pnl",
        "current_pnl_points", "current_pnl", "pnl",
    ):
        if row.get(key) is not None:
            return _f(row.get(key))
    return 0.0


def _stamp(row: Dict[str, Any], *, closed: bool = False) -> str:
    keys = (
        ("closed_at", "close_time", "last_updated", "updated_at",
         "created_at", "entry_time")
        if closed else
        ("created_at", "entry_time", "updated_at", "last_updated")
    )
    return next((str(row.get(k)) for k in keys if row.get(k)), "")


def _snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    value = row.get("signal_snapshot") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {}
    return value if isinstance(value, dict) else {}


def public_trade(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: row[k] for k in PUBLIC_TRADE_FIELDS if k in row}
    out["id"] = str(row.get("id") or "")
    out["symbol"] = str(row.get("symbol") or "XAU/USD")
    out["type"] = str(row.get("type") or row.get("side") or "").upper()
    out["status"] = str(row.get("status") or "UNKNOWN").upper()
    out["pnl"] = _pnl(row)
    out["created_at"] = _stamp(row)
    out["closed_at"] = _stamp(row, closed=True)
    # Four narrow, non-proprietary fallbacks used by the UI.
    snap = _snapshot(row)
    session = snap.get("session_info") or {}
    if not out.get("session_label") and session.get("current_session"):
        out["session_label"] = session.get("current_session")
    tech = ((snap.get("market_context") or {}).get("technical_regime") or {})
    for key in ("volatility_regime", "market_phase"):
        if not out.get(key) and tech.get(key):
            out[key] = tech.get(key)
    sig = snap.get("signal") or {}
    if not _f(out.get("planned_rr")):
        out["planned_rr"] = _f(sig.get("rr_ratio") or sig.get("tp2_rr"))
    return out


def _summary(closed: List[Dict[str, Any]], live: List[Dict[str, Any]], pending: List[Dict[str, Any]]) -> Dict[str, Any]:
    wins = sum(_f(t.get("pnl")) > 0 for t in closed)
    losses = sum(_f(t.get("pnl")) < 0 for t in closed)
    net = sum(_f(t.get("pnl")) for t in closed)
    gp = sum(_f(t.get("pnl")) for t in closed if _f(t.get("pnl")) > 0)
    gl = abs(sum(_f(t.get("pnl")) for t in closed if _f(t.get("pnl")) < 0))
    return {
        "closedTrades": len(closed), "liveTrades": len(live),
        "pendingOrders": len(pending),
        "tp1Live": sum(t.get("status") == "TP1_HIT" for t in live),
        "beCount": len(closed) - wins - losses,
        "winRate": wins / (wins + losses) * 100 if wins + losses else 0,
        "netPoints": round(net, 1),
        "profitFactor": round(gp / gl, 3) if gl else (None if gp else 0),
        "wins": wins, "losses": losses,
    }


def _report_text(period: str, rows: List[Dict[str, Any]], *, weekly: bool = False) -> tuple[str, str]:
    wins = sum(_f(t.get("pnl")) > 0 for t in rows)
    losses = sum(_f(t.get("pnl")) < 0 for t in rows)
    be = len(rows) - wins - losses
    net = sum(_f(t.get("pnl")) for t in rows)
    wr = wins / (wins + losses) * 100 if wins + losses else 0
    kind_en = "Weekly" if weekly else "Daily"
    kind_ar = "الأسبوعي" if weekly else "اليومي"
    en = (
        f"SmartSignal — {kind_en} Report\nPeriod: {period}\n"
        f"Closed: {len(rows)} | Wins: {wins} | Losses: {losses} | BE: {be}\n"
        f"Win rate: {wr:.1f}%\nNet: {net:+.1f} pts\n"
        "Source: VPS local trade book."
    )
    ar = (
        f"سمارت سيجنال — التقرير {kind_ar}\nالفترة: {period}\n"
        f"المغلقة: {len(rows)} | رابحة: {wins} | خاسرة: {losses} | تعادل: {be}\n"
        f"نسبة الربح: {wr:.1f}%\nالصافي: {net:+.1f} نقطة\n"
        "المصدر: سجل الصفقات المحلي على VPS."
    )
    return en, ar


def _daily_reports(closed: List[Dict[str, Any]], limit: int = 31) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in closed:
        day = str(row.get("closed_at") or row.get("created_at") or "")[:10]
        if day:
            groups[day].append(row)
    result = []
    for day in sorted(groups, reverse=True)[:limit]:
        rows = groups[day]
        wins = sum(_f(t.get("pnl")) > 0 for t in rows)
        losses = sum(_f(t.get("pnl")) < 0 for t in rows)
        en, ar = _report_text(day, rows)
        result.append({
            "id": f"local-daily-{day}", "report_type": "daily",
            "report_date": day, "month": day[:7], "closed_trades": len(rows),
            "win_rate": wins / (wins + losses) * 100 if wins + losses else 0,
            "daily_pnl": round(sum(_f(t.get("pnl")) for t in rows), 1),
            "report_text": en, "report_text_en": en, "report_text_ar": ar,
        })
    return result


def _week_start(day: str) -> str:
    try:
        value = datetime.fromisoformat(day).date()
        return (value - timedelta(days=value.weekday())).isoformat()
    except Exception:
        return day


def _weekly_reports(closed: List[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in closed:
        day = str(row.get("closed_at") or row.get("created_at") or "")[:10]
        if day:
            groups[_week_start(day)].append(row)
    result = []
    for week in sorted(groups, reverse=True)[:limit]:
        rows = groups[week]
        wins = sum(_f(t.get("pnl")) > 0 for t in rows)
        losses = sum(_f(t.get("pnl")) < 0 for t in rows)
        en, ar = _report_text(week, rows, weekly=True)
        result.append({
            "id": f"local-weekly-{week}", "report_type": "weekly",
            "week_start": week, "month": week[:7], "closed_trades": len(rows),
            "win_rate": wins / (wins + losses) * 100 if wins + losses else 0,
            "net_pnl_points": round(sum(_f(t.get("pnl")) for t in rows), 1),
            "report_text": en, "report_text_en": en, "report_text_ar": ar,
        })
    return result


def _agent_performance(raw_closed: Iterable[Dict[str, Any]], weights: Dict[str, float]) -> List[Dict[str, Any]]:
    names = ("unified_trend", "classical", "smc", "price_action", "auction_flow")
    stats = {name: {"agent_name": name, "weight": _f(weights.get(name)),
                    "predictions": 0, "wins": 0, "losses": 0,
                    "net_pnl": 0.0, "confidence_sum": 0.0,
                    "source": "computed_from_vps_local_trades"} for name in names}
    for row in raw_closed:
        pnl = _pnl(row)
        side = str(row.get("type") or row.get("side") or "").upper()
        details = (_snapshot(row).get("agent_details") or {})
        if not isinstance(details, dict):
            continue
        for name in names:
            vote = details.get(name) or {}
            if not isinstance(vote, dict):
                continue
            direction = str(vote.get("direction") or vote.get("signal") or "").upper()
            confidence = _f(vote.get("confidence"))
            if direction not in {"BUY", "SELL"}:
                continue
            st = stats[name]
            st["predictions"] += 1
            st["confidence_sum"] += confidence
            correct = (direction == side and pnl > 0) or (direction != side and pnl < 0)
            st["wins" if correct else "losses"] += 1
            st["net_pnl"] += pnl if direction == side else -pnl
    result = []
    for st in stats.values():
        n = st["predictions"]
        st["total_predictions"] = n
        st["win_rate"] = st["wins"] / n * 100 if n else None
        st["avg_confidence"] = st.pop("confidence_sum") / n if n else 0
        st["net_pnl"] = round(st["net_pnl"], 1)
        result.append(st)
    return result


def build_dashboard_payload(root: str | Path, limit: int = 200) -> Dict[str, Any]:
    root = Path(root)
    rows = load_trades_checked(root / "storage" / "trades.json")
    raw_closed = [r for r in rows if str(r.get("status") or "").upper() in OUTCOME_STATUSES]
    raw_active = [r for r in rows if str(r.get("status") or "").upper() in LIVE_STATUSES | PENDING_STATUSES]
    raw_closed.sort(key=lambda r: _stamp(r, closed=True), reverse=True)
    raw_active.sort(key=_stamp, reverse=True)
    closed = [public_trade(r) for r in raw_closed[:max(20, min(int(limit), 500))]]
    active = [public_trade(r) for r in raw_active[:50]]
    live = [r for r in active if r["status"] in LIVE_STATUSES]
    pending = [r for r in active if r["status"] in PENDING_STATUSES]
    try:
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    except Exception:
        config = {}
    weights = {k: _f(v) for k, v in (config.get("agent_weights") or {}).items()
               if not str(k).startswith("_")}
    return {
        "ok": True, "source": "vps-local-json",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": _summary(closed, live, pending),
        "closedTrades": closed, "liveTrades": live,
        "pendingOrders": pending, "activeTrades": active,
        "dailyReports": _daily_reports(closed),
        "weeklyReports": _weekly_reports(closed),
        "agentPerformance": _agent_performance(raw_closed, weights),
        "agentWeights": [{"agent_name": k, "weight": v} for k, v in weights.items()],
    }
