from __future__ import annotations

from typing import Any, Dict, List

MAIN_ROLES = {"PRIMARY", "STARTER"}
ADD_ROLES = {"STANDBY", "ADD_ON"}
OPEN_STATUSES = {"OPEN", "PARTIAL", "TP1_HIT"}
PENDING_STATUSES = {"PENDING"}
CANCELLED_STATUSES = {"CANCELLED", "EXPIRED"}


def _snapshot(trade: Dict[str, Any]) -> Dict[str, Any]:
    snap = trade.get("signal_snapshot") or {}
    if isinstance(snap, str):
        try:
            import json
            snap = json.loads(snap)
        except Exception:
            snap = {}
    return snap if isinstance(snap, dict) else {}


def trade_role(trade: Dict[str, Any]) -> str:
    setup = (_snapshot(trade).get("setup_context") or {}) if isinstance(_snapshot(trade), dict) else {}
    if not isinstance(setup, dict):
        setup = {}
    return str(setup.get("pending_plan_role") or setup.get("selection_role") or "").upper()


def trade_leg_label(trade: Dict[str, Any]) -> str:
    setup = (_snapshot(trade).get("setup_context") or {}) if isinstance(_snapshot(trade), dict) else {}
    if not isinstance(setup, dict):
        setup = {}
    return str(setup.get("execution_leg_label") or "")


def trade_scenario_id(trade: Dict[str, Any]) -> str:
    snap = _snapshot(trade)
    plan = snap.get("session_plan") or {}
    setup = snap.get("setup_context") or {}
    if isinstance(plan, dict) and plan.get("scenario_id"):
        return str(plan.get("scenario_id"))
    if isinstance(setup, dict) and setup.get("scenario_id"):
        return str(setup.get("scenario_id"))
    return ""


def trade_pnl_points(trade: Dict[str, Any]) -> float:
    for key in ("final_pnl_points", "final_pnl", "current_pnl_points", "current_pnl", "pnl"):
        value = trade.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


def trade_status(trade: Dict[str, Any]) -> str:
    return str(trade.get("status") or "").upper()


def trade_outcome(trade: Dict[str, Any]) -> str:
    status = trade_status(trade)
    pnl = trade_pnl_points(trade)
    if status in PENDING_STATUSES:
        return "PENDING"
    if status in OPEN_STATUSES:
        if pnl > 0:
            return "OPEN_PROFIT"
        if pnl < 0:
            return "OPEN_LOSS"
        return "OPEN_FLAT"
    if status in CANCELLED_STATUSES:
        return "CANCELLED"
    if pnl > 0:
        return "WIN"
    if pnl < 0:
        return "LOSS"
    return "BREAKEVEN"


def trade_activated(trade: Dict[str, Any]) -> bool:
    return trade_outcome(trade) not in {"PENDING", "CANCELLED"}


def summarize_day_map_execution(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    role_breakdown: Dict[str, Dict[str, Any]] = {}
    scenarios: Dict[str, List[Dict[str, Any]]] = {}

    def _bucket(role: str) -> Dict[str, Any]:
        return role_breakdown.setdefault(role or "UNMAPPED", {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "open": 0,
            "pending": 0,
            "cancelled": 0,
            "net_pnl_points": 0.0,
        })

    for trade in trades or []:
        role = trade_role(trade)
        sid = trade_scenario_id(trade)
        if not role and not sid:
            continue
        bucket = _bucket(role)
        outcome = trade_outcome(trade)
        pnl = trade_pnl_points(trade)
        bucket["count"] += 1
        bucket["net_pnl_points"] += pnl
        if outcome in {"WIN", "OPEN_PROFIT"}:
            bucket["wins"] += 1
        elif outcome in {"LOSS", "OPEN_LOSS"}:
            bucket["losses"] += 1
        elif outcome == "BREAKEVEN":
            bucket["breakeven"] += 1
        elif outcome.startswith("OPEN"):
            bucket["open"] += 1
        elif outcome == "PENDING":
            bucket["pending"] += 1
        elif outcome == "CANCELLED":
            bucket["cancelled"] += 1
        scenarios.setdefault(sid or f"NO_SCENARIO::{trade.get('id')}", []).append(trade)

    for data in role_breakdown.values():
        data["net_pnl_points"] = round(float(data["net_pnl_points"]), 1)

    main_worked_count = 0
    add_needed_count = 0
    starter_survived_alone_count = 0
    day_map_failed_count = 0
    map_changed_cancelled_count = 0

    for scenario_trades in scenarios.values():
        activated_main = [t for t in scenario_trades if trade_role(t) in MAIN_ROLES and trade_activated(t)]
        activated_add = [t for t in scenario_trades if trade_role(t) in ADD_ROLES and trade_activated(t)]
        positive_any = any(trade_outcome(t) in {"WIN", "OPEN_PROFIT"} for t in scenario_trades)
        positive_main = any(trade_role(t) in MAIN_ROLES and trade_outcome(t) in {"WIN", "OPEN_PROFIT"} for t in scenario_trades)
        positive_starter = any(trade_role(t) == "STARTER" and trade_outcome(t) in {"WIN", "OPEN_PROFIT"} for t in scenario_trades)
        main_losses = [t for t in scenario_trades if trade_role(t) in MAIN_ROLES and trade_outcome(t) == "LOSS"]
        main_cancels = [t for t in scenario_trades if trade_role(t) in MAIN_ROLES and trade_outcome(t) == "CANCELLED"]
        if positive_main:
            main_worked_count += 1
        if activated_add:
            add_needed_count += 1
        if positive_starter and not activated_add:
            starter_survived_alone_count += 1
        if (main_losses or main_cancels) and not positive_any:
            day_map_failed_count += 1
        if main_cancels and not activated_add:
            map_changed_cancelled_count += 1

    return {
        "tracked_trade_count": sum(v["count"] for v in role_breakdown.values()),
        "scenario_count": len(scenarios),
        "role_breakdown": role_breakdown,
        "scenario_metrics": {
            "main_worked_count": main_worked_count,
            "add_needed_count": add_needed_count,
            "starter_survived_alone_count": starter_survived_alone_count,
            "day_map_failed_count": day_map_failed_count,
            "map_changed_cancelled_count": map_changed_cancelled_count,
        },
    }
