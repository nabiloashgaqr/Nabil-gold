"""Backfill Phase 5 enrichment columns for existing trades.

Usage:
    python scripts/backfill_trade_enrichment.py --dry-run
    python scripts/backfill_trade_enrichment.py

The script is intentionally non-destructive: it only fills enrichment columns
that are currently NULL/empty on each trade. It supports Supabase and the local
JSON fallback used by tests/manual dry-runs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import DatabaseService
from utils.helpers import calculate_pips, load_config, load_trades, save_trades, setup_logging
from utils.instruments import price_to_points

setup_logging()
logger = logging.getLogger(__name__)

ENRICHMENT_COLUMNS = (
    "planned_risk_points",
    "planned_tp2_points",
    "planned_rr",
    "session_label",
    "session_quality",
    "entry_day_of_week",
    "entry_hour_local",
    "news_status_at_entry",
    "news_risk_at_entry",
    "volatility_regime",
    "trend_strength",
    "daily_bias_at_entry",
)


def _is_missing(value: Any) -> bool:
    return value is None or value == ""


def _snapshot(trade: dict[str, Any]) -> dict[str, Any]:
    snap = trade.get("signal_snapshot") or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except Exception:  # noqa: BLE001
            snap = {}
    return snap if isinstance(snap, dict) else {}


def _nested_signal(trade: dict[str, Any]) -> dict[str, Any]:
    sig = _snapshot(trade).get("signal") or {}
    return sig if isinstance(sig, dict) else {}


def _entry_price(trade: dict[str, Any]) -> float:
    try:
        return float(trade.get("entry_price") or 0)
    except (TypeError, ValueError):
        return 0.0


def _stop_loss(trade: dict[str, Any]) -> float:
    for value in (trade.get("initial_stop_loss"), trade.get("stop_loss"), _nested_signal(trade).get("stop_loss")):
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return 0.0


def _tp2(trade: dict[str, Any]) -> float:
    for value in (trade.get("tp2"), _nested_signal(trade).get("tp2")):
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return 0.0


def _trade_time(trade: dict[str, Any], timezone_name: str) -> datetime | None:
    text = trade.get("entry_time") or trade.get("opened_at") or trade.get("created_at")
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(timezone_name))
    except Exception:  # noqa: BLE001
        return None


def _infer_session(dt: datetime | None) -> str | None:
    if not dt:
        return None
    h = dt.hour
    if 3 <= h < 10:
        return "Asia Morning"
    if 10 <= h < 15:
        return "London / Europe Midday"
    if 15 <= h < 19:
        return "London + New York Afternoon"
    if 19 <= h < 24:
        return "New York Evening"
    return "Late New York Night"


def _trade_pnl(trade: dict[str, Any]) -> float:
    for key in ("final_pnl_points", "final_pnl", "current_pnl_points", "current_pnl"):
        value = trade.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    try:
        typ = str(trade.get("type") or trade.get("trade_type") or "BUY").upper()
        entry = float(trade.get("entry_price") or 0)
        px = float(trade.get("close_price") or trade.get("current_price") or entry or 0)
        return calculate_pips(entry, px, typ, str(trade.get("symbol") or "XAU/USD"))
    except Exception:  # noqa: BLE001
        return 0.0


def compute_enrichment_updates(trade: dict[str, Any], timezone_name: str = "Asia/Hebron") -> dict[str, Any]:
    """Return only missing enrichment fields that can be inferred safely."""
    updates: dict[str, Any] = {}
    snap = _snapshot(trade)
    sig = _nested_signal(trade)
    symbol = str(trade.get("symbol") or sig.get("symbol") or "XAU/USD")
    entry = _entry_price(trade)
    sl = _stop_loss(trade)
    tp2 = _tp2(trade)

    if _is_missing(trade.get("planned_risk_points")) and entry and sl:
        updates["planned_risk_points"] = round(abs(price_to_points(entry - sl, symbol=symbol)), 1)
    if _is_missing(trade.get("planned_tp2_points")) and entry and tp2:
        updates["planned_tp2_points"] = round(abs(price_to_points(tp2 - entry, symbol=symbol)), 1)
    if _is_missing(trade.get("planned_rr")):
        rr = None
        for value in (sig.get("rr_ratio"), sig.get("tp2_rr")):
            try:
                if value is not None:
                    rr = float(value)
                    break
            except (TypeError, ValueError):
                pass
        risk = updates.get("planned_risk_points") or trade.get("planned_risk_points")
        reward = updates.get("planned_tp2_points") or trade.get("planned_tp2_points")
        try:
            if rr is None and float(risk or 0) > 0 and float(reward or 0) > 0:
                rr = float(reward) / float(risk)
        except (TypeError, ValueError):
            pass
        if rr is not None:
            updates["planned_rr"] = round(rr, 2)

    session_info = snap.get("session_info") or {}
    dt = _trade_time(trade, timezone_name)
    if _is_missing(trade.get("session_label")):
        label = session_info.get("current_session") or session_info.get("session") or session_info.get("session_name") or _infer_session(dt)
        if label:
            updates["session_label"] = label
    if _is_missing(trade.get("session_quality")):
        value = session_info.get("session_quality") or session_info.get("quality")
        if value:
            updates["session_quality"] = value
    if dt and _is_missing(trade.get("entry_day_of_week")):
        updates["entry_day_of_week"] = dt.strftime("%A")
    if dt and _is_missing(trade.get("entry_hour_local")):
        updates["entry_hour_local"] = int(dt.hour)

    news_context = snap.get("news_context") or {}
    rule = news_context.get("rule_based") or {}
    if _is_missing(trade.get("news_status_at_entry")):
        value = rule.get("market_status") or rule.get("status")
        if value:
            updates["news_status_at_entry"] = value
    if _is_missing(trade.get("news_risk_at_entry")):
        value = rule.get("risk_level") or rule.get("risk")
        if value:
            updates["news_risk_at_entry"] = value

    market_context = snap.get("market_context") or {}
    tech = market_context.get("technical_regime") or {}
    if _is_missing(trade.get("volatility_regime")) and tech.get("volatility_regime"):
        updates["volatility_regime"] = tech.get("volatility_regime")
    if _is_missing(trade.get("trend_strength")) and tech.get("trend_strength"):
        updates["trend_strength"] = tech.get("trend_strength")
    daily_bias = snap.get("daily_bias") or market_context.get("daily_bias") or {}
    if _is_missing(trade.get("daily_bias_at_entry")) and isinstance(daily_bias, dict) and daily_bias.get("bias"):
        updates["daily_bias_at_entry"] = daily_bias.get("bias")

    # If legacy rows have only final_pnl but no final_pnl_points, this is useful
    # for downstream reports and safe because project PnL is already stored in points.
    if _is_missing(trade.get("final_pnl_points")) and not _is_missing(trade.get("final_pnl")):
        try:
            updates["final_pnl_points"] = round(float(trade.get("final_pnl")), 1)
        except (TypeError, ValueError):
            pass
    elif _is_missing(trade.get("final_pnl_points")) and str(trade.get("status", "")).upper() not in {"OPEN", "TP1_HIT", "PARTIAL", "PENDING"}:
        updates["final_pnl_points"] = round(_trade_pnl(trade), 1)

    return {k: v for k, v in updates.items() if k in ENRICHMENT_COLUMNS or k == "final_pnl_points"}


def _fetch_all_trades(db: DatabaseService) -> list[dict[str, Any]]:
    if db.use_supabase and db.client is not None:
        response = db.client.table("trades").select("*").execute()
        return list(response.data or [])
    return load_trades(db.local_path)


def run_backfill(dry_run: bool = False, limit: int = 0) -> dict[str, int]:
    config = load_config()
    db = DatabaseService(config)
    timezone_name = str((config.get("schedule") or {}).get("timezone") or (config.get("trading_hours") or {}).get("timezone") or "Asia/Hebron")
    trades = _fetch_all_trades(db)
    if limit and limit > 0:
        trades = trades[:limit]

    scanned = updated = fields = 0
    local_changed = False
    for trade in trades:
        scanned += 1
        updates = compute_enrichment_updates(trade, timezone_name)
        if not updates:
            continue
        updated += 1
        fields += len(updates)
        trade_id = str(trade.get("id") or "")
        logger.info("%s enrichment fields for %s: %s", "Would update" if dry_run else "Updating", trade_id, ", ".join(sorted(updates)))
        if dry_run:
            continue
        if db.use_supabase and db.client is not None and trade_id:
            db.update_trade(trade_id, updates)
        else:
            trade.update(updates)
            local_changed = True

    if local_changed and not dry_run:
        save_trades(trades, db.local_path)
    return {"scanned": scanned, "updated": updated, "fields": fields}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill missing Phase 5 trade enrichment columns")
    parser.add_argument("--dry-run", action="store_true", help="show what would be updated without writing")
    parser.add_argument("--limit", type=int, default=0, help="optional max number of trades to scan")
    args = parser.parse_args()
    result = run_backfill(dry_run=args.dry_run, limit=args.limit)
    logger.info("✅ Backfill complete: scanned=%s updated=%s fields=%s dry_run=%s", result["scanned"], result["updated"], result["fields"], args.dry_run)


if __name__ == "__main__":
    main()
