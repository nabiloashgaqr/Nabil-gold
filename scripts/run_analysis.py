"""Main analysis script.

Runs every 5 minutes via cron-job.org/GitHub Actions. Fetches market data, runs agents,
يطبق إدارة المخاطر وDecision، ثم يحفظ ويرسل الإشارة إذا كانت مؤهلة.
"""

from __future__ import annotations

import logging
import os
import sys
import html
from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.classical_agent import ClassicalAgent
from agents.decision_agent import DecisionAgent
from agents.daily_bias_agent import DailyBiasAgent
from agents.multitimeframe_agent import MultiTimeframeAgent
from agents.macro_fundamental_agent import MacroFundamentalAgent
from agents.news_risk_agent import NewsRiskAgent
from agents.price_action_agent import PriceActionAgent
from agents.risk_management_agent import RiskManagementAgent
from agents.smc_agent import SMCAgent
from agents.technical_agent import TechnicalAgent
from services.market_snapshot import build_market_snapshot
from agents.trading_session_agent import TradingSessionAgent
from agents.open_trades_manager import OpenTradesManager
from services.database import DatabaseService
from services.dynamic_risk import DynamicRiskManager, should_block_signal
from services.market_data import MarketDataService
from services.telegram_bot import TelegramService, post_news_alert_sent, post_news_alert_record
from services.learning_service import get_learning_service
from services.llm_review import get_gemini_review_service
from services.pending_governor import PendingGovernor
from services.scenario_governor import ScenarioGovernor
from services.adaptive_execution import AdaptiveExecutionService
from services.directional_authority import DirectionalAuthorityService
from services.day_map_sanity import DayMapSanityService
from services.setup_memory import SetupMemoryService
from services.session_planner import SessionPlannerService
from utils.helpers import load_config, setup_logging, get_agent_weights
from utils.instruments import enabled_instruments, config_for_instrument, normalize_symbol, price_to_points, points_to_price

setup_logging()
logger = logging.getLogger(__name__)


def synthetic_timeframe_sources(data: Dict[str, Any]) -> list[str]:
    """Return timeframe/source names that are synthetic demo data."""
    synthetic: list[str] = []
    if data.get("source") == "synthetic_demo":
        synthetic.append(str(data.get("timeframe") or "primary"))
    for timeframe, payload in (data.get("timeframes", {}) or {}).items():
        if isinstance(payload, dict) and payload.get("source") == "synthetic_demo":
            name = str(timeframe)
            if name not in synthetic:
                synthetic.append(name)
    return synthetic


def _manual_status_enabled() -> bool:
    """Return True only when a human explicitly asks a workflow_dispatch run to
    send WAIT/status messages."""
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        return False
    return str(os.environ.get("SEND_STATUS_ON_MANUAL", "false")).strip().lower() in {"1", "true", "yes", "y"}


def should_send_status(config: Dict[str, Any]) -> bool:
    """Send blocked/no-signal messages only when configured."""
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return _manual_status_enabled()
    notif = config.get("notifications", {}) or {}
    return bool(notif.get("send_no_signal_updates", False)) or bool(notif.get("notify_on_blocked_signal", False))


def should_send_hourly_status(config: Dict[str, Any]) -> bool:
    """Send a clean market status update roughly once per hour."""
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return _manual_status_enabled()
    notif = config.get("notifications", {}) or {}
    if not (bool(notif.get("send_no_signal_updates", False)) or bool(notif.get("hourly_status", False))):
        return False
    now = datetime.now(timezone.utc)
    interval = int(notif.get("hourly_status_interval_minutes", 60) or 60)
    if interval <= 10:
        return True
    return now.minute < 10


def _parse_datetime(value: Any) -> datetime | None:
    """Parse common ISO timestamps safely as UTC-aware datetimes."""
    if not value:
        return None
    try:
        text = str(value).replace('Z', '+00:00')
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _trade_direction(trade: Dict[str, Any]) -> str:
    return str(trade.get('type') or trade.get('side') or trade.get('trade_type') or trade.get('decision') or '').upper()


def _trade_entry_price(trade: Dict[str, Any]) -> float | None:
    """Reference price for duplicate/cooldown logic.

    - OPEN trades should be compared by their original entry zone.
    - RECENTLY CLOSED trades should be compared by their close/exit zone first,
      because an immediate re-entry near the fresh exit area is the real
      duplicate/revenge-risk case. Using the old entry price let a trade entered
      at 4040 and trailed out at 3993 re-enter immediately at 3992 without any
      cooldown block, simply because 3992 was far from the original 4040 entry.
    """
    outcome = _trade_outcome(trade)
    keys = ('entry_price', 'current_price') if outcome == 'OPEN' else ('close_price', 'entry_price', 'current_price')
    for key in keys:
        value = trade.get(key)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


_OPEN_STATUSES = {"OPEN", "PARTIAL", "TP1_HIT", "PENDING"}
_LOSS_STATUSES = {"SL_HIT"}
_WIN_STATUSES = {"TP2_HIT"}
_BREAKEVEN_STATUSES = {"BE_HIT", "EXPIRED", "MANUAL_CLOSE"}


def _trade_outcome(trade: Dict[str, Any]) -> str:
    """Classify a trade as OPEN / WIN / LOSS / BREAKEVEN."""
    status = str(trade.get("status", "")).upper()
    if status in _OPEN_STATUSES:
        return "OPEN"
    result = str(trade.get("result", "") or "").upper()
    if result in {"WIN", "LOSS", "BREAKEVEN"}:
        return result
    for key in ("final_pnl", "final_pnl_points", "current_pnl", "current_pnl_points"):
        try:
            pnl = float(trade.get(key))
        except (TypeError, ValueError):
            continue
        if pnl > 0: return "WIN"
        if pnl < 0: return "LOSS"
        return "BREAKEVEN"
    if status in _LOSS_STATUSES: return "LOSS"
    if status in _WIN_STATUSES: return "WIN"
    if status in _BREAKEVEN_STATUSES: return "BREAKEVEN"
    return "BREAKEVEN"


def _trade_reference_time(trade: Dict[str, Any], now: datetime) -> datetime:
    closed = _parse_datetime(trade.get("closed_at") or trade.get("close_time"))
    if closed: return closed
    opened = _parse_datetime(trade.get("created_at") or trade.get("entry_time") or trade.get("opened_at"))
    return opened or now


_SETUP_STATE_RANK = {
    "DETECTED": 0,
    "SWEEP_CONFIRMED": 1,
    "POI_MARKED": 2,
    "ENTRY_ARMED": 3,
    "ENTRY_TRIGGERED": 4,
    "INVALIDATED": 5,
    "EXPIRED": 5,
}


def _trade_setup_context(trade: Dict[str, Any]) -> Dict[str, Any]:
    snap = trade.get("signal_snapshot") or {}
    if isinstance(snap, str):
        try:
            import json
            snap = json.loads(snap)
        except Exception:
            snap = {}
    if not isinstance(snap, dict):
        snap = {}
    setup = snap.get("setup_context") or trade.get("setup_context") or {}
    return dict(setup) if isinstance(setup, dict) else {}


def _setup_state_rank(value: Any) -> int:
    return _SETUP_STATE_RANK.get(str(value or "").upper(), -1)


def _setup_zone_midpoint(setup: Dict[str, Any]) -> float | None:
    zone = setup.get("poi_zone") or {}
    try:
        top = float(zone.get("top"))
        bottom = float(zone.get("bottom"))
        if top > 0 and bottom > 0:
            return (top + bottom) / 2.0
    except (TypeError, ValueError, AttributeError):
        pass
    try:
        high = float(setup.get("poi_high"))
        low = float(setup.get("poi_low"))
        if high > 0 and low > 0:
            return (high + low) / 2.0
    except (TypeError, ValueError):
        pass
    return None


def _setup_sweep_time(setup: Dict[str, Any]) -> datetime | None:
    details = setup.get("details") or {}
    if isinstance(details, dict):
        sweep = details.get("recent_sweep") or {}
        if isinstance(sweep, dict):
            return _parse_datetime(sweep.get("time"))
    return None


def _decision_scenario_id(decision: Dict[str, Any]) -> str:
    plan = decision.get("session_plan") or {}
    if isinstance(plan, dict) and plan.get("scenario_id"):
        return str(plan.get("scenario_id"))
    setup = decision.get("setup_context") or {}
    if isinstance(setup, dict) and setup.get("scenario_id"):
        return str(setup.get("scenario_id"))
    return ""



def _decision_ladder_role(decision: Dict[str, Any]) -> str:
    setup = decision.get("setup_context") or {}
    if isinstance(setup, dict) and setup.get("pending_plan_role"):
        return str(setup.get("pending_plan_role")).upper()
    if isinstance(setup, dict) and setup.get("selection_role"):
        return str(setup.get("selection_role")).upper()
    return ""



def _trade_session_plan(trade: Dict[str, Any]) -> Dict[str, Any]:
    snap = trade.get("signal_snapshot") or {}
    if isinstance(snap, str):
        try:
            import json
            snap = json.loads(snap)
        except Exception:
            snap = {}
    if not isinstance(snap, dict):
        snap = {}
    plan = snap.get("session_plan") or trade.get("session_plan") or {}
    return dict(plan) if isinstance(plan, dict) else {}



def _trade_scenario_id(trade: Dict[str, Any]) -> str:
    plan = _trade_session_plan(trade)
    if plan.get("scenario_id"):
        return str(plan.get("scenario_id"))
    setup = _trade_setup_context(trade)
    if setup.get("scenario_id"):
        return str(setup.get("scenario_id"))
    return ""



def _trade_ladder_role(trade: Dict[str, Any]) -> str:
    setup = _trade_setup_context(trade)
    if setup.get("pending_plan_role"):
        return str(setup.get("pending_plan_role")).upper()
    if setup.get("selection_role"):
        return str(setup.get("selection_role")).upper()
    return ""



def _ladder_sibling_allowed(decision: Dict[str, Any], trade: Dict[str, Any]) -> bool:
    current_sid = _decision_scenario_id(decision)
    existing_sid = _trade_scenario_id(trade)
    if not current_sid or current_sid != existing_sid:
        return False
    current_role = _decision_ladder_role(decision)
    existing_role = _trade_ladder_role(trade)
    if not current_role or not existing_role:
        return False
    return current_role != existing_role



def _plan_execution_hierarchy(plan: Dict[str, Any], role: str) -> Dict[str, Any]:
    manual_plan = (plan.get("manual_plan") or {}) if isinstance(plan, dict) else {}
    direction = str(plan.get("session_bias") or "").upper()
    side_word = "BUY" if direction == "BUY" else "SELL" if direction == "SELL" else "TRADE"
    main_label = str(manual_plan.get("main_area_label") or f"MAIN {side_word} AREA")
    add_label = str(manual_plan.get("add_area_label") or f"ADD {side_word} AREA")
    role = str(role or "PRIMARY").upper()
    if role == "PRIMARY":
        return {"execution_leg": "MAIN_AREA", "execution_leg_label": main_label, "execution_stage": "MAIN"}
    if role == "STANDBY":
        return {"execution_leg": "ADD_AREA", "execution_leg_label": add_label, "execution_stage": "ADD"}
    if role == "STARTER":
        return {"execution_leg": "STARTER", "execution_leg_label": f"STARTER inside {main_label}", "execution_stage": "MAIN"}
    if role == "ADD_ON":
        return {"execution_leg": "ADD_ON", "execution_leg_label": f"ADD-ON from {add_label}", "execution_stage": "ADD"}
    return {"execution_leg": role or "MAIN_AREA", "execution_leg_label": role or main_label, "execution_stage": "MAIN"}



def _planned_order_type(config: Dict[str, Any], direction: str, entry: float, current_price: float, symbol: str) -> str:
    oe = config.get("order_execution", {}) or {}
    entry_style = str(oe.get("entry_style", "market")).lower()
    if entry_style in {"market", "fixed_risk"}:
        return f"{direction}_MARKET"
    if entry_style == "hybrid":
        threshold = points_to_price(_safe_float(oe.get("market_threshold_points"), 30), symbol=symbol)
    else:
        threshold = points_to_price(_safe_float(oe.get("pending_threshold_points"), 20), symbol=symbol)
    if abs(entry - current_price) <= max(threshold, 0.01):
        return f"{direction}_MARKET"
    if direction == "BUY":
        return "BUY_LIMIT" if entry < current_price else "BUY_STOP"
    if direction == "SELL":
        return "SELL_LIMIT" if entry > current_price else "SELL_STOP"
    return "UNKNOWN"



def _plan_targets(direction: str, entry_price: float, stop_loss: float, target_price: float) -> tuple[float, float, float]:
    risk = abs(stop_loss - entry_price)
    reward = abs(target_price - entry_price)
    if risk <= 0 or reward <= 0:
        tp1 = target_price
        tp2 = target_price
        rr = 0.0
        return round(tp1, 2), round(tp2, 2), rr
    one_r = risk
    half_reward = reward * 0.5
    tp1_dist = min(max(one_r, reward * 0.35), half_reward if half_reward > 0 else one_r)
    if direction == "BUY":
        tp1 = entry_price + tp1_dist
        tp2 = target_price
    else:
        tp1 = entry_price - tp1_dist
        tp2 = target_price
    rr = reward / risk if risk > 0 else 0.0
    return round(tp1, 2), round(tp2, 2), round(rr, 2)



def _planner_trade_levels(
    config: Dict[str, Any],
    *,
    direction: str,
    entry_price: float,
    stop_loss: float,
    target_price: float,
    symbol: str,
) -> Dict[str, Any]:
    risk_cfg = (config.get("risk_settings") or {}) if isinstance(config, dict) else {}
    min_sl_points = _safe_float(risk_cfg.get("min_sl_distance_points"), 0.0)
    min_sl_distance = points_to_price(min_sl_points, symbol=symbol) if min_sl_points > 0 else 0.0
    max_rr = _safe_float(risk_cfg.get("max_rr_ratio"), 0.0)
    target_method = "mapped_target"
    floor_applied = False

    adjusted_stop = float(stop_loss)
    if min_sl_distance > 0 and abs(entry_price - adjusted_stop) < min_sl_distance:
        adjusted_stop = entry_price - min_sl_distance if direction == "BUY" else entry_price + min_sl_distance
        sl_mult = _safe_float(risk_cfg.get("atr_multiplier_sl"), 2.0) or 2.0
        tp1_ratio = (_safe_float(risk_cfg.get("atr_multiplier_tp1"), 2.5) or 2.5) / sl_mult
        tp2_ratio = (_safe_float(risk_cfg.get("atr_multiplier_tp2"), 4.5) or 4.5) / sl_mult
        if direction == "BUY":
            tp1 = entry_price + min_sl_distance * tp1_ratio
            tp2 = entry_price + min_sl_distance * tp2_ratio
        else:
            tp1 = entry_price - min_sl_distance * tp1_ratio
            tp2 = entry_price - min_sl_distance * tp2_ratio
        floor_applied = True
        target_method = "rr_from_floored_sl"
    else:
        tp1, tp2, _ = _plan_targets(direction, entry_price, adjusted_stop, target_price)

    risk = abs(adjusted_stop - entry_price)
    if max_rr > 0 and risk > 0:
        max_tp2_distance = risk * max_rr
        if direction == "BUY" and tp2 - entry_price > max_tp2_distance:
            tp2 = entry_price + max_tp2_distance
            target_method += "+max_rr_cap"
        elif direction == "SELL" and entry_price - tp2 > max_tp2_distance:
            tp2 = entry_price - max_tp2_distance
            target_method += "+max_rr_cap"

    rr = abs(tp2 - entry_price) / risk if risk > 0 else 0.0
    return {
        "entry_price": round(entry_price, 2),
        "stop_loss": round(adjusted_stop, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "rr": round(rr, 2),
        "floor_applied": floor_applied,
        "target_method": target_method,
        "min_sl_distance_points": round(min_sl_points, 1),
    }



def _candidate_zone_bounds(candidate: Dict[str, Any]) -> tuple[float, float] | None:
    zone = candidate.get("poi_zone") or {}
    if isinstance(zone, dict) and zone.get("top") is not None and zone.get("bottom") is not None:
        low = _safe_float(zone.get("bottom"), 0.0)
        high = _safe_float(zone.get("top"), 0.0)
        if low > 0 and high > 0:
            return min(low, high), max(low, high)
    low = _safe_float(candidate.get("poi_low"), 0.0)
    high = _safe_float(candidate.get("poi_high"), 0.0)
    if low > 0 and high > 0:
        return min(low, high), max(low, high)
    return None



def _zone_progress_pct(direction: str, current_price: float, low: float, high: float) -> float:
    width = max(high - low, 0.0001)
    if direction == "SELL":
        return max(0.0, min(100.0, ((current_price - low) / width) * 100.0))
    return max(0.0, min(100.0, ((high - current_price) / width) * 100.0))



def _split_execution_cfg(config: Dict[str, Any]) -> Dict[str, Any]:
    return (config.get("split_execution") or {}) if isinstance(config, dict) else {}



def _build_plan_ladder_decision(
    base_decision: Dict[str, Any],
    plan: Dict[str, Any],
    candidate: Dict[str, Any],
    config: Dict[str, Any],
    *,
    force_market: bool = False,
    role_override: str | None = None,
    entry_price_override: float | None = None,
    risk_share: float | None = None,
    basis_override: str | None = None,
) -> Dict[str, Any] | None:
    direction = str(plan.get("session_bias") or candidate.get("direction") or "").upper()
    symbol = str(plan.get("symbol") or base_decision.get("symbol") or config.get("symbol", "XAU/USD"))
    if direction not in {"BUY", "SELL"}:
        return None
    entry_price = _safe_float(entry_price_override if entry_price_override is not None else candidate.get("entry_price"), 0.0)
    stop_loss = _safe_float(candidate.get("stop_loss"), 0.0)
    target_price = _safe_float(candidate.get("target_price") or candidate.get("target_liquidity"), 0.0)
    current_price = _safe_float(base_decision.get("current_price"), 0.0)
    if entry_price <= 0 or stop_loss <= 0 or target_price <= 0 or current_price <= 0:
        return None

    levels = _planner_trade_levels(
        config,
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_price=target_price,
        symbol=symbol,
    )
    stop_loss = levels["stop_loss"]
    order_type = f"{direction}_MARKET" if force_market else _planned_order_type(config, direction, entry_price, current_price, symbol)
    if order_type.endswith("MARKET") and not force_market:
        return None
    entry_kind = "MARKET" if force_market else order_type.split("_")[-1]
    zone = candidate.get("poi_zone") or {}
    if isinstance(zone, dict) and zone.get("top") is not None and zone.get("bottom") is not None:
        low = min(_safe_float(zone.get("top"), entry_price), _safe_float(zone.get("bottom"), entry_price))
        high = max(_safe_float(zone.get("top"), entry_price), _safe_float(zone.get("bottom"), entry_price))
    else:
        low = _safe_float(candidate.get("poi_low"), entry_price)
        high = _safe_float(candidate.get("poi_high"), entry_price)
        if low <= 0 or high <= 0:
            low = high = entry_price
    tp1 = levels["tp1"]
    tp2 = levels["tp2"]
    rr = levels["rr"]
    role = str(role_override or candidate.get("selection_role") or "PRIMARY").upper()
    hierarchy = _plan_execution_hierarchy(plan, role)

    decision = deepcopy(base_decision)
    decision.update(
        {
            "decision": direction,
            "symbol": symbol,
            "confidence": max(_safe_float(plan.get("planner_confidence"), 0.0), _safe_float(candidate.get("thesis_dominance_score"), 0.0)),
            "entry_mode": "session_plan_ladder_market" if force_market else "session_plan_ladder",
            "entry_path": 3,
            "reasons": [
                f"Session plan {plan.get('scenario_type')} ({role})",
                f"Execution leg: {hierarchy.get('execution_leg_label')}",
                *([f"Planner SL floored to {levels.get('min_sl_distance_points', 0):.0f} points minimum risk distance."] if levels.get("floor_applied") else []),
                f"Morning/session planner prepared this pending thesis before the move.",
            ],
            "quality": {
                "grade": plan.get("planner_grade") or candidate.get("quality_grade") or "B",
                "score": max(_safe_float(plan.get("planner_confidence"), 0.0), _safe_float(candidate.get("quality_score"), 0.0)),
            },
            "session_plan": deepcopy(plan),
        }
    )
    setup_context = deepcopy(candidate)
    setup_context.update(
        {
            "scenario_id": plan.get("scenario_id"),
            "plan_id": plan.get("plan_id"),
            "pending_plan_role": role,
            "selection_role": role,
            "execution_leg": hierarchy.get("execution_leg"),
            "execution_leg_label": hierarchy.get("execution_leg_label"),
            "execution_stage": hierarchy.get("execution_stage"),
        }
    )
    decision["setup_context"] = setup_context
    decision["setup_id"] = setup_context.get("id")
    decision["setup_type"] = setup_context.get("setup_type")
    decision["setup_state"] = setup_context.get("setup_state")
    decision["lead_agent"] = setup_context.get("lead_agent")
    decision["setup_quality"] = setup_context.get("quality_grade") or candidate.get("quality_grade")
    position_size = {}
    if risk_share is not None:
        position_size["scenario_risk_share"] = round(float(risk_share), 3)
    decision["signal"] = {
        "type": direction,
        "entry": {
            "price": round(entry_price, 2),
            "low": round(current_price if force_market else low, 2),
            "high": round(current_price if force_market else high, 2),
            "kind": entry_kind,
            "order_type": order_type,
            "basis": basis_override or f"{hierarchy.get('execution_leg_label')} · session plan",
            "current_price": round(current_price, 2),
            "distance_points": 0.0 if force_market else abs(price_to_points(entry_price - current_price, symbol=symbol)),
        },
        "stop_loss": round(stop_loss, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "rr_ratio": rr,
        "tp1_rr": round(abs(tp1 - entry_price) / max(abs(stop_loss - entry_price), 0.01), 2),
        "tp2_rr": rr,
        "order_type": order_type,
        "entry_kind": entry_kind,
        "position_size": position_size,
        "risk_summary": f"Session planner {hierarchy.get('execution_leg_label')} {'market' if force_market else 'pending'} · {levels.get('target_method')}",
        "execution_leg": hierarchy.get("execution_leg"),
        "execution_leg_label": hierarchy.get("execution_leg_label"),
        "target_method": levels.get("target_method"),
    }
    decision["execution_leg"] = hierarchy.get("execution_leg")
    decision["execution_leg_label"] = hierarchy.get("execution_leg_label")
    return decision



def _planner_execution_gate(decision: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    side = str(decision.get("decision") or "").upper()
    if side not in {"BUY", "SELL"}:
        return {"allow": False, "reason": "no approved directional admission"}

    sig_cfg = (config.get("signal_requirements") or {}) if isinstance(config, dict) else {}
    min_agents = int(sig_cfg.get("min_agents_agree", 3) or 3)
    min_agent_conf = float(sig_cfg.get("agent_min_confidence", 70) or 70)
    details = decision.get("agent_details") or {}
    support_count = 0
    for key in ["technical", "classical", "smc", "price_action", "multitimeframe"]:
        detail = (details or {}).get(key)
        if not isinstance(detail, dict):
            continue
        direction = str(detail.get("direction") or "WAIT").upper()
        confidence = _safe_float(detail.get("confidence"), 0.0)
        if direction == side and confidence >= min_agent_conf:
            support_count += 1

    if support_count >= min_agents:
        return {
            "allow": True,
            "kind": "THREE_AGENT_ADMISSION",
            "support_count": support_count,
            "reason": f"{support_count} qualified agents aligned with the mapped direction",
        }

    confirm_source = str(decision.get("confirm_source") or "").lower()
    confirm_conf = _safe_float(decision.get("confirm_confidence"), 0.0)
    if support_count >= 2 and confirm_source in {"macro", "gemini"}:
        return {
            "allow": True,
            "kind": "TWO_AGENT_CONFIRMED_ADMISSION",
            "support_count": support_count,
            "confirm_source": confirm_source,
            "confirm_confidence": round(confirm_conf, 1),
            "reason": f"{support_count} qualified agents + {confirm_source} confirmation",
        }

    return {
        "allow": False,
        "support_count": support_count,
        "reason": f"planner execution requires 3 qualified agents or 2 agents + macro/gemini; got {support_count}",
    }


def _split_execution_decisions(
    base_decision: Dict[str, Any],
    plan: Dict[str, Any],
    primary: Dict[str, Any],
    standby: Dict[str, Any] | None,
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    split_cfg = _split_execution_cfg(config)
    if not bool(split_cfg.get("enabled", True)):
        return []
    if not bool(plan.get("extreme_poi", False)):
        return []
    if str(plan.get("execution_preference") or "").upper() != "SPLIT_EXECUTION_WATCH":
        return []
    direction = str(plan.get("session_bias") or primary.get("direction") or "").upper()
    current_price = _safe_float(base_decision.get("current_price"), 0.0)
    zone = _candidate_zone_bounds(primary)
    if direction not in {"BUY", "SELL"} or current_price <= 0 or not zone:
        return []
    low, high = zone
    if not (low <= current_price <= high):
        return []
    zone_progress = _zone_progress_pct(direction, current_price, low, high)
    starter_max_progress = float(split_cfg.get("starter_max_zone_progress_pct", 45) or 45)
    if zone_progress > starter_max_progress:
        return []
    starter_share = float(split_cfg.get("starter_risk_share", 0.4) or 0.4)
    addon_share = float(split_cfg.get("add_on_risk_share", max(0.0, 1.0 - starter_share)) or max(0.0, 1.0 - starter_share))
    starter = _build_plan_ladder_decision(
        base_decision,
        plan,
        primary,
        config,
        force_market=True,
        role_override="STARTER",
        entry_price_override=current_price,
        risk_share=starter_share,
        basis_override="Extreme POI starter market execution",
    )
    if not starter:
        return []
    if isinstance(standby, dict) and standby:
        addon_candidate = standby
    else:
        addon_candidate = deepcopy(primary)
        addon_candidate["selection_role"] = "ADD_ON"
        addon_zone = _candidate_zone_bounds(primary)
        if addon_zone:
            low_z, high_z = addon_zone
            if direction == "SELL":
                addon_candidate["entry_price"] = round(low_z + (high_z - low_z) * 0.5, 2)
            else:
                addon_candidate["entry_price"] = round(high_z - (high_z - low_z) * 0.5, 2)
    addon = _build_plan_ladder_decision(
        base_decision,
        plan,
        addon_candidate,
        config,
        role_override="ADD_ON",
        risk_share=addon_share,
        basis_override="Extreme POI add-on pending",
    )
    return [starter] + ([addon] if addon else [])



def _execute_session_plan_ladder(
    base_decision: Dict[str, Any],
    all_results: Dict[str, Any],
    open_trades: List[Dict[str, Any]],
    database: DatabaseService,
    telegram: TelegramService,
    config: Dict[str, Any],
) -> int:
    planner_cfg = (config.get("session_planner") or {}) if isinstance(config, dict) else {}
    if not bool(planner_cfg.get("create_pending_orders_from_plan", True)):
        return 0
    plan = base_decision.get("session_plan") or {}
    if not isinstance(plan, dict) or not plan.get("plan_ready"):
        return 0
    gate = _planner_execution_gate(base_decision, config)
    if not gate.get("allow"):
        logger.info("Session-plan ladder blocked: %s", gate.get("reason"))
        return 0
    symbol = str(base_decision.get("symbol") or plan.get("symbol") or config.get("symbol", "XAU/USD"))
    normalized_symbol = normalize_symbol(symbol)
    symbol_open_trades = [t for t in (open_trades or []) if normalize_symbol(t.get("symbol") or symbol) == normalized_symbol]
    scenario_review = ScenarioGovernor(config).review_new_plan(plan, symbol_open_trades, database=database)
    if scenario_review.get("action") == "KEEP_EXISTING_FAMILY":
        logger.info("Session-plan family kept for %s: %s", symbol, scenario_review.get("reason"))
        return 0
    if scenario_review.get("action") == "REPLACE_PENDING_FAMILY":
        logger.info("Session-plan family replaced for %s: %s", symbol, scenario_review.get("reason"))
        try:
            telegram.send_scenario_governance(scenario_review, symbol=symbol, side=str(plan.get("session_bias") or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to send scenario family replacement message: %s", exc)
        symbol_open_trades = [
            t for t in symbol_open_trades
            if str(t.get("status") or "").upper() != "PENDING"
        ]
    if any(str(t.get("status") or "").upper() in {"OPEN", "PARTIAL", "TP1_HIT"} for t in symbol_open_trades):
        return 0

    primary = plan.get("primary_poi") or {}
    standby = plan.get("standby_poi") or {}
    if not isinstance(primary, dict) or not primary:
        return 0

    split_decisions = _split_execution_decisions(base_decision, plan, primary, standby if isinstance(standby, dict) else None, config)
    if split_decisions:
        plan_decisions = split_decisions
    else:
        primary_decision = _build_plan_ladder_decision(base_decision, plan, primary, config)
        if not primary_decision:
            return 0
        plan_decisions = [primary_decision] + ([ _build_plan_ladder_decision(base_decision, plan, standby, config) ] if isinstance(standby, dict) and standby else [])

    created = 0
    staged_trades = list(symbol_open_trades)
    for ladder_decision in plan_decisions:
        if not ladder_decision:
            continue
        ladder_decision["planner_execution_gate"] = deepcopy(gate)
        ladder_decision.setdefault("reasons", []).append(f"Planner admission: {gate.get('reason')}")
        role = _decision_ladder_role(ladder_decision)
        if any(_trade_scenario_id(t) == _decision_scenario_id(ladder_decision) and _trade_ladder_role(t) == role for t in staged_trades):
            continue
        duplicate_reason = duplicate_signal_reason(ladder_decision, database, config)
        if duplicate_reason:
            logger.info("Session-plan ladder %s blocked for %s: %s", role, symbol, duplicate_reason)
            if role in {"PRIMARY", "STARTER"}:
                return created
            continue
        trade_id = database.new_trade_id()
        ladder_decision["trade_id"] = trade_id
        delivered = False
        try:
            delivered = bool(telegram.send_signal(ladder_decision))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to send session-plan ladder signal (%s) for %s: %s", role, symbol, exc)
            delivered = False
        if not delivered:
            if role in {"PRIMARY", "STARTER"}:
                return created
            continue
        database.save_trade(ladder_decision)
        staged_trades.append(
            {
                "id": trade_id,
                "symbol": symbol,
                "type": ladder_decision.get("decision"),
                "status": "OPEN" if str(((ladder_decision.get("signal") or {}).get("order_type") or "")).endswith("MARKET") else "PENDING",
                "entry_price": ((ladder_decision.get("signal") or {}).get("entry") or {}).get("price"),
                "signal_snapshot": ladder_decision,
            }
        )
        created += 1
    return created



def _post_exit_revalidation_review(
    decision: Dict[str, Any],
    closed_trade: Dict[str, Any],
    config: Dict[str, Any],
    *,
    now: datetime,
    symbol: str,
) -> Dict[str, Any]:
    """Allow same-zone re-entry only when a materially new thesis appears.

    Manual-analyst intent:
    - do NOT re-enter just because the previous trade closed;
    - do allow a fresh same-direction entry when there is genuinely new setup
      evidence: a different POI, a stronger setup-state progression, or a fresh
      sweep/displacement event after the previous exit.
    """
    cfg = (config.get("post_exit_revalidation") or {}) if isinstance(config, dict) else {}
    if cfg.get("enabled", True) is False:
        return {"allow": False, "reason": "post-exit revalidation is disabled"}

    new_setup = decision.get("setup_context") or {}
    old_setup = _trade_setup_context(closed_trade)
    if not isinstance(new_setup, dict) or not new_setup:
        return {"allow": False, "reason": "new signal has no rich setup context"}
    if not old_setup:
        return {"allow": False, "reason": "previous trade has no setup context to prove a new thesis"}

    new_key = str(new_setup.get("state_key") or "")
    old_key = str(old_setup.get("state_key") or "")
    new_type = str(new_setup.get("setup_type") or "")
    old_type = str(old_setup.get("setup_type") or "")
    new_poi = str(new_setup.get("poi_type") or "")
    old_poi = str(old_setup.get("poi_type") or "")

    zone_shift_pts = 0.0
    new_mid = _setup_zone_midpoint(new_setup)
    old_mid = _setup_zone_midpoint(old_setup)
    if new_mid is not None and old_mid is not None:
        zone_shift_pts = abs(price_to_points(new_mid - old_mid, symbol=symbol))
    new_poi_min_distance_points = float(cfg.get("new_poi_min_distance_points", 80) or 80)
    different_poi = bool(
        new_key and old_key and new_key != old_key and (
            zone_shift_pts >= new_poi_min_distance_points or new_type != old_type or new_poi != old_poi
        )
    )

    old_state_rank = _setup_state_rank(old_setup.get("setup_state"))
    new_state_rank = _setup_state_rank(new_setup.get("setup_state"))
    min_state_progress_steps = int(cfg.get("min_state_progress_steps", 1) or 1)
    state_progressed = new_state_rank >= old_state_rank + min_state_progress_steps

    old_trigger_score = _safe_float(old_setup.get("trigger_score"), 0.0)
    new_trigger_score = _safe_float(new_setup.get("trigger_score"), 0.0)
    min_trigger_score_improvement = float(cfg.get("min_trigger_score_improvement", 8) or 8)
    trigger_improved = new_trigger_score >= old_trigger_score + min_trigger_score_improvement
    new_trigger_state = str(new_setup.get("trigger_state") or "").upper()
    old_trigger_state = str(old_setup.get("trigger_state") or "").upper()
    rejection_upgrade = new_trigger_state == "REJECTION_CONFIRMED" and old_trigger_state != "REJECTION_CONFIRMED"

    old_disp = _safe_float(old_setup.get("displacement_score"), 0.0)
    new_disp = _safe_float(new_setup.get("displacement_score"), 0.0)
    min_displacement_improvement = float(cfg.get("min_displacement_improvement", 5) or 5)
    displacement_improved = new_disp >= old_disp + min_displacement_improvement

    exit_time = _trade_reference_time(closed_trade, now)
    new_sweep_time = _setup_sweep_time(new_setup)
    old_sweep_time = _setup_sweep_time(old_setup)
    fresh_sweep_after_exit = bool(new_sweep_time and new_sweep_time > exit_time and (old_sweep_time is None or new_sweep_time > old_sweep_time))

    old_dom = _safe_float(old_setup.get("thesis_dominance_score"), 0.0)
    new_dom = _safe_float(new_setup.get("thesis_dominance_score"), 0.0)
    min_dominance_improvement = float(cfg.get("min_dominance_improvement", 6) or 6)
    dominance_improved = new_dom >= old_dom + min_dominance_improvement

    if different_poi:
        return {
            "allow": True,
            "reason": f"new POI / state_key detected (zone shift {zone_shift_pts:.0f} pts)",
        }
    if state_progressed and (trigger_improved or rejection_upgrade or dominance_improved):
        return {
            "allow": True,
            "reason": "setup state progressed with stronger trigger / thesis quality",
        }
    if fresh_sweep_after_exit and (displacement_improved or rejection_upgrade or dominance_improved):
        return {
            "allow": True,
            "reason": "fresh post-exit sweep / displacement created a new same-direction thesis",
        }

    blockers = []
    if not different_poi:
        blockers.append("no materially new POI")
    if not state_progressed:
        blockers.append("no stronger setup-state progression")
    if not fresh_sweep_after_exit:
        blockers.append("no fresh sweep after the previous exit")
    if not (trigger_improved or rejection_upgrade):
        blockers.append("trigger did not improve enough")
    if not dominance_improved:
        blockers.append("thesis dominance did not improve enough")
    return {"allow": False, "reason": "; ".join(blockers[:3])}


def duplicate_signal_reason(decision: Dict[str, Any], database: DatabaseService, config: Dict[str, Any]) -> str | None:
    filt = config.get('duplicate_signal_filter', {}) or {}
    if not filt.get('enabled', True): return None
    direction = str(decision.get('decision', '')).upper()
    if direction not in {'BUY', 'SELL'}: return None
    signal = decision.get('signal', {}) or {}
    entry = signal.get('entry', {}) or {}
    try:
        entry_price = float(entry.get('price') or decision.get('current_price') or 0)
    except (TypeError, ValueError):
        entry_price = 0.0
    if entry_price <= 0: return None
    now = datetime.now(timezone.utc)
    price_zone_points = float(filt.get('price_zone_points', filt.get('same_direction_price_zone_points', 50)))
    open_cfg = filt.get('open_trade', {}) or {}
    block_open_any_price = bool(open_cfg.get('block_same_direction_any_price', filt.get('block_if_open_same_direction', False)))
    block_open_in_zone = bool(open_cfg.get('block_same_direction_in_zone', True))
    max_open_same_direction = int(open_cfg.get('max_open_same_direction', filt.get('max_open_same_direction', 3)))
    cooldown_cfg = filt.get('cooldown', {}) or {}
    legacy_cooldown = float(filt.get('lookback_minutes', 90))
    cooldown_after_loss = float(cooldown_cfg.get('after_loss_minutes', legacy_cooldown))
    cooldown_after_breakeven = float(cooldown_cfg.get('after_breakeven_minutes', max(legacy_cooldown * 0.5, 30)))
    cooldown_after_win = float(cooldown_cfg.get('after_win_minutes', max(legacy_cooldown * 0.33, 20)))
    lookback_hours = float(cooldown_cfg.get('lookback_hours', 6))
    symbol = str(decision.get("symbol") or (decision.get("signal", {}) or {}).get("symbol") or config.get("symbol", "XAU/USD"))

    def _points_away(prev_price: float) -> float:
        return abs(price_to_points(entry_price - prev_price, symbol=symbol))

    candidates: List[Dict[str, Any]] = []
    seen_ids: set = set()

    def _add(trade: Dict[str, Any]) -> None:
        trade_symbol = str(trade.get('symbol') or config.get('symbol', 'XAU/USD')).upper()
        if trade_symbol != str(symbol).upper(): return
        tid = str(trade.get('id', ''))
        if tid and tid in seen_ids: return
        if tid: seen_ids.add(tid)
        candidates.append(trade)

    for trade in database.get_open_trades():
        if _trade_direction(trade) == direction: _add(trade)
    for trade in database.get_recent_trades(limit=50):
        if _trade_direction(trade) == direction: _add(trade)

    if max_open_same_direction > 0:
        open_same_direction = [t for t in candidates if _trade_outcome(t) == "OPEN"]
        if len(open_same_direction) >= max_open_same_direction:
            return f"Same-direction exposure cap: {len(open_same_direction)} open {direction} trade(s) already exist, blocking another {direction}."

    for trade in candidates:
        if _trade_outcome(trade) == "OPEN":
            prev_entry = _trade_entry_price(trade)
            if prev_entry is None: continue
            if _ladder_sibling_allowed(decision, trade):
                continue
            if block_open_any_price: return f"Duplicate {direction} blocked: one position per direction."
            if block_open_in_zone:
                pts = _points_away(prev_entry)
                if pts <= price_zone_points: return f"Duplicate {direction} blocked: already open in same price zone."
        else:
            outcome = _trade_outcome(trade)
            prev_entry = _trade_entry_price(trade)
            if prev_entry is None: continue
            ref_time = _trade_reference_time(trade, now)
            age_minutes = (now - ref_time).total_seconds() / 60.0
            if age_minutes > lookback_hours * 60.0: continue
            pts = _points_away(prev_entry)
            if pts > price_zone_points: continue
            cooldown = {"LOSS": cooldown_after_loss, "WIN": cooldown_after_win}.get(outcome, cooldown_after_breakeven)
            if age_minutes <= cooldown:
                review = _post_exit_revalidation_review(decision, trade, config, now=now, symbol=symbol)
                if review.get("allow"):
                    continue
                detail = str(review.get("reason") or "").strip()
                suffix = f" Revalidation: {detail}." if detail else ""
                return f"Post-exit revalidation blocked: recently closed {outcome} trade in same zone.{suffix}"
    return None


def _dedupe_warnings(warnings: list) -> list:
    seen: set = set()
    result: list = []
    news_block_kept = False
    for w in warnings:
        text = str(w).strip()
        if not text: continue
        key = " ".join(text.lower().split())
        if key in seen: continue
        lower = text.lower()
        if lower.startswith("news blocked") or lower.startswith("ai news blocked"):
            if news_block_kept: continue
            news_block_kept = True
        seen.add(key)
        result.append(text)
    return result


def _safe_float(value: Any, default: float = 0.0) -> float:
    try: return float(value)
    except (TypeError, ValueError): return default


def _levels_from_results(all_results: Dict[str, Any], side: str) -> List[float]:
    """Extract support/resistance levels relevant for a fixed-risk scale-in.

    BUY scale-ins are considered near support below/around price; SELL scale-ins
    are considered near resistance above/around price.  The project has used a
    few different key names over time, so this helper accepts the common shapes
    without raising when an agent omits a section.
    """
    side = str(side or "").upper()
    wanted_keys = (
        ("support_levels", "supports", "support")
        if side == "BUY"
        else ("resistance_levels", "resistances", "resistance")
    )
    levels: List[float] = []
    for section_name in ("classical", "smc", "price_action", "technical"):
        section = all_results.get(section_name, {}) or {}
        for key in wanted_keys:
            raw = section.get(key)
            if raw is None:
                continue
            if not isinstance(raw, list):
                raw = [raw]
            for item in raw:
                value = item.get("price") if isinstance(item, dict) else item
                price = _safe_float(value, 0.0)
                if price > 0:
                    levels.append(price)
    return levels


def _scale_in_count_for_parent(open_trades: List[Dict[str, Any]], parent_id: str) -> int:
    """Count already-open scale-ins for a parent trade from known schema shapes."""
    count = 0
    for trade in open_trades:
        signal = trade.get("signal") or (trade.get("signal_snapshot", {}) or {}).get("signal", {}) or {}
        if not isinstance(signal, dict):
            signal = {}
        if bool(signal.get("scale_in") or trade.get("scale_in")) and str(
            signal.get("parent_trade_id") or trade.get("parent_trade_id") or ""
        ) == str(parent_id):
            count += 1
    return count


async def _check_scale_in(
    config: Dict[str, Any],
    all_results: Dict[str, Any],
    open_trades: List[Dict[str, Any]],
    database: DatabaseService,
    telegram: TelegramService,
) -> None:
    """Send and persist fixed-risk scale-in trades when price retests a level.

    Scale-in is treated as a NEW signal decision, requiring:
    1. Pullback of ≥ trigger_points (200) from parent entry (better price only)
    2. Full agent consensus: ≥ min_agents_agree (3) qualified agents agree
    3. Net weighted confidence ≥ min_consensus_confidence (72%)
    4. No opposition (agents opposing the direction reduce confidence)
    5. All risk filters pass (news, trading hours, etc.)

    This prevents adding to a losing position blindly — scale-in must confirm
    the market still supports the original direction with fresh agent votes.
    """
    oe = config.get("order_execution", {}) or {}
    fr = oe.get("fixed_risk", {}) or {}
    if str(oe.get("entry_style", "")).lower() != "fixed_risk":
        return
    if not bool(fr.get("scale_in_enabled", False)):
        return
    if _is_news_hard_block({}, all_results):
        return

    current_price = _safe_float(all_results.get("current_price"), 0.0)
    if current_price <= 0:
        return
    symbol = str(all_results.get("symbol") or config.get("symbol") or "XAU/USD")
    trigger_points = float(fr.get("scale_in_trigger_points", 200) or 200)
    max_scale_ins = int(fr.get("scale_in_max", 2) or 2)
    if max_scale_ins <= 0:
        return

    # Respect the same-direction cap: count all open trades in this direction
    # (parents + scale-ins). If already at the limit, no more scale-ins.
    max_open_same_dir = int(
        (config.get("duplicate_signal_filter", {}) or {})
        .get("open_trade", {})
        .get("max_open_same_direction", 3)
    )

    for parent in open_trades:
        parent_id = str(parent.get("id") or parent.get("trade_id") or "")
        side = _trade_direction(parent)
        if not parent_id or side not in {"BUY", "SELL"}:
            continue
        if str(parent.get("status", "OPEN")).upper() not in {"OPEN", "PARTIAL", "TP1_HIT"}:
            continue
        if _scale_in_count_for_parent(open_trades, parent_id) >= max_scale_ins:
            continue

        # Block scale-in if total open trades in same direction already at cap
        if max_open_same_dir > 0:
            open_same_dir = len([t for t in open_trades if _trade_direction(t) == side and str(t.get("status", "OPEN")).upper() in {"OPEN", "PARTIAL", "TP1_HIT"}])
            if open_same_dir >= max_open_same_dir:
                logger.info(
                    "Scale-in blocked for %s %s: %d open same-direction trades already at cap %d",
                    side, symbol, open_same_dir, max_open_same_dir,
                )
                continue

        parent_entry = _safe_float(parent.get("entry_price"), 0.0)

        # Scale-in only at a BETTER price than the parent entry:
        #   BUY  → price must be at least trigger_points BELOW entry (pullback/discount)
        #   SELL → price must be at least trigger_points ABOVE entry (pullback/discount)
        # This prevents scale-ins at the same price or worse (adding to a loser).
        if parent_entry > 0:
            if side == "BUY":
                pullback_pts = price_to_points(parent_entry - current_price, symbol=symbol)
            else:
                pullback_pts = price_to_points(current_price - parent_entry, symbol=symbol)
            if pullback_pts < trigger_points:
                logger.info(
                    "Scale-in skipped for %s %s: price %.2f is only %.0f pts pullback from entry %.2f (need ≥%d pts %s entry)",
                    side, symbol, current_price, pullback_pts, parent_entry, trigger_points,
                    "below" if side == "BUY" else "above",
                )
                continue

        # ── Agent consensus check: scale-in is a NEW signal ──
        # Must have fresh agent agreement, not just price proximity.
        sr = config.get("signal_requirements", {}) or {}
        min_agents = int(sr.get("min_agents_agree", 3) or 3)
        min_agent_conf = int(sr.get("agent_min_confidence", 70) or 70)
        min_net_conf = float(sr.get("min_consensus_confidence", 72) or 72)

        agent_names = ["technical", "classical", "smc", "price_action", "multitimeframe"]
        weights = get_agent_weights(config)
        agree_count = 0
        oppose_count = 0
        net_weighted = 0.0
        total_weight = 0.0
        for name in agent_names:
            result = all_results.get(name, {}) or {}
            agent_signal = str(result.get("signal", "WAIT")).upper()
            agent_conf = float(result.get("confidence", 0) or 0)
            weight = float(weights.get(name, 0.2))
            if agent_conf < min_agent_conf:
                continue  # Agent not qualified
            total_weight += weight
            if agent_signal == side:
                agree_count += 1
                net_weighted += weight * (agent_conf / 100.0)
            elif agent_signal in {"BUY", "SELL"} and agent_signal != side:
                oppose_count += 1
                net_weighted -= weight * (agent_conf / 100.0)

        # Net weighted confidence (after opposition penalty)
        consensus_conf = (net_weighted / total_weight * 100.0) if total_weight > 0 else 0.0

        if agree_count < min_agents:
            logger.info(
                "Scale-in blocked for %s %s: only %d/%d qualified agents agree (need ≥%d)",
                side, symbol, agree_count, len(agent_names), min_agents,
            )
            continue

        if consensus_conf < min_net_conf:
            logger.info(
                "Scale-in blocked for %s %s: net confidence %.0f%% below %.0f%% (%d agree, %d oppose)",
                side, symbol, consensus_conf, min_net_conf, agree_count, oppose_count,
            )
            continue

        # Check risk filters
        risk = all_results.get("risk", {}) or {}
        risk_checks = risk.get("checks", risk.get("risk_checks", {})) or {}
        risk_approved = risk.get("approved", True)
        if not risk_approved or any(
            not v for k, v in risk_checks.items()
            if k in {"max_open_trades_filter", "max_daily_signals_filter", "atr_filter", "spread_filter", "consecutive_losses_filter"}
        ):
            failed = [k for k, v in risk_checks.items() if not v and k in {"max_open_trades_filter", "max_daily_signals_filter", "atr_filter", "spread_filter", "consecutive_losses_filter"}]
            logger.info("Scale-in blocked for %s %s: risk filters failed: %s", side, symbol, failed or "not approved")
            continue

        levels = _levels_from_results(all_results, side)
        if not levels:
            continue
        if side == "BUY":
            directional_levels = [level for level in levels if level <= current_price]
        else:
            directional_levels = [level for level in levels if level >= current_price]
        if not directional_levels:
            directional_levels = levels
        nearest_level = min(directional_levels, key=lambda level: abs(level - current_price))
        distance_points = abs(price_to_points(current_price - nearest_level, symbol=symbol))

        entry_price = current_price
        parent_sl = _safe_float(parent.get("stop_loss"), 0.0)
        parent_tp1 = _safe_float(parent.get("tp1"), 0.0)
        parent_tp2 = _safe_float(parent.get("tp2"), 0.0)
        # Recalculate SL/TP for scale-in based on its own entry price,
        # preserving the same distance ratios as the parent trade.
        if parent_entry > 0 and parent_sl > 0:
            sl_distance = abs(parent_entry - parent_sl)
            stop_loss = entry_price - sl_distance if side == "BUY" else entry_price + sl_distance
        else:
            stop_loss = parent_sl
        if parent_entry > 0 and parent_tp1 > 0:
            tp1_distance = abs(parent_tp1 - parent_entry)
            tp1 = entry_price + tp1_distance if side == "BUY" else entry_price - tp1_distance
        else:
            tp1 = parent_tp1
        if parent_entry > 0 and parent_tp2 > 0:
            tp2_distance = abs(parent_tp2 - parent_entry)
            tp2 = entry_price + tp2_distance if side == "BUY" else entry_price - tp2_distance
        else:
            tp2 = parent_tp2
        trade_id = database.new_trade_id()
        reason = f"Pullback {pullback_pts:.0f} pts from entry + {agree_count} agents agree ({consensus_conf:.0f}% confidence)"
        decision: Dict[str, Any] = {
            "trade_id": trade_id,
            "decision": side,
            "symbol": symbol,
            "current_price": entry_price,
            "confidence": int(_safe_float(all_results.get("confidence"), 75)),
            "trading_mode": oe.get("mode", "paper"),
            "paper_trading": True,
            "reasons": [reason, f"Fixed-risk scale-in for parent trade {parent_id}"],
            "signal": {
                "symbol": symbol,
                "type": side,
                "scale_in": True,
                "parent_trade_id": parent_id,
                "scale_in_size_ratio": float(fr.get("scale_in_size_ratio", 0.5) or 0.5),
                "entry": {"price": entry_price, "kind": "MARKET"},
                "entry_kind": "MARKET",
                "stop_loss": stop_loss,
                "tp1": tp1,
                "tp2": tp2,
            },
        }
        # Build agent votes line for Telegram
        vote_emojis = {"BUY": "🟢", "SELL": "🔴", "WAIT": "🟡"}
        agent_lines = []
        for name in agent_names:
            result = all_results.get(name, {}) or {}
            agent_signal = str(result.get("signal", "WAIT")).upper()
            agent_conf = float(result.get("confidence", 0) or 0)
            if agent_conf < min_agent_conf:
                emoji = "⚪"
                label = "skip"
            else:
                emoji = vote_emojis.get(agent_signal, "⚪")
                label = f"{agent_signal} {agent_conf:.0f}%"
            agent_lines.append(f"{emoji} {name.title()} {label}")
        votes_block = "\n".join(agent_lines)
        message = (
            f"➕ <b>Scale-In {html.escape(symbol)} — {html.escape(side)}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Parent:</b> {html.escape(parent_id)}\n"
            f"• <b>Pullback:</b> {pullback_pts:.0f} pts from entry ({parent_entry:.2f} → {entry_price:.2f})\n"
            f"• <b>Consensus:</b> {agree_count}/{len(agent_names)} agents · {consensus_conf:.0f}% confidence\n"
            "──────────────────\n"
            "🗳️ AGENT VOTES\n"
            f"{votes_block}\n"
            "──────────────────\n"
            "🎯 TRADE PLAN\n"
            f"• <b>Entry:</b> {entry_price:.2f}\n"
            f"• <b>Stop Loss:</b> {stop_loss:.2f}\n"
            f"• <b>TP1:</b> {tp1:.2f}\n"
            f"• <b>TP2:</b> {tp2:.2f}\n"
            f"• <b>Size:</b> {decision['signal']['scale_in_size_ratio']}x (half position)\n"
            f"• <b>RR:</b> {abs(tp2 - entry_price) / max(abs(stop_loss - entry_price), 0.01):.2f}R\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>ID: {html.escape(trade_id)}</i>"
        )
        delivered = False
        try:
            delivered = bool(telegram.send_message(message, urgent=True))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to send scale-in Telegram message for %s: %s", parent_id, exc)
        if delivered:
            database.save_trade(decision)
        else:
            logger.error("Scale-in for %s was not saved because Telegram delivery failed", parent_id)
        return


def _session_plan_delivery_cfg(config: Dict[str, Any]) -> Dict[str, Any]:
    return (config.get("session_plan_delivery") or {}) if isinstance(config, dict) else {}


def _session_plan_payload(plan_or_row: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(plan_or_row, dict):
        return {}
    payload = plan_or_row.get("payload")
    if isinstance(payload, dict) and payload:
        return dict(payload)
    return dict(plan_or_row)

def _session_plan_reference_time(plan_or_row: Dict[str, Any] | None) -> datetime | None:
    if not isinstance(plan_or_row, dict):
        return None
    for key in ("telegram_sent_at", "analysis_run_at", "plan_created_at", "created_at"):
        parsed = _parse_datetime(plan_or_row.get(key))
        if parsed:
            return parsed
    payload = _session_plan_payload(plan_or_row)
    for key in ("telegram_sent_at", "analysis_run_at", "plan_created_at", "created_at"):
        parsed = _parse_datetime(payload.get(key))
        if parsed:
            return parsed
    return None


def _session_plan_session_key(plan_or_row: Dict[str, Any] | None, config: Dict[str, Any], *, symbol: str) -> str:
    payload = _session_plan_payload(plan_or_row)
    session_label = str(payload.get("session_label") or plan_or_row.get("session_label") or "UNKNOWN")
    ref = _session_plan_reference_time(plan_or_row) or datetime.now(timezone.utc)
    tz_name = str((config.get("schedule", {}) or {}).get("timezone") or (config.get("trading_hours", {}) or {}).get("timezone") or "Asia/Hebron")
    try:
        local_dt = ref.astimezone(ZoneInfo(tz_name))
    except Exception:
        local_dt = ref.astimezone(timezone.utc)
    return f"{symbol}::{local_dt.strftime('%Y-%m-%d')}::{session_label}"


def _session_plan_delivery_meta(current_plan: Dict[str, Any], sent_rows: List[Dict[str, Any]], config: Dict[str, Any], *, symbol: str) -> Dict[str, Any]:
    cfg = _session_plan_delivery_cfg(config)
    if not bool(cfg.get("enabled", True)):
        return {"send": False, "reason": None, "kind": None, "previous": None}
    if bool(cfg.get("only_when_ready", True)) and not bool(current_plan.get("plan_ready")):
        return {"send": False, "reason": None, "kind": None, "previous": None}
    current_key = _session_plan_session_key(current_plan, config, symbol=symbol)
    same_session_rows = [row for row in (sent_rows or []) if _session_plan_session_key(row, config, symbol=symbol) == current_key]
    previous = same_session_rows[0] if same_session_rows else None
    if previous is None:
        return {"send": bool(current_plan.get("plan_ready")), "reason": "first_ready_plan_this_session", "kind": "OPENING_PLAN", "previous": None}
    min_interval = float(cfg.get("min_update_interval_minutes", 25) or 25)
    previous_time = _session_plan_reference_time(previous)
    if previous_time:
        age_minutes = (datetime.now(timezone.utc) - previous_time).total_seconds() / 60.0
        if age_minutes < min_interval:
            return {"send": False, "reason": None, "kind": None, "previous": previous}
    reason = _session_plan_delivery_reason(current_plan, previous, config, symbol=symbol)
    if not reason:
        return {"send": False, "reason": None, "kind": None, "previous": previous}
    return {"send": True, "reason": reason, "kind": "PLAN_UPDATE", "previous": previous}


def _plan_field_changed(prev: Dict[str, Any], curr: Dict[str, Any], key: str, *, symbol: str, min_change_points: float) -> bool:
    try:
        old_v = float(prev.get(key))
        new_v = float(curr.get(key))
    except (TypeError, ValueError):
        return False
    return abs(price_to_points(new_v - old_v, symbol=symbol)) >= min_change_points


def _session_plan_delivery_reason(current_plan: Dict[str, Any], previous_snapshot: Dict[str, Any] | None, config: Dict[str, Any], *, symbol: str) -> str | None:
    cfg = _session_plan_delivery_cfg(config)
    if not bool(cfg.get("enabled", True)):
        return None
    if bool(cfg.get("only_when_ready", True)) and not bool(current_plan.get("plan_ready")):
        return None
    prev = _session_plan_payload(previous_snapshot)
    if not prev:
        return "first_ready_plan"
    if not bool(prev.get("plan_ready")) and bool(current_plan.get("plan_ready")):
        return "became_ready"
    # Metadata-only changes (scenario_type / poi_classification / planner_source)
    # must not spam users with a fresh PLAN UPDATE. Only material directional,
    # authority, execution, or price-level changes deserve a new broadcast.
    keys = ["session_bias", "authority_state", "authority_direction", "execution_preference", "plan_status"]
    for key in keys:
        if str(prev.get(key) or "") != str(current_plan.get(key) or ""):
            return f"changed_{key}"
    min_change_points = float(cfg.get("min_change_points", 60) or 60)
    for key in ["primary_entry_price", "standby_entry_price", "invalidation_level", "target_liquidity"]:
        if _plan_field_changed(prev, current_plan, key, symbol=symbol, min_change_points=min_change_points):
            return f"changed_{key}_materially"
    prev_zone = prev.get("primary_entry_zone") or {}
    curr_zone = current_plan.get("primary_entry_zone") or {}
    for key in ["low", "high"]:
        try:
            old_v = float(prev_zone.get(key))
            new_v = float(curr_zone.get(key))
            if abs(price_to_points(new_v - old_v, symbol=symbol)) >= min_change_points:
                return f"changed_primary_zone_{key}"
        except (TypeError, ValueError):
            pass
    return None


def _should_send_session_plan_telegram(current_plan: Dict[str, Any], previous_snapshot: Dict[str, Any] | None, config: Dict[str, Any], *, symbol: str) -> bool:
    return _session_plan_delivery_reason(current_plan, previous_snapshot, config, symbol=symbol) is not None


def _session_plan_agent_opinions(agent_details: Dict[str, Any]) -> List[Dict[str, Any]]:
    opinions: List[Dict[str, Any]] = []
    for key in ["technical", "classical", "smc", "price_action", "multitimeframe", "macro_fundamental"]:
        detail = (agent_details or {}).get(key)
        if not isinstance(detail, dict):
            continue
        direction = str(detail.get("direction") or "WAIT").upper()
        confidence = _safe_float(detail.get("confidence"), 0.0)
        signals = [str(x) for x in (detail.get("signals") or []) if str(x).strip()]
        summary = str(detail.get("summary") or "").strip()
        if not summary and not signals and direction == "WAIT":
            summary = "No strong directional edge yet."
        opinions.append(
            {
                "key": key,
                "label": str(detail.get("label") or key),
                "direction": direction,
                "confidence": round(confidence, 1),
                "summary": summary,
                "signals": signals[:2],
            }
        )
    return opinions


def _decorate_session_plan_for_delivery(
    plan: Dict[str, Any],
    decision: Dict[str, Any],
    all_results: Dict[str, Any],
    delivery_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = deepcopy(plan) if isinstance(plan, dict) else {}
    agent_details = decision.get("agent_details") or _compact_agent_details(all_results)
    payload["agent_opinions"] = _session_plan_agent_opinions(agent_details if isinstance(agent_details, dict) else {})
    payload["gemini_plan_review"] = deepcopy(decision.get("gemini_analysis") or {})
    payload["gemini_macro_review"] = deepcopy(decision.get("gemini_macro_review") or {})
    payload["gemini_news_review"] = deepcopy(decision.get("gemini_news_review") or {})
    payload["macro_plan"] = deepcopy(all_results.get("macro_fundamental") or {})
    payload["delivery_context"] = dict(delivery_context or {})
    return payload


def _is_news_hard_block(decision: Dict[str, Any], all_results: Dict[str, Any]) -> bool:
    warnings = [str(w).lower() for w in (decision.get("warnings") or [])]
    if any(w.startswith("news blocked") or w.startswith("ai news blocked") for w in warnings): return True
    news = all_results.get("news", {}) or {}
    if news.get("can_trade") is False or str(news.get("market_status", "")).upper() in {"DANGER", "HIGH_VOLATILITY"}: return True
    news_ai = all_results.get("news_ai", {}) or news.get("ai_interpretation", {}) or {}
    if news_ai.get("available"):
        if bool(news_ai.get("block_trading", False)): return True
        if str(news_ai.get("allowed_direction", "BOTH")).upper() == "NONE": return True
        if str(news_ai.get("risk_level", "")).upper() == "EXTREME": return True
    return False


def _reason_key(text: str) -> str:
    value = str(text or "").lower()
    value = value.replace("&gt;=", ">=").replace("≥", ">=")
    value = value.replace("agreeing agents", "agents")
    value = value.replace("with weighted confidence", "weighted confidence")
    return " ".join(value.split())


def _append_unique_reason(lines: List[str], text: str) -> None:
    clean = str(text or "").strip()
    if not clean: return
    key = _reason_key(clean)
    existing_keys = [_reason_key(line.lstrip("• ")) for line in lines]
    if key not in existing_keys:
        lines.append(f"• {clean}")


def _payload_supports_signal_generation(payload: Dict[str, Any] | None) -> bool:
    if not payload:
        return False
    if isinstance(payload, dict) and payload.get("supports_signal_generation") is not None:
        return bool(payload.get("supports_signal_generation"))
    source = str((payload or {}).get("source") or "") if isinstance(payload, dict) else ""
    return source == "twelvedata"


def _payload_supports_pending_activation(payload: Dict[str, Any] | None) -> bool:
    if not payload:
        return False
    if isinstance(payload, dict) and payload.get("supports_pending_activation") is not None:
        return bool(payload.get("supports_pending_activation"))
    source = str((payload or {}).get("source") or "") if isinstance(payload, dict) else ""
    return source == "twelvedata"


def _market_prices_text(config: Dict[str, Any] | None, current_symbol: str, current_price: float) -> str:
    try:
        base_config = config or load_config()
        instruments = enabled_instruments(base_config)
    except Exception:
        base_config = config or {}
        instruments = [{"symbol": current_symbol or "XAU/USD"}]
    lines: List[str] = []
    seen: set[str] = set()
    for instrument in instruments:
        symbol = str(instrument.get("symbol") or "").strip() or "XAU/USD"
        if symbol in seen: continue
        seen.add(symbol)
        price = 0.0
        if symbol == current_symbol and current_price > 0: price = current_price
        else:
            try:
                symbol_config = config_for_instrument(base_config, instrument)
                payload = MarketDataService(symbol_config).get_ohlcv("5m", outputsize=3)
                if payload: price = _safe_float(payload.get("current_price"), 0.0)
            except Exception: pass
        price_label = f"{price:.2f}" if price > 0 else "N/A"
        lines.append(f"• {html.escape(symbol)}: {html.escape(price_label)}")
    return "\n".join(lines) if lines else f"• {html.escape(current_symbol)}: N/A"


def _pending_age_hours(trade: Dict[str, Any]) -> float:
    ref = _parse_datetime(trade.get("created_at") or trade.get("entry_time") or trade.get("opened_at"))
    if not ref:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - ref).total_seconds() / 3600.0)


def _build_market_status_message(
    decision: Dict[str, Any],
    all_results: Dict[str, Any],
    database: DatabaseService,
    config: Dict[str, Any] | None = None,
) -> str:
    current_symbol = str(decision.get("symbol") or all_results.get("symbol") or (config or {}).get("symbol") or "XAU/USD")
    current_price = _safe_float(decision.get("current_price", all_results.get("current_price", 0)), 0.0)
    prices_text = _market_prices_text(config, current_symbol, current_price)

    # ── Open trades / pending orders summary ─────────────────────────────
    tracked_trades = database.get_open_trades()
    trades_section = ""
    if tracked_trades:
        from utils.instruments import price_to_points
        live_statuses = {"OPEN", "PARTIAL", "TP1_HIT"}
        pending_statuses = {"PENDING"}
        live_trades = [t for t in tracked_trades if str(t.get("status") or "OPEN").upper() in live_statuses]
        pending_trades = [t for t in tracked_trades if str(t.get("status") or "").upper() in pending_statuses]
        parts: List[str] = ["──────────────────"]

        if live_trades:
            trade_lines: List[str] = []
            net_pts = 0.0
            for t in live_trades[:20]:
                tid = str(t.get("id", ""))
                short = tid.split("_")[-1] if "_" in tid else (tid[-8:] if len(tid) >= 8 else tid)
                direction = str(t.get("type") or t.get("side") or "BUY").upper()
                entry = _safe_float(t.get("entry_price"), 0.0)
                tp1 = _safe_float(t.get("tp1"), 0.0)
                pnl_pts = _safe_float(t.get("current_pnl_points"), 0.0)
                if pnl_pts == 0 and entry > 0 and current_price > 0:
                    raw = (current_price - entry) if direction == "BUY" else (entry - current_price)
                    pnl_pts = price_to_points(raw, symbol=str(t.get("symbol") or current_symbol))
                net_pts += pnl_pts
                usd = pnl_pts / 10.0
                status = str(t.get("status") or "OPEN").upper()
                marker = "🟢" if pnl_pts > 0 else "🔴" if pnl_pts < 0 else "➖"
                prog_txt = ""
                tp1_dist = abs(price_to_points(tp1 - entry, symbol=str(t.get("symbol") or current_symbol))) if tp1 and entry else 0
                if tp1_dist > 0 and pnl_pts > 0:
                    pct = min(pnl_pts / tp1_dist * 100, 100)
                    prog_txt = f" · {pct:.0f}%➜TP1"
                elif tp1 and entry and ((direction == "BUY" and current_price >= tp1) or (direction == "SELL" and current_price <= tp1)):
                    prog_txt = " · ✅TP1"
                status_txt = "" if status == "OPEN" else f" [{html.escape(status)}]"
                trade_lines.append(
                    f"{marker} {direction} <code>#{html.escape(short)}</code>  "
                    f"{pnl_pts:+.0f}pts ({usd:+.1f}$){prog_txt}{status_txt}"
                )
            if len(live_trades) > 20:
                trade_lines.append(f"… and {len(live_trades) - 20} more")
            net_usd = net_pts / 10.0
            net_marker = "🟢" if net_pts > 0 else "🔴" if net_pts < 0 else "➖"
            parts.append(f"📊 <b>Open Trades ({len(live_trades)})</b>")
            parts.extend(trade_lines)
            parts.append(f"{net_marker} <b>Net:</b> {net_pts:+.0f}pts ({net_usd:+.1f}$)")

        if pending_trades:
            if live_trades:
                parts.append("──────────────────")
            pending_lines: List[str] = []
            for t in pending_trades[:20]:
                tid = str(t.get("id", ""))
                short = tid.split("_")[-1] if "_" in tid else (tid[-8:] if len(tid) >= 8 else tid)
                direction = str(t.get("type") or t.get("side") or "BUY").upper()
                entry = _safe_float(t.get("entry_price"), 0.0)
                status = str(t.get("status") or "PENDING").upper()
                order_type = str(t.get("order_type") or t.get("order_kind") or status).upper()
                pts_to_fill = abs(price_to_points(entry - current_price, symbol=str(t.get("symbol") or current_symbol))) if entry and current_price else 0.0
                age_h = _pending_age_hours(t)
                pending_lines.append(
                    f"🟡 {direction} <code>#{html.escape(short)}</code> @ {entry:.2f} [{html.escape(order_type)}] · {pts_to_fill:.0f} pts to fill · waiting {age_h:.1f}h"
                )
            if len(pending_trades) > 20:
                pending_lines.append(f"… and {len(pending_trades) - 20} more")
            parts.append(f"⏳ <b>Pending Orders ({len(pending_trades)})</b>")
            parts.extend(pending_lines)

        trades_section = "\n".join(parts) + "\n"

    # ── Gemini review (keep concise) ─────────────────────────────────────
    gemini_context = ""
    gemini_analysis = decision.get("gemini_analysis", {}) or {}
    if gemini_analysis.get("available"):
        bias = gemini_analysis.get("market_bias", "NEUTRAL")
        reason = gemini_analysis.get("reason", "")
        gemini_context = (
            f"🧠 <b>Gemini:</b> {html.escape(str(bias))} — {html.escape(str(reason))}\n"
        )
    gemini_news = decision.get("gemini_news_review", {}) or {}
    if gemini_news.get("available") and not gemini_news.get("suppressed"):
        risk = str(gemini_news.get("risk_level") or "LOW").upper()
        gemini_context += f"📰 <b>Gemini News:</b> {html.escape(risk)}"
        bullets = gemini_news.get("summary_bullets") or []
        if bullets:
            first = str(bullets[0]).strip()
            if first:
                gemini_context += f" — {html.escape(first[:80])}"
        gemini_context += "\n"
    gemini_macro = decision.get("gemini_macro_review", {}) or {}
    if gemini_macro.get("available") and not gemini_macro.get("suppressed"):
        verdict = str(gemini_macro.get("macro_verdict") or "NEUTRAL")
        driver = str(gemini_macro.get("primary_driver") or "")
        gemini_context += f"🌍 <b>Gemini Macro:</b> {html.escape(verdict)}"
        if driver:
            gemini_context += f" ({html.escape(driver)})"
        gemini_context += "\n"

    return (
        "🟡 <b>SmartSignal — Market Status</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 <b>Prices:</b>\n{prices_text}\n"
        f"🎯 Decision: WAIT\n"
        f"{trades_section}"
        f"{gemini_context}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Market status • Next update in ~1 hour</i>"
    )


def _compact_agent_details(all_results: Dict[str, Any]) -> Dict[str, Any]:
    labels = {"technical": "Technical", "classical": "Classical", "smc": "SMC", "price_action": "Price Action", "multitimeframe": "Multi-Timeframe", "macro_fundamental": "Macro / Fundamental"}
    details: Dict[str, Any] = {}
    for key, label in labels.items():
        result = all_results.get(key, {}) or {}
        # Unify reading order: signal first, then direction (same as DecisionAgent._collect_votes)
        direction = str(result.get("signal") or result.get("direction") or "WAIT").upper()
        if direction in {"NEUTRAL", "HOLD", "NO_TRADE", "NONE", ""}: direction = "WAIT"
        signals = result.get("signals") or result.get("reasons") or []
        if not signals and key == "technical": signals = (result.get("technical", {}) or {}).get("reasons") or []
        if not isinstance(signals, list): signals = [signals] if signals else []
        summary = result.get("summary") or result.get("reasoning") or ""
        details[key] = {"label": label, "direction": direction, "confidence": result.get("confidence", 0), "summary": summary, "signals": [str(x) for x in signals[:4] if x]}
    return details


def _select_setup_candidate(decision_type: str, all_results: Dict[str, Any]) -> Dict[str, Any]:
    smc = all_results.get("smc", {}) or {}
    candidates = list(smc.get("setup_candidates") or [])
    if not candidates:
        return {}
    side = str(decision_type or "").upper()
    if side in {"BUY", "SELL"}:
        directional = [c for c in candidates if str(c.get("direction", "")).upper() == side]
        if directional:
            directional.sort(key=lambda c: float((c.get("setup_quality") or {}).get("score", c.get("quality_score", 0)) or 0), reverse=True)
            return directional[0]
    candidates.sort(key=lambda c: float((c.get("setup_quality") or {}).get("score", c.get("quality_score", 0)) or 0), reverse=True)
    return candidates[0]


def _setup_context_payload(decision: Dict[str, Any], all_results: Dict[str, Any]) -> Dict[str, Any]:
    decision_type = str(decision.get("decision") or "").upper()
    selected = _select_setup_candidate(decision_type, all_results)
    mtf = all_results.get("multitimeframe", {}) or {}
    quality = decision.get("quality") or {}
    entry_attr = decision.get("entry_attribution") or {}
    classic = decision.get("classic", {}) or {}
    strongest = classic.get("strongest_directional") or {}
    smc_structure = (all_results.get("smc", {}) or {}).get("setup_structure") or {}
    quality_obj = selected.get("setup_quality") if isinstance(selected.get("setup_quality"), dict) else None
    payload = {
        "id": selected.get("id"),
        "state_key": selected.get("state_key"),
        "setup_type": selected.get("setup_type") or mtf.get("setup_type") or smc_structure.get("setup_type") or "CONSENSUS_GENERIC",
        "setup_state": selected.get("setup_state") or smc_structure.get("setup_state") or ("ENTRY_TRIGGERED" if decision_type in {"BUY", "SELL"} else "DETECTED"),
        "lead_agent": selected.get("lead_agent") or entry_attr.get("primary_entry_driver") or strongest.get("agent") or "consensus",
        "quality_grade": (quality_obj or {}).get("grade") or selected.get("quality_grade") or quality.get("grade") or ((decision.get("trade_grade") or {}).get("grade") if isinstance(decision.get("trade_grade"), dict) else None),
        "quality_score": (quality_obj or {}).get("score") or selected.get("quality_score") or quality.get("score"),
        "poi_type": selected.get("poi_type") or smc_structure.get("poi_type"),
        "poi_zone": selected.get("poi_zone"),
        "poi_rank_score": selected.get("poi_rank_score") or smc_structure.get("poi_rank_score"),
        "poi_rank_reasons": selected.get("poi_rank_reasons") or smc_structure.get("poi_rank_reasons"),
        "poi_quality_score": selected.get("poi_quality_score") or smc_structure.get("poi_quality_score"),
        "return_probability_score": selected.get("return_probability_score") or smc_structure.get("return_probability_score"),
        "thesis_dominance_score": selected.get("thesis_dominance_score") or smc_structure.get("thesis_dominance_score"),
        "selection_role": selected.get("selection_role") or smc_structure.get("selection_role"),
        "selection_rank": selected.get("selection_rank") or smc_structure.get("selection_rank"),
        "expected_revisit_window": selected.get("expected_revisit_window") or smc_structure.get("expected_revisit_window"),
        "sweep_side": selected.get("sweep_side") or smc_structure.get("sweep_side"),
        "displacement_score": selected.get("displacement_score") or smc_structure.get("displacement_score"),
        "trigger_state": selected.get("trigger_state") or smc_structure.get("trigger_state"),
        "trigger_score": selected.get("trigger_score") or smc_structure.get("trigger_score"),
        "trigger_ready": selected.get("trigger_ready") if selected.get("trigger_ready") is not None else smc_structure.get("trigger_ready"),
        "execution_hint": selected.get("execution_hint") or smc_structure.get("execution_hint"),
        "target_liquidity": selected.get("target_liquidity") or smc_structure.get("target_liquidity"),
        "entry_reason": selected.get("entry_reason"),
        "details": selected.get("details") or {},
    }
    return {k: v for k, v in payload.items() if v not in (None, "", {}, [])}


def run_agent(agent_name: str, agent: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    try:
        logger.info("Running agent: %s", agent_name)
        return agent.analyze(data)
    except Exception as exc:
        logger.exception("Agent %s failed", agent_name)
        return {"agent": agent_name, "signal": "WAIT", "confidence": 0, "reasoning": f"Agent failed: {exc}"}


def _log_gemini_result(label: str, result: Dict[str, Any] | None) -> None:
    result = result or {}
    if result.get("available"):
        logger.info("🧠 Gemini %s: added quality=%s", label, result.get("quality", "ok"))
    elif result.get("suppressed"):
        logger.info("🧠 Gemini %s: suppressed (%s)", label, result.get("suppress_reason", "generic"))
    else:
        logger.info("🧠 Gemini %s: unavailable/skipped (%s)", label, result.get("summary") or result.get("reason") or "unknown")


def _check_and_send_post_news(
    gemini, telegram, news_result: Dict[str, Any],
    symbol: str, current_price: float, config: Dict[str, Any],
    database: Any = None,
) -> None:
    """Check if a TIER_1/TIER_2 event recently released and send post-news analysis.

    Trigger: event was released 5-30 minutes ago (minutes_until between -5 and -30).
    Only fires once per event (tracked in storage/post_news_tracker.json).
    Uses persisted macro context from Supabase for DXY/dollar strength analysis.
    """
    from utils.helpers import get_current_session
    try:
        upcoming = news_result.get("upcoming_events") or []
        for event in upcoming:
            tier = str(event.get("tier", "")).upper()
            if tier not in {"TIER_1", "TIER_2"}:
                continue
            minutes_until = event.get("minutes_until", 0)
            # Event was released between 3 and 60 minutes ago.
            # Wide enough to catch it across multiple 5-min cron cycles.
            if -60 <= minutes_until <= -3:
                event_name = str(event.get("event", "Unknown Event"))
                event_time = str(event.get("time", ""))
                # Create unique key to avoid duplicate alerts
                event_key = f"{event_name}_{event_time}"
                if post_news_alert_sent(event_key, database=database):
                    continue
                logger.info("📰 Post-news trigger: %s released %d min ago", event_name, abs(minutes_until))
                # Build payload for Gemini post-news analysis
                # Read persisted macro context (DXY strength, risk sentiment, etc.)
                macro_context = {}
                if database:
                    try:
                        macro_context = database.get_macro_context()
                    except Exception:
                        pass
                from agents.macro_fundamental_agent import MacroFundamentalAgent
                macro_agent = MacroFundamentalAgent(config)
                macro = macro_agent.macro_direction(macro_context) if macro_context else macro_agent.macro_direction({})
                dxy_trend = macro_context.get("dxy_trend") or macro_context.get("usd_trend") or "unknown"
                risk_sentiment = macro_context.get("risk_sentiment") or "unknown"
                usd_score_detail = ""
                observations = macro_context.get("observations") or {}
                if isinstance(observations, dict):
                    pairs = [f"{sym}: {obs.get('usd_read', '?')}" for sym, obs in observations.items() if obs.get("component") == "usd"]
                    if pairs:
                        usd_score_detail = " | ".join(pairs)
                dxy_info = f"DXY trend: {dxy_trend}, Risk: {risk_sentiment}"
                if usd_score_detail:
                    dxy_info += f", Pairs: [{usd_score_detail}]"
                if macro.get("summary"):
                    dxy_info += f", Macro: {macro['summary']}"
                payload = {
                    "symbol": symbol,
                    "event_name": event_name,
                    "actual": event.get("actual") or event.get("expected"),  # actual may not be available yet
                    "forecast": event.get("expected") or event.get("forecast"),
                    "previous": event.get("previous"),
                    "impact_tier": tier,
                    "minutes_since_release": abs(minutes_until),
                    "current_price": current_price,
                    "price_before_event": None,  # not tracked per-event
                    "price_change_since_event": None,
                    "dxy_macro": dxy_info,
                    "session": get_current_session(),
                }
                analysis = gemini.interpret_post_news(payload)
                _log_gemini_result("post-news", analysis)
                if analysis.get("available") and not analysis.get("suppressed"):
                    sent = telegram.send_post_news_analysis(analysis, event_name, symbol)
                    if sent:
                        post_news_alert_record(event_key, database=database)
                        logger.info("📰 Post-news analysis sent for: %s", event_name)
    except Exception as exc:
        logger.warning("Post-news analysis check failed: %s", exc)


async def _run_analysis_for_config(config: Dict[str, Any]) -> None:
    telegram = TelegramService(config)
    try:
        database = DatabaseService(config)
        symbol = str(config.get("symbol", "XAU/USD"))
        normalized_symbol = normalize_symbol(symbol)
        open_trades_snapshot = database.get_open_trades()
        has_symbol_active_trades = any(normalize_symbol(t.get("symbol") or symbol) == normalized_symbol for t in open_trades_snapshot)
        session = TradingSessionAgent(config).check()
        if not session.get("trading_allowed") and not has_symbol_active_trades:
            # Post-news check: even outside hours, fire if a TIER_1/TIER_2
            # event just released — subscribers need the briefing regardless.
            post_news_was_sent = False
            try:
                gemini_off_hours = get_gemini_review_service(config)
                if gemini_off_hours.enabled:
                    news_off = NewsRiskAgent({**config, "macro_context": database.get_macro_context()}).check()
                    _check_and_send_post_news(
                        gemini=gemini_off_hours, telegram=telegram,
                        news_result=news_off, symbol=symbol,
                        current_price=None, config=config, database=database,
                    )
                    post_news_was_sent = any(
                        "post-news" in str(getattr(telegram, '_last_msg', ''))
                    )
            except Exception: pass
            
            if should_send_hourly_status(config) and not post_news_was_sent:
                telegram.send_message("🟡 <b>SmartSignal — Market Status</b>\n━━━━━━━━━━━━━━━━━━━━\n📈 Price: N/A\n🎯 Decision: WAIT\n📊 Outside trading hours\n\n<b>Reason:</b>\n• Outside trading hours\n━━━━━━━━━━━━━━━━━━━━")
            return
        market_data = MarketDataService(config)
        data = market_data.get_gold_data()
        if not data: return
        integrity = data.get("source_integrity") or {}
        logger.info(
            "Market data integrity for %s: source=%s type=%s grade=%s signal_generation=%s pending_activation=%s",
            symbol,
            integrity.get("source") or data.get("source"),
            integrity.get("source_type") or "unknown",
            integrity.get("reliability_grade") or "UNKNOWN",
            integrity.get("supports_signal_generation"),
            integrity.get("supports_pending_activation"),
        )
        if not _payload_supports_signal_generation(data):
            logger.error(
                "Analysis stopped for %s: source %s is not reliable enough for signal generation.",
                symbol,
                integrity.get("source") or data.get("source"),
            )
            return
        # Global price sanity — reject obviously corrupt ticks before analysis
        _cp = float(data.get('current_price', 0))
        _sym = str(config.get('symbol', 'XAU/USD'))
        _sane_min = 2500.0 if _sym.startswith('XAU') else 30.0
        _sane_max = 5500.0 if _sym.startswith('XAU') else 150.0
        if _cp > 0 and (_cp < _sane_min or _cp > _sane_max):
            logger.error(
                'PRICE SANITY FAILED (analysis): %s price=%.2f outside [%.0f-%.0f]. '
                'Skipping cycle — data provider glitch.',
                _sym, _cp, _sane_min, _sane_max,
            )
            return
        persisted_macro_context = database.get_macro_context()
        if has_symbol_active_trades:
            high, low = _latest_candle_extremes(data)
            recent_candles = (((data.get("timeframes", {}) or {}).get("5m") or {}).get("data") or data.get("data") or [])[-6:]
            try:
                news_pre_cfg = {**config, "macro_context": persisted_macro_context} if persisted_macro_context else config
                news_pre = NewsRiskAgent(news_pre_cfg).check()
            except Exception:
                news_pre = {}
            news_blocked_pre = bool(news_pre.get("can_trade") is False or str(news_pre.get("market_status", "")).upper() in {"DANGER", "HIGH_VOLATILITY"})
            OpenTradesManager(config).update_trades(
                open_trades=[t for t in open_trades_snapshot if normalize_symbol(t.get("symbol") or symbol) == normalized_symbol],
                current_price=float(data.get("current_price", 0)),
                candle_high=high,
                candle_low=low,
                recent_candles=recent_candles,
                database=database,
                telegram=telegram,
                now=datetime.now(timezone.utc),
                news_blocked=news_blocked_pre,
                news_context=news_pre,
                market_data_source=str(data.get("source") or ""),
            )
        if not session.get("trading_allowed"): return
        verified_snapshot = build_market_snapshot(data, config)
        data["verified_snapshot"] = verified_snapshot
        macro_input = {**data, "macro_context": persisted_macro_context} if persisted_macro_context else data
        macro = run_agent("macro_fundamental", MacroFundamentalAgent(config), macro_input)
        news_config = {**config, "macro_context": persisted_macro_context} if persisted_macro_context else config
        news = NewsRiskAgent(news_config).check()
        if isinstance(news, dict) and isinstance(macro, dict) and macro.get("macro_direction"):
            news["macro_direction"] = macro.get("macro_direction")
            news["macro_agent"] = macro
        all_results = {"technical": run_agent("technical", TechnicalAgent(config), data), "classical": run_agent("classical", ClassicalAgent(config), data), "smc": run_agent("smc", SMCAgent(config), data), "price_action": run_agent("price_action", PriceActionAgent(config), data), "multitimeframe": run_agent("multitimeframe", MultiTimeframeAgent(config), data), "macro_fundamental": macro, "current_price": data["current_price"], "symbol": symbol, "session": session, "verified_snapshot": verified_snapshot, "news": news, "daily_bias": run_agent("daily_bias", DailyBiasAgent(config), data)}
        # Sprint 2 foundation: persist setup-state transitions across cycles.
        setup_memory = SetupMemoryService(database, config)
        try:
            processed_candidates = setup_memory.process_candidates(
                list(((all_results.get("smc", {}) or {}).get("setup_candidates") or []))[:3],
                current_price=float(data.get("current_price", 0) or 0),
                symbol=symbol,
            )
            if "smc" in all_results and isinstance(all_results["smc"], dict):
                all_results["smc"]["setup_candidates"] = processed_candidates
                if processed_candidates:
                    all_results["smc"]["setup_structure"] = processed_candidates[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to process setup-memory state transitions: %s", exc)

        # Phase 1 foundation: build a morning/session plan BEFORE the move.
        # This is planning-only for now; later phases can translate PRIMARY /
        # STANDBY plan objects into live laddered pending orders.
        previous_sent_session_plan_rows: List[Dict[str, Any]] = []
        session_plan_context = {
            "symbol": symbol,
            "current_price": float(data.get("current_price") or 0),
            "market_data_source": str(data.get("source") or ""),
            "analysis_run_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        session_plan_snapshot_id = None
        session_plan_delivery_meta = {"send": False, "reason": None, "kind": None, "previous": None}
        try:
            previous_sent_session_plan_rows = database.get_recent_session_plans(limit=12, symbol=symbol, sent_only=True)
        except Exception as prev_exc:  # noqa: BLE001
            logger.warning("Failed to load previously delivered session plans: %s", prev_exc)
        try:
            session_plan = SessionPlannerService(config).build_plan(all_results, persist=False)
            all_results["session_plan"] = session_plan
            if session_plan.get("plan_ready"):
                logger.info(
                    "Session plan ready for %s: %s %s | primary=%s | standby=%s | score=%s",
                    symbol,
                    session_plan.get("session_bias"),
                    session_plan.get("scenario_type"),
                    ((session_plan.get("primary_poi") or {}).get("entry_price")),
                    ((session_plan.get("standby_poi") or {}).get("entry_price")) if session_plan.get("standby_poi") else None,
                    session_plan.get("planner_confidence"),
                )
            else:
                logger.info("Session plan not ready for %s: %s", symbol, session_plan.get("plan_reason"))
            try:
                session_plan_snapshot_id = database.save_session_plan(session_plan, session_plan_context)
            except Exception as persist_exc:  # noqa: BLE001
                logger.warning("Failed to persist session plan snapshot: %s", persist_exc)
            session_plan_delivery_meta = _session_plan_delivery_meta(
                session_plan,
                previous_sent_session_plan_rows,
                config,
                symbol=symbol,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to build session plan: %s", exc)
            all_results["session_plan"] = {"enabled": True, "plan_ready": False, "plan_status": "ERROR", "plan_reason": str(exc)}
            try:
                session_plan_snapshot_id = database.save_session_plan(all_results["session_plan"], session_plan_context)
            except Exception as persist_exc:  # noqa: BLE001
                logger.warning("Failed to persist errored session plan snapshot: %s", persist_exc)
        # Inject portfolio info so RiskManagementAgent can enforce max_open_trades
        # and max_daily_signals filters. Without this, those filters see 0 and
        # never block — which caused 15 simultaneous BUY trades.
        from datetime import date as _date
        _today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _open_trades_count = len([t for t in open_trades_snapshot if str(t.get("status", "OPEN")).upper() in {"OPEN", "PARTIAL", "TP1_HIT"}])
        _today_signals = database.get_recent_trades(limit=100)
        _today_signals_count = len([t for t in _today_signals if (t.get("created_at") or t.get("entry_time") or "").startswith(_today_str)])
        all_results["portfolio"] = {
            "open_trades_count": _open_trades_count,
            "today_signals_count": _today_signals_count,
        }
        all_results["risk"] = RiskManagementAgent(config).evaluate(all_results)
        # Scale-in AFTER risk evaluation so it can check risk filters
        if has_symbol_active_trades:
            await _check_scale_in(
                config,
                all_results,
                [t for t in open_trades_snapshot if normalize_symbol(t.get("symbol") or symbol) == normalized_symbol],
                database,
                telegram,
            )
        all_results["dynamic_risk"] = DynamicRiskManager(config).evaluate(database)
        learning_service = None
        try:
            learning_service = get_learning_service(database, config)
            # Note: load_current_weights() now reads from config.json (single source of truth).
            # We still initialize learning_service for confidence adjustments and recommendations.
        except Exception: pass
        decision = await DecisionAgent(config, learning_service=learning_service).decide_async(all_results)
        decision["agent_details"] = _compact_agent_details(all_results)
        decision["symbol"] = symbol
        decision["session_plan"] = all_results.get("session_plan", {})
        # Phase 5 data-enrichment: persist compact context with each trade so
        # learning/weekly reports can reason about sessions, news proximity,
        # volatility regime, and planned-vs-actual R:R without reconstructing
        # the original analysis run later.
        decision["session_info"] = session
        decision["daily_bias"] = all_results.get("daily_bias", {})
        decision["news_context"] = {
            "rule_based": all_results.get("news", {}),
            "macro": all_results.get("macro_fundamental", {}),
            "ai": all_results.get("news_ai", {}),
        }
        decision["market_context"] = {
            "technical_regime": ((all_results.get("technical", {}) or {}).get("technical", {}) or {}).get("market_regime")
            or (all_results.get("technical", {}) or {}).get("market_regime")
            or {},
            "rsi": ((all_results.get("technical", {}) or {}).get("technical", {}) or {}).get("rsi"),
            "daily_bias": all_results.get("daily_bias", {}),
            "macro_direction": (all_results.get("news", {}) or {}).get("macro_direction") or (all_results.get("macro_fundamental", {}) or {}).get("macro_direction", {}),
        }
        setup_context = _setup_context_payload(decision, all_results)
        decision["setup_context"] = setup_context
        decision["setup_id"] = setup_context.get("id")
        decision["setup_type"] = setup_context.get("setup_type")
        decision["setup_state"] = setup_context.get("setup_state")
        decision["lead_agent"] = setup_context.get("lead_agent")
        decision["setup_quality"] = setup_context.get("quality_grade")
        decision_type = str(decision.get("decision") or "").upper()

        # ═══════════════════════════════════════════════════════════════════
        # ── Path 2: Two-Agent Entry with External Confirmation ──
        # ═══════════════════════════════════════════════════════════════════
        if decision_type == "WAIT":
            two_agent = (decision.get("classic") or {}).get("two_agent")
            if isinstance(two_agent, dict) and two_agent:
                side = str(two_agent.get("side", "")).upper()
                if side in {"BUY", "SELL"}:
                    tae_cfg = (config.get("signal_requirements") or {}).get("two_agent_entry") or {}
                    cross_pts = int(tae_cfg.get("cross_entry_distance_points", 200) or 200)
                    macro_confirmed = False
                    gemini_confirmed = False
                    confirm_source = None
                    confirm_conf = 0.0

                    # ── Step A: Try Macro Confirmation ──
                    macro_cfg = tae_cfg.get("macro_confirmation") or {}
                    if macro_cfg.get("enabled", True):
                        macro_agent = all_results.get("macro_fundamental", {}) or {}
                        macro_dir = macro_agent.get("macro_direction", {}) or {}
                        macro_bias = str(macro_dir.get("bias", "")).upper()
                        macro_conf_val = float(macro_dir.get("confidence", 0) or 0)
                        macro_min = float(macro_cfg.get("min_confidence", 55) or 55)
                        expected_bias = "BULLISH_GOLD" if side == "BUY" else "BEARISH_GOLD"
                        if macro_bias == expected_bias and macro_conf_val >= macro_min:
                            macro_confirmed = True
                            confirm_source = "macro"
                            confirm_conf = macro_conf_val
                            logger.info(
                                "✅ Path 2: Macro confirms %s (bias=%s, conf=%.0f%% ≥ %.0f%%)",
                                side, macro_bias, macro_conf_val, macro_min
                            )
                        else:
                            logger.info(
                                "Path 2: Macro does NOT confirm %s (bias=%s, need=%s, conf=%.0f%% < %.0f%% or mismatch)",
                                side, macro_bias, expected_bias, macro_conf_val, macro_min
                            )

                    # ── Step B: Fallback to Gemini Confirmation ──
                    gemini_cfg = tae_cfg.get("gemini_confirmation") or {}
                    if not macro_confirmed and gemini_cfg.get("enabled", True):
                        try:
                            gemini_svc = get_gemini_review_service(config)
                            if gemini_svc.enabled:
                                gemini_review = gemini_svc.review_signal({
                                    "symbol": symbol,
                                    "decision": decision,
                                    "all_results": all_results
                                })
                                g_verdict = str(gemini_review.get("verdict", "")).upper()
                                g_conf_val = float(gemini_review.get("confidence", 0) or 0)
                                g_min = float(gemini_cfg.get("min_confidence", 70) or 70)
                                if g_verdict == side and g_conf_val >= g_min:
                                    gemini_confirmed = True
                                    confirm_source = "gemini"
                                    confirm_conf = g_conf_val
                                    logger.info(
                                        "✅ Path 2: Gemini confirms %s (verdict=%s, conf=%.0f%% ≥ %.0f%%)",
                                        side, g_verdict, g_conf_val, g_min
                                    )
                                    decision["gemini_review"] = gemini_review
                                else:
                                    logger.info(
                                        "❌ Path 2: Gemini does NOT confirm (verdict=%s vs %s, conf=%.0f%% < %.0f%%)",
                                        g_verdict, side, g_conf_val, g_min
                                    )
                            else:
                                logger.info("Path 2: Gemini skipped — API key not configured")
                        except Exception as _g_exc:
                            logger.warning("Path 2: Gemini confirmation failed: %s", _g_exc)

                    # ── If confirmed → rebuild signal payload and finalize entry ──
                    if macro_confirmed or gemini_confirmed:
                        risk = all_results.get("risk", {}) or {}
                        current_price = all_results.get("current_price")
                        entry_info = risk.get("entry", {}) or {}
                        entry_zone = entry_info.get("zone", {}) or {}
                        sl = risk.get("stop_loss", {}) or {}
                        tp = risk.get("take_profit", {}) or {}
                        tp1 = tp.get("tp1", {}) or {}
                        tp2 = tp.get("tp2", {}) or {}
                        entry_price = entry_info.get("price") or current_price
                        order_type = entry_info.get("order_type") or f"{side}_MARKET"
                        entry_kind = entry_info.get("kind") or "MARKET"

                        # Rebuild signal payload
                        decision["decision"] = side
                        decision["confidence"] = float(two_agent.get("confidence", 0))
                        decision["signal"] = {
                            "type": side,
                            "entry": {
                                "price": entry_price,
                                "low": entry_zone.get("low", entry_price),
                                "high": entry_zone.get("high", entry_price),
                                "kind": entry_kind,
                                "order_type": order_type,
                                "basis": entry_info.get("basis", ""),
                                "current_price": entry_info.get("current_price", current_price),
                                "distance_points": entry_info.get("distance_points", 0.0),
                            },
                            "stop_loss": sl.get("price", 0),
                            "tp1": tp1.get("price", 0),
                            "tp2": tp2.get("price", 0),
                            "tp1_rr": tp1.get("rr_ratio", 0),
                            "tp2_rr": tp2.get("rr_ratio", 0),
                            "rr_ratio": tp2.get("rr_ratio", tp1.get("rr_ratio", 0)),
                            "order_type": order_type,
                            "entry_kind": entry_kind,
                            "position_size": risk.get("position_size", {}),
                            "risk_summary": risk.get("summary", ""),
                        }
                        decision["entry_mode"] = f"two_agent_{confirm_source}"
                        decision["entry_path"] = 2
                        decision["confirm_source"] = confirm_source
                        decision["confirm_confidence"] = confirm_conf
                        existing_reasons = list(decision.get("reasons", []))
                        existing_reasons.append(
                            f"Two-agent entry: {side} confirmed by {confirm_source} ({confirm_conf:.0f}%)"
                        )
                        decision["reasons"] = existing_reasons

                        # Check cross-path distance BEFORE proceeding
                        cross_reason = _cross_path_distance_check(
                            decision, database, config, cross_distance_points=cross_pts
                        )
                        if cross_reason:
                            logger.info("❌ Path 2 blocked by cross-path distance: %s", cross_reason)
                            decision["decision"] = "WAIT"
                            decision["signal"] = {}
                            decision["entry_mode"] = "wait"
                            decision["entry_path"] = 0
                            decision_type = "WAIT"
                        else:
                            decision_type = side  # Set for downstream flow
                            logger.info(
                                "✅ Path 2 entry confirmed: %s via %s (2-agent conf=%.0f%%, %s conf=%.0f%%)",
                                side, confirm_source,
                                float(two_agent.get("confidence", 0)),
                                confirm_source, confirm_conf
                            )

        # Phase D: a confirmed day-map authority must not be overridden by a
        # weak local opposite-direction idea. Only high-authority reversal /
        # regime-flip setups may challenge the day map.
        if decision_type in {"BUY", "SELL"}:
            authority_review = DirectionalAuthorityService(config).review(
                decision,
                all_results.get("session_plan", {}) or {},
                [t for t in open_trades_snapshot if normalize_symbol(t.get("symbol") or symbol) == normalized_symbol],
            )
            decision["directional_authority"] = authority_review
            action = str(authority_review.get("action") or "ALLOW")
            if action == "BLOCK_OPPOSITE_LOCAL":
                logger.info("Directional authority blocked %s for %s: %s", decision_type, symbol, authority_review.get("reason"))
                decision["warnings"] = list(decision.get("warnings", [])) + [str(authority_review.get("reason") or "Directional authority blocked")]
                decision["decision"] = "WAIT"
                decision["signal"] = {}
                decision_type = "WAIT"
            elif action == "ALLOW_REGIME_FLIP":
                logger.info("Directional authority allowed regime flip %s for %s: %s", decision_type, symbol, authority_review.get("reason"))
                decision.setdefault("reasons", []).append(str(authority_review.get("reason") or "Directional authority allowed regime flip"))

        send_hourly_now = should_send_hourly_status(config)
        session_plan_ready_for_delivery = bool((all_results.get("session_plan") or {}).get("plan_ready")) and bool(_session_plan_delivery_cfg(config).get("enabled", True))
        if (decision_type in {"BUY", "SELL"}) or (decision_type == "WAIT" and send_hourly_now) or session_plan_ready_for_delivery:
            try:
                gemini = get_gemini_review_service(config)
                if not gemini.enabled:
                    logger.info("🧠 Gemini analysis skipped: API key not configured")
                else:
                    decision["gemini_analysis"] = gemini.analyze_market_context({"symbol": symbol, "current_price": data.get("current_price"), "decision": decision, "all_results": all_results})
                    _log_gemini_result("market context", decision.get("gemini_analysis"))
                    if decision_type in {"BUY", "SELL"}:
                        decision["gemini_review"] = gemini.review_signal({"symbol": symbol, "decision": decision, "all_results": all_results})
                        _log_gemini_result("signal review", decision.get("gemini_review"))
                    else:
                        logger.info("🧠 Gemini signal review skipped: WAIT hourly status")
                    decision["gemini_news_review"] = gemini.interpret_news_context({"symbol": symbol, "current_price": data.get("current_price"), "session": all_results.get("session"), "news": all_results.get("news"), "daily_bias": all_results.get("daily_bias"), "technical_context": all_results.get("technical"), "macro_agent": all_results.get("macro_fundamental")})
                    _log_gemini_result("news review", decision.get("gemini_news_review"))

                    # ── NEW: Macro-only independent review — July 2026 ──
                    try:
                        macro_agent_result = all_results.get("macro_fundamental", {}) or {}
                        if macro_agent_result.get("macro_direction"):
                            decision["gemini_macro_review"] = gemini.interpret_macro_context(macro_agent_result)
                            _log_gemini_result("macro", decision.get("gemini_macro_review"))
                    except Exception as _macro_exc:
                        logger.warning("Gemini macro review failed: %s", _macro_exc)

                    # ── Post-news analysis: after a TIER_1/TIER_2 event releases ──
                    _check_and_send_post_news(
                        gemini=gemini, telegram=telegram,
                        news_result=all_results.get("news", {}),
                        symbol=symbol,
                        current_price=data.get("current_price"),
                        config=config,
                        database=database,
                    )
            except Exception:
                logger.exception("🧠 Gemini analysis block failed")
        elif decision_type == "WAIT":
            logger.info("🧠 Gemini skipped: normal WAIT without hourly status")

        if session_plan_delivery_meta.get("send"):
            try:
                plan_message = _decorate_session_plan_for_delivery(
                    all_results.get("session_plan") or {},
                    decision,
                    all_results,
                    {
                        "message_kind": session_plan_delivery_meta.get("kind"),
                        "delivery_reason": session_plan_delivery_meta.get("reason"),
                    },
                )
                sent = telegram.send_session_plan(plan_message)
                if sent:
                    if session_plan_snapshot_id:
                        try:
                            database.mark_session_plan_telegram_sent(session_plan_snapshot_id, str(session_plan_delivery_meta.get("reason") or session_plan_delivery_meta.get("kind") or "session_plan_delivery"))
                        except Exception as mark_exc:  # noqa: BLE001
                            logger.warning("Failed to mark session plan Telegram delivery: %s", mark_exc)
                    logger.info("Session plan Telegram sent for %s (%s)", symbol, session_plan_delivery_meta.get("reason"))
                else:
                    logger.warning("Session plan Telegram returned False for %s", symbol)
            except Exception as delivery_exc:  # noqa: BLE001
                logger.warning("Failed to deliver session plan Telegram for %s: %s", symbol, delivery_exc)

        # Phase 2: if the morning/session planner already prepared a strong
        # PRIMARY / STANDBY thesis before the move, publish those pending ladder
        # orders now instead of waiting for a late one-off signal after price has
        # already traveled.
        ladder_created = _execute_session_plan_ladder(
            decision,
            all_results,
            [t for t in open_trades_snapshot if normalize_symbol(t.get("symbol") or symbol) == normalized_symbol],
            database,
            telegram,
            config,
        )
        if ladder_created:
            logger.info("Session-plan ladder created %s pending order(s) for %s", ladder_created, symbol)
            return
        if decision_type in {"BUY", "SELL"}:
            symbol_trades = [t for t in open_trades_snapshot if normalize_symbol(t.get("symbol") or symbol) == normalized_symbol]
            adaptive = AdaptiveExecutionService(config).review(decision, symbol_trades)
            adaptive_action = str(adaptive.get("action") or "ALLOW_NEW")
            if adaptive_action == "KEEP_PENDING":
                logger.info("Adaptive execution kept pending for %s %s: %s", decision_type, symbol, adaptive.get("reason"))
                return
            if adaptive_action == "NO_TRADE_MISSED_MOVE":
                logger.info("Adaptive execution skipped %s %s as missed move: %s", decision_type, symbol, adaptive.get("reason"))
                return
            if adaptive_action in {"PROMOTE_TO_MARKET", "REPLACE_WITH_CONTINUATION"}:
                decision = adaptive.get("decision") or decision
                decision["adaptive_execution"] = {
                    "action": adaptive_action,
                    "reason": adaptive.get("reason"),
                }
                logger.info("Adaptive execution %s for %s %s: %s", adaptive_action, decision_type, symbol, adaptive.get("reason"))

            # Phase E: even if legacy path 1 / path 2 found an entry, it must
            # still be inside or near the confirmed day map. This prevents small
            # local execution zones from bypassing a stronger planner view.
            day_map_review = DayMapSanityService(config).review(decision, all_results.get("session_plan", {}) or {})
            decision["day_map_sanity"] = day_map_review
            if str(day_map_review.get("action") or "ALLOW") != "ALLOW":
                logger.info("Day-map sanity blocked %s for %s: %s", decision_type, symbol, day_map_review.get("reason"))
                return

            # Cross-path distance check (applies to BOTH Path 1 and Path 2),
            # except when we're intentionally promoting/replacing an existing
            # morning-plan family rather than opening an unrelated duplicate.
            _tae_cfg_cross = (config.get("signal_requirements") or {}).get("two_agent_entry") or {}
            _cross_pts = int(_tae_cfg_cross.get("cross_entry_distance_points", 200) or 200)
            if adaptive_action not in {"PROMOTE_TO_MARKET", "REPLACE_WITH_CONTINUATION"}:
                _cross_block = _cross_path_distance_check(decision, database, config, cross_distance_points=_cross_pts)
                if _cross_block:
                    logger.info("Cross-path distance blocked: %s", _cross_block)
                    return

            if adaptive_action in {"PROMOTE_TO_MARKET", "REPLACE_WITH_CONTINUATION"}:
                governance = {
                    "action": "ALLOW_NEW",
                    "reason": f"adaptive execution {adaptive_action.lower()} bypassed normal pending duplication gate",
                    "cancelled_ids": [],
                }
            else:
                governance = PendingGovernor(config).review(
                    decision,
                    symbol_trades,
                    database=database,
                )
            decision["pending_governor"] = governance
            action = str(governance.get("action") or "ALLOW_NEW")
            if action == "KEEP_EXISTING_PENDING":
                logger.info("Pending governor blocked new %s for %s: %s", decision_type, symbol, governance.get("reason"))
                return
            if action in {"REPLACE_PENDING", "CANCEL_PENDING_ALLOW_NEW"}:
                logger.info("Pending governor action for %s %s: %s", decision_type, symbol, governance.get("reason"))
                existing_reasons = list(decision.get("reasons", []))
                existing_reasons.append(f"Pending governor: {governance.get('reason')}")
                decision["reasons"] = existing_reasons
                try:
                    telegram.send_pending_governance(governance, symbol=symbol, side=decision_type)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to send pending governance message: %s", exc)
            elif action == "KEEP_EXISTING_PENDING" and "blocked" in str(governance.get("reason") or "").lower():
                logger.info("Pending governor blocked replacement for %s %s: %s", decision_type, symbol, governance.get("reason"))
                try:
                    telegram.send_pending_governance(governance, symbol=symbol, side=decision_type)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to send pending replacement-blocked message: %s", exc)
                return

            duplicate_reason = None if adaptive_action in {"PROMOTE_TO_MARKET", "REPLACE_WITH_CONTINUATION"} else duplicate_signal_reason(decision, database, config)
            if duplicate_reason:
                logger.info("Signal blocked for %s %s: %s", decision_type, symbol, duplicate_reason)
                if str(duplicate_reason).startswith("Post-exit revalidation blocked:"):
                    try:
                        signal_entry = ((decision.get("signal") or {}).get("entry") or {}).get("price") or decision.get("current_price")
                        telegram.send_revalidation_block(
                            symbol=symbol,
                            side=decision_type,
                            entry_price=signal_entry,
                            reason=duplicate_reason,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Failed to send re-entry blocked message: %s", exc)
                return
            trade_id = database.new_trade_id()
            decision["trade_id"] = trade_id
            delivered = False
            try:
                delivered = bool(telegram.send_signal(decision))
            except Exception as exc:  # noqa: BLE001
                telegram.send_error_alert(f"Signal delivery failed: {exc}")
                return
            if delivered:
                cancelled_pending = 0
                try:
                    cancelled_pending = database.cancel_pending_orders(
                        reason=f"Replaced by newer {decision_type} signal",
                        symbol=symbol,
                        direction=decision_type,
                    )
                    if cancelled_pending:
                        logger.info("Cancelled %s stale pending %s order(s) for %s before saving new signal", cancelled_pending, decision_type, symbol)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to cancel stale pending orders before saving new signal: %s", exc)
                database.save_trade(decision)
                if decision.get("setup_id"):
                    try:
                        setup_memory.mark_entry_triggered(
                            setup_id=str(decision.get("setup_id")),
                            state_key=str((decision.get("setup_context") or {}).get("state_key") or ""),
                            trade_id=trade_id,
                            current_price=float(decision.get("current_price") or 0),
                            symbol=symbol,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Failed to link setup candidate %s to trade %s: %s", decision.get("setup_id"), trade_id, exc)
            else:
                telegram.send_error_alert("Signal delivery failed: Telegram returned False; trade was not saved.")
        elif decision_type == "WAIT":
            if send_hourly_now:
                telegram.send_message(_build_market_status_message(decision, all_results, database, config))
    except Exception as exc:
        telegram.send_error_alert(str(exc))

def _cross_path_distance_check(
    decision: Dict[str, Any],
    database: DatabaseService,
    config: Dict[str, Any],
    cross_distance_points: int = 200
) -> str | None:
    """Block new entry if too close to existing open trade in same direction.
    
    BUY: new entry must be LOWER than existing BUY (buy the dip).
    SELL: new entry must be HIGHER than existing SELL (sell the rally).
    Minimum gap: cross_distance_points (default 200 pts for gold).
    """
    direction = str(decision.get('decision', '')).upper()
    if direction not in {'BUY', 'SELL'}:
        return None

    signal = decision.get('signal', {}) or {}
    entry_info = signal.get('entry', {}) or {}
    try:
        entry_price = float(entry_info.get('price') or decision.get('current_price') or 0)
    except (TypeError, ValueError):
        return None
    if entry_price <= 0:
        return None

    symbol = str(decision.get("symbol") or config.get("symbol", "XAU/USD"))
    norm_sym = normalize_symbol(symbol)

    for trade in database.get_open_trades():
        trade_dir = str(trade.get('type') or trade.get('side') or '').upper()
        if trade_dir != direction:
            continue
        trade_sym = normalize_symbol(str(trade.get('symbol') or ''))
        if trade_sym != norm_sym:
            continue

        try:
            prev_entry = float(trade.get('entry_price') or 0)
        except (TypeError, ValueError):
            continue
        if prev_entry <= 0:
            continue

        pts = abs(price_to_points(entry_price - prev_entry, symbol=symbol))

        if pts < cross_distance_points:
            return (
                f"{direction} blocked: only {pts:.0f} pts from existing {direction} "
                f"@ {prev_entry:.2f} in {direction} (need ≥{cross_distance_points} pts)"
            )

        # Directional rule: BUY lower, SELL higher
        if direction == 'BUY' and entry_price >= prev_entry:
            return (
                f"BUY blocked: new entry {entry_price:.2f} is not lower than "
                f"existing BUY @ {prev_entry:.2f} (buy the dip rule — must be below)"
            )
        if direction == 'SELL' and entry_price <= prev_entry:
            return (
                f"SELL blocked: new entry {entry_price:.2f} is not higher than "
                f"existing SELL @ {prev_entry:.2f} (sell the rally rule — must be above)"
            )

    return None


def _latest_candle_extremes(data: Dict[str, Any]) -> tuple[float, float]:
    current = float(data.get("current_price") or 0.0)
    candles = (data.get("timeframes", {}).get("5m") or {}).get("data") or data.get("data") or []
    latest = candles[-1] if candles else {}
    high = float(latest.get("high") or current)
    low = float(latest.get("low") or current)
    return max(high, low), min(high, low)

async def run_analysis_async() -> None:
    base_config = load_config()
    for instrument in enabled_instruments(base_config):
        await _run_analysis_for_config(config_for_instrument(base_config, instrument))

def main() -> None:
    import asyncio
    asyncio.run(run_analysis_async())

if __name__ == "__main__":
    main()
