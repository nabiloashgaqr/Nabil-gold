"""Main analysis script.

Runs every 5 minutes via cron-job.org/GitHub Actions. Fetches market data, runs agents,
يطبق إدارة المخاطر وDecision، ثم يحفظ ويرسل الإشارة إذا كانت مؤهلة.
"""

from __future__ import annotations

# --- VPS: load .env if present (real env vars ALWAYS win over .env) ---
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()  # override=False: task-wrapper vars take precedence
except Exception:
    pass

import logging
import os
import sys
import html
import traceback
from copy import deepcopy
from datetime import datetime, timedelta, timezone
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
from services.setup_performance import SetupPerformanceService
from services.session_planner import SessionPlannerService
from utils.helpers import load_config, setup_logging, get_agent_weights
from utils import trading_rules as _tr
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
_BREAKEVEN_STATUSES = {"BE_HIT", "EXPIRED", "THESIS_EXIT", "MANUAL_CLOSE"}


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

# Terminal setup-memory states: a candidate here is a finished thesis and must
# never produce a live order. Mirrors SessionPlannerService.TERMINAL_SETUP_STATES.
TERMINAL_SETUP_STATES = {"INVALIDATED", "EXPIRED", "ENTRY_TRIGGERED"}


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



def _planned_order_type(
    config: Dict[str, Any],
    direction: str,
    entry: float,
    current_price: float,
    symbol: str,
    planned_stop: float | None = None,
) -> str:
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
    # Entries are MARKET or LIMIT. Never STOP.
    #
    # A STOP entry buys above the market or sells below it -- it pays a worse
    # price than the one available now, in exchange for waiting for the move
    # to prove itself. That is chasing, and it is the shape of the 2026-07-29
    # loss: BUY STOP at 4028.77 placed above the market while three qualified
    # agents read SELL, filled into a 310-point decline.
    #
    # A LIMIT is the opposite trade: it sells into a rally or buys into a dip,
    # always at a price better than the market. When the mapped level is on
    # the wrong side to do that, the entry is taken at the market instead of
    # waiting for a worse one. Nothing is skipped and nothing is chased.
    #
    # The market fill is only offered while the mapped stop still protects the
    # live price. A plan whose stop sits between the market and its own entry
    # -- price 3992 for a BUY mapped 4042/4039 -- cannot be repriced to the
    # market without inverting the trade, so it is refused outright rather
    # than shipped as an unprotected position. This is caught downstream by
    # validate_signal_before_send too; declaring it here keeps the refusal
    # legible instead of surfacing as a late validation error.
    stop_loss = _safe_float(planned_stop, 0.0)
    if direction == "BUY":
        if entry < current_price:
            return "BUY_LIMIT"
        if stop_loss > 0 and stop_loss >= current_price:
            return "NO_ENTRY"
        return "BUY_MARKET"
    if direction == "SELL":
        if entry > current_price:
            return "SELL_LIMIT"
        if stop_loss > 0 and stop_loss <= current_price:
            return "NO_ENTRY"
        return "SELL_MARKET"
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



def _resolve_reward_target(
    direction: str,
    entry_price: float,
    stop_loss: float,
    target_price: float,
    candidate: Dict[str, Any] | None,
    min_rr: float,
    symbol: str = "XAU/USD",
    min_tp1_rr: float = 0.0,
    prefer_far: bool = True,
    max_rr: float = 0.0,
    min_tp2_beyond_rr: float = 0.5,
    tp2_multiple: float = 2.0,
    risk_cfg: Dict[str, Any] | None = None,
) -> tuple[float, float, str | None]:
    """Pick TP1 and TP2 from the liquidity map.

    Returns (tp1, tp2, rejection_reason).

    TARGET POLICY (operator directive, 2026-08-04): look at FAR liquidity
    first, then near -- far liquidity is better. TP2 is the level the trade
    is actually held for, so it aims at the FURTHEST real pool whose reward
    the risk justifies (rr >= min_rr), never beyond max_rr when a cap is set,
    so the pick stays a level the map actually drew. Near pools are used only
    when nothing far qualifies. Before this directive the nearest qualifying
    pool was taken, which left the move's real objective unaimed at -- the
    same gap the manual analyst flagged on 2026-07-30 when his extended target
    sat 257 points beyond the system's shipped TP2.

    TP1 stays the nearest usable real level short of TP2 (at least
    min_tp1_rr away): it books the first half and arms breakeven early, while
    the runner is aimed at the far objective. A level closer than min_tp1_rr
    is skipped rather than rejected; only if nothing usable remains does TP1
    fall back to the midpoint of the run.

    Targets are still never invented: TP2 must be an actual level from the
    liquidity map or the plan, otherwise the leg is rejected.
    """
    risk = abs(entry_price - stop_loss)
    if risk <= 0 or entry_price <= 0:
        return target_price, target_price, None

    def _rr(level: float) -> float:
        return abs(level - entry_price) / risk

    def _is_ahead(level: float) -> bool:
        return level > entry_price if direction == "BUY" else level < entry_price

    # Collect every structural level ahead of us, nearest first.
    levels: List[float] = []
    if target_price > 0 and _is_ahead(target_price):
        levels.append(target_price)
    details = (candidate or {}).get("details") or {}
    liquidity_map = details.get("liquidity") if isinstance(details, dict) else {}
    side_key = "buy_side" if direction == "BUY" else "sell_side"
    if isinstance(liquidity_map, dict):
        for raw in liquidity_map.get(side_key) or []:
            level = _safe_float(raw, 0.0)
            if level > 0 and _is_ahead(level):
                levels.append(level)
    for key in ("secondary_target", "extended_target", "next_liquidity", "target_price_2"):
        level = _safe_float((candidate or {}).get(key), 0.0)
        if level > 0 and _is_ahead(level):
            levels.append(level)

    ordered = sorted(set(levels), key=lambda lv: abs(lv - entry_price))
    # Operator directive 2026-08-07c: an approved plan is NEVER refused on
    # reward. Targets compare liquidity against stop ratios and ship the
    # FARTHER objective:
    #     TP1 = farther(0.8R of the stop, nearest pool)
    #     TP2 = farther(1.5R of the stop, farthest pool)
    # Example (operator's own): stop 270 -> ratio TP1 216~220 pts; a pool at
    # 250 is farther -> TP1 = 250. Minimums are enforced BY CONSTRUCTION;
    # nothing is rejected, so the agent-approved map always ships.
    def _dist(lv: float) -> float:
        return abs(lv - entry_price)

    def _level(rr_r: float) -> float:
        return entry_price + risk * rr_r if direction == "BUY" else entry_price - risk * rr_r

    # 2026-08-07 audit: the target law lives ONCE in utils.trading_rules.
    risk_cfg = risk_cfg or {}
    tp1, tp2, _used = _tr.targets_law(
        direction=direction, entry=entry_price,
        risk_price=abs(entry_price - stop_loss),
        levels=ordered, cfg={}, symbol=symbol, risk_cfg=risk_cfg)
    return round(tp1, 2), round(tp2, 2), None


def _stop_from_liquidity_points(
    direction: str,
    entry: float,
    candidate: Dict[str, Any] | None,
    rule_cfg: Dict[str, Any],
    symbol: str,
) -> float:
    """Thin wrapper over the single source of truth
    (utils.trading_rules.stop_from_liquidity_points). 2026-08-07 audit: the
    formula lives ONCE in the loader; every door delegates."""
    details = ((candidate or {}).get("details") or {}) if isinstance(candidate, dict) else {}
    liquidity = details.get("liquidity") or {}
    return _tr.stop_from_liquidity_points(
        direction=direction, entry=entry, liquidity_map=liquidity,
        cfg={}, symbol=symbol, rule=rule_cfg)


def _planner_trade_levels(
    config: Dict[str, Any],
    *,
    direction: str,
    entry_price: float,
    stop_loss: float,
    target_price: float,
    symbol: str,
    candidate: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    risk_cfg = (config.get("risk_settings") or {}) if isinstance(config, dict) else {}
    min_sl_points = _safe_float(risk_cfg.get("min_sl_distance_points"), 0.0)
    min_sl_distance = points_to_price(min_sl_points, symbol=symbol) if min_sl_points > 0 else 0.0
    max_rr = _safe_float(risk_cfg.get("max_rr_ratio"), 0.0)
    target_method = "mapped_target"
    floor_applied = False

    adjusted_stop = float(stop_loss)
    structural_points = abs(price_to_points(entry_price - adjusted_stop, symbol=symbol))

    # The floor exists because gold can travel 50-100 points in seconds, so a
    # stop pinned to a narrow POI is noise-bait. But a single fixed distance
    # ignores how volatile the session actually is: on XAU it engaged on every
    # plan, multiplying structural risk by up to 18x and pushing reward-to-risk
    # below the minimum even for sound setups.
    #
    # Scale it instead. The structural stop already embeds an ATR buffer, so a
    # multiple of it tracks volatility, bounded so it can neither collapse to
    # the POI width nor exceed the configured ceiling.
    # Operator directive (2026-08-07b) -- the liquidity rule for stops, same
    # arithmetic as RiskManagementAgent._stop_from_liquidity_points (ONE rule,
    # BOTH doors): ignore liquidity closer than 200 pts; stop = first eligible
    # level + 70 safety; past 400 or none -> 400 directly. Band [270, 400].
    rule_cfg = (risk_cfg.get("stop_from_liquidity") or {}) if isinstance(risk_cfg, dict) else {}
    rule_active = bool(rule_cfg.get("enabled", False))
    if rule_active:
        structural_points = _stop_from_liquidity_points(
            direction, entry_price, candidate, rule_cfg, symbol)
        min_sl_distance = points_to_price(structural_points, symbol=symbol)
        adjusted_stop = entry_price - min_sl_distance if direction == "BUY" else entry_price + min_sl_distance
        floor_applied = True

    if not rule_active and min_sl_distance > 0 and abs(entry_price - adjusted_stop) < min_sl_distance:
        adjusted_stop = entry_price - min_sl_distance if direction == "BUY" else entry_price + min_sl_distance
        floor_applied = True

    # Targets must be anchored to structure. Widening the stop to the risk
    # floor used to trigger a purely ratio-based target (floor x ATR multiple),
    # which discarded the mapped liquidity entirely -- publishing TP1 460 pts
    # beyond a target that was only 40 pts away, and a 2.25R label on a trade
    # whose real reward-to-risk was 0.58.
    min_rr = _safe_float(risk_cfg.get("min_rr_ratio"), 1.5) or 1.5
    # 2026-08-07 phase 1: ratios/multiples/bands are read ONLY inside
    # utils.trading_rules; the doors pass the raw risk block and delegate.
    tp1, tp2, reject_reason = _resolve_reward_target(
        direction, entry_price, adjusted_stop, target_price, candidate, min_rr,
        symbol=symbol,
        risk_cfg=risk_cfg,
    )
    if reject_reason:
        return {
            "entry_price": round(entry_price, 2),
            "stop_loss": round(adjusted_stop, 2),
            "tp1": 0.0,
            "tp2": 0.0,
            "rr": 0.0,
            "floor_applied": floor_applied,
            "target_method": "rejected_insufficient_reward",
            "reject_reason": reject_reason,
            "min_sl_distance_points": round(min_sl_points, 1),
        }
    if tp2 != target_price:
        target_method = "extended_liquidity"
    if floor_applied and target_method == "mapped_target":
        target_method = "mapped_target_with_floored_sl"

    # 2026-08-07 audit: the max_rr cap is removed here as well -- with the
    # liquidity rule (stop >= 270 pts) it is mathematically inert, and as a
    # directive the operator's band/double rule is the only target law.
    risk = abs(adjusted_stop - entry_price)
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



def _max_pending_distance_points(config: Dict[str, Any]) -> float:
    """Furthest a resting order may sit from the market, in points.

    Derived from the map's own geometry rather than a new invented number:
    ``session_planner.max_primary_zone_width_points`` is the widest zone the
    planner is allowed to publish, so an entry further away than that cannot
    be a mapped level -- there is no zone that reaches it.

    Set ``execution_guards.max_pending_distance_points`` to override, or 0 to
    disable the check.
    """
    guards = (config.get("execution_guards") or {}) if isinstance(config, dict) else {}
    if "max_pending_distance_points" in guards:
        return max(0.0, _safe_float(guards.get("max_pending_distance_points"), 0.0))
    planner = (config.get("session_planner") or {}) if isinstance(config, dict) else {}
    return max(0.0, _safe_float(planner.get("max_primary_zone_width_points"), 450.0))


def _unreachable_pending_violation(
    decision: Dict[str, Any], config: Dict[str, Any]
) -> str | None:
    """Refuse a resting order the market would have to travel absurdly far to fill.

    2026-07-31, 13:16 UTC. With SELL d917b1d5 already live and deep in
    profit, the system published TRADE_..._6e31ddf6: a SELL LIMIT at 4076.93
    while price was 4023.21 -- 537 points ABOVE a market that was collapsing
    toward the very target its own live trade was about to hit. Thirty
    minutes later it cancelled the order itself: "market covered 61% of
    target path without fill".

    The cancellation was right. The placement was the fault. That order could
    only ever fill on a 537-point rally against the system's own winning
    thesis, and while it sat there it occupied the map.

    Distance is measured to the entry the order will actually rest at, and
    only in the direction that would have to be travelled to reach it. An
    entry the market has already passed through is a different case entirely
    and is left to the normal fill logic.
    """
    limit = _max_pending_distance_points(config)
    if limit <= 0:
        return None

    side = str(decision.get("decision") or "").upper()
    if side not in {"BUY", "SELL"}:
        return None
    signal = decision.get("signal") or {}
    if not isinstance(signal, dict):
        return None

    order_type = str(signal.get("order_type") or "").upper()
    if order_type.endswith("MARKET"):
        return None

    symbol = str(decision.get("symbol") or config.get("symbol", "XAU/USD"))
    entry_info = signal.get("entry") or {}
    entry = _safe_float(entry_info.get("price") if isinstance(entry_info, dict) else None, 0.0)
    current = _safe_float(decision.get("current_price"), 0.0)
    if entry <= 0 or current <= 0:
        return None

    # Points the market must travel to reach the resting entry. A SELL LIMIT
    # sits above the market and needs a rally; a BUY LIMIT sits below and
    # needs a decline. Anything on the other side is already reachable.
    if side == "SELL":
        travel = price_to_points(entry - current, symbol=symbol)
    else:
        travel = price_to_points(current - entry, symbol=symbol)
    if travel <= 0:
        return None
    if travel <= limit:
        return None

    return (
        f"resting {side} entry {entry:.2f} is {travel:.0f} pts from the market "
        f"at {current:.2f}, beyond the {limit:.0f}-pt reach of the widest "
        "mapped zone; it could only fill on a move that would invalidate the "
        "setup it is based on"
    )


def _counter_to_live_winner_violation(
    decision: Dict[str, Any],
    config: Dict[str, Any],
    open_trades: List[Dict[str, Any]] | None,
) -> str | None:
    """Refuse a resting order that is a bet against the system's own winner.

    The same 2026-07-31 incident from the other side. A SELL LIMIT placed
    537 points above the market fills only if price rallies that far -- which
    is precisely the move that would have destroyed the live SELL then
    running to its target. The system was, in effect, hedging against its own
    correct call while that call was winning.

    Narrow by construction. It only refuses a *resting* order in the *same
    direction* as a live trade that is *already in profit* and whose entry
    the new order sits *worse than*. A genuine pyramid -- adding at a better
    price, or scaling a loser's recovery -- is untouched, and a market order
    is never blocked.
    """
    guards = (config.get("execution_guards") or {}) if isinstance(config, dict) else {}
    if guards.get("block_pending_behind_live_winner") is False:
        return None

    side = str(decision.get("decision") or "").upper()
    if side not in {"BUY", "SELL"}:
        return None
    signal = decision.get("signal") or {}
    if not isinstance(signal, dict):
        return None
    if str(signal.get("order_type") or "").upper().endswith("MARKET"):
        return None

    symbol = str(decision.get("symbol") or config.get("symbol", "XAU/USD"))
    normalized = normalize_symbol(symbol)
    entry_info = signal.get("entry") or {}
    entry = _safe_float(entry_info.get("price") if isinstance(entry_info, dict) else None, 0.0)
    if entry <= 0:
        return None

    for trade in open_trades or []:
        if normalize_symbol(trade.get("symbol") or symbol) != normalized:
            continue
        if str(trade.get("status") or "").upper() not in {"OPEN", "PARTIAL", "TP1_HIT"}:
            continue
        if str(trade.get("type") or trade.get("side") or "").upper() != side:
            continue
        live_pnl = _safe_float(
            trade.get("current_pnl_points", trade.get("current_pnl")), 0.0
        )
        if live_pnl <= 0:
            continue
        live_entry = _safe_float(trade.get("entry_price"), 0.0)
        if live_entry <= 0:
            continue
        # "Worse than" the live entry: for a SELL that means higher (needs a
        # rally against the winner); for a BUY, lower.
        worse = entry > live_entry if side == "SELL" else entry < live_entry
        if not worse:
            continue
        gap = abs(price_to_points(entry - live_entry, symbol=symbol))
        return (
            f"resting {side} entry {entry:.2f} sits {gap:.0f} pts worse than live "
            f"{side} {trade.get('id') or ''} (entry {live_entry:.2f}, +{live_pnl:.0f} pts "
            "open); it can only fill on the move that would end the winning trade"
        ).replace("  ", " ")
    return None


def _rejected_setup_execution_block(
    decision: Dict[str, Any], config: Dict[str, Any]
) -> str | None:
    """Refuse to trade a setup the SMC agent ranked and declined.

    ``selection_role`` carries SMCAgent's verdict on its own candidates.
    REJECTED means it was ranked and not chosen -- neither the primary thesis
    nor a qualifying standby. Publishing one is trading a setup the agent that
    discovered it did not believe in.

    2026-07-31, TRADE_20260731_152110_326407_a5520ee6: a SELL LIMIT went out
    with "role REJECTED · quality C · dominance 47.6 · reach 40.8". The
    planner would have refused it at two separate floors
    (min_primary_dominance 50, min_return_probability 42); the dual-agent path
    does not run those floors, so nothing stopped it.

    This is a quality gate, not a risk gate: no stop, target, size or ratio is
    affected. Set ``execution_guards.allow_rejected_setups: true`` to restore
    the previous behaviour.
    """
    guards = (config.get("execution_guards") or {}) if isinstance(config, dict) else {}
    if guards.get("allow_rejected_setups") is True:
        return None

    side = str(decision.get("decision") or "").upper()
    if side not in {"BUY", "SELL"}:
        return None

    setup = decision.get("setup_context") or {}
    if not isinstance(setup, dict):
        return None
    role = str(setup.get("selection_role") or "").upper()
    # No role at all means the snapshot predates the labelling; do not block
    # on an absence of information.
    if not role or role in _SELECTED_SETUP_ROLES:
        return None

    dominance = _safe_float(setup.get("thesis_dominance_score"), 0.0)
    reach = _safe_float(setup.get("return_probability_score"), 0.0)
    return (
        f"setup was ranked {role} by the SMC agent — it was not chosen as the "
        f"primary thesis or a qualifying standby (dominance {dominance:.1f}, "
        f"return probability {reach:.1f}); trading it overrides the agent that "
        "found it"
    )


def validate_signal_before_send(
    decision: Dict[str, Any],
    config: Dict[str, Any],
    open_trades: List[Dict[str, Any]] | None = None,
) -> List[str]:
    """Final arithmetic check on a finished signal. Returns violations.

    Every execution fault found so far was individually invisible and jointly
    obvious: a first target 5 points from entry against a 150-point stop, a
    protection threshold that could never be reached before that target, a
    stop distance quoted from config rather than from the trade, a thesis
    whose stated objective sat on the wrong side of the entry.

    None of them needed market knowledge to catch -- only for someone to
    check the finished numbers against each other once. That is what this
    does, at the single point where a signal becomes real.

    A non-empty list means the signal is arithmetically incoherent and must
    not be sent or saved. It is deliberately not a strategy opinion: it only
    rejects what cannot be true.
    """
    violations: List[str] = []

    side = str(decision.get("decision") or "").upper()
    if side not in {"BUY", "SELL"}:
        return violations

    symbol = str(decision.get("symbol") or config.get("symbol", "XAU/USD"))
    signal = decision.get("signal") or {}
    if not isinstance(signal, dict) or not signal:
        return ["signal payload is missing"]

    entry_info = signal.get("entry") or {}
    entry = _safe_float(entry_info.get("price") if isinstance(entry_info, dict) else None, 0.0)
    if entry <= 0:
        entry = _safe_float(decision.get("current_price"), 0.0)
    stop = _safe_float(signal.get("stop_loss"), 0.0)
    tp1 = _safe_float(signal.get("tp1"), 0.0)
    tp2 = _safe_float(signal.get("tp2"), 0.0)

    if entry <= 0 or stop <= 0:
        return [f"entry ({entry}) and stop ({stop}) must both be positive"]

    ahead = (lambda level: level > entry) if side == "BUY" else (lambda level: level < entry)
    behind = (lambda level: level < entry) if side == "BUY" else (lambda level: level > entry)

    # 1. Geometry. A stop on the wrong side is not a stop.
    if not behind(stop):
        violations.append(
            f"stop {stop:.2f} is not protective for a {side} entered at {entry:.2f}"
        )
    for label, level in (("tp1", tp1), ("tp2", tp2)):
        if level > 0 and not ahead(level):
            violations.append(
                f"{label} {level:.2f} is not ahead of a {side} entered at {entry:.2f}"
            )

    risk_points = abs(price_to_points(entry - stop, symbol=symbol))
    if risk_points <= 0:
        violations.append("risk distance is zero; the stop sits on the entry")
        return violations

    # 2. Reward. Reaching TP1 arms the breakeven stop, so a token first target
    #    converts a correct call into a flat trade.
    risk_cfg = (config.get("risk_settings") or {}) if isinstance(config, dict) else {}
    min_tp1_rr = _tr.target_ratios(config or {}, default_min_tp1_rr=0.0)["min_tp1_rr"]
    min_rr = _safe_float(risk_cfg.get("min_rr_ratio"), 0.0)
    tp1_rr = abs(price_to_points(tp1 - entry, symbol=symbol)) / risk_points if tp1 > 0 else 0.0
    tp2_rr = abs(price_to_points(tp2 - entry, symbol=symbol)) / risk_points if tp2 > 0 else 0.0

    if min_tp1_rr > 0 and tp1 > 0 and tp1_rr < min_tp1_rr:
        violations.append(
            f"tp1 is only {tp1_rr:.2f}R from entry (minimum {min_tp1_rr:.2f}R); "
            "reaching it would arm breakeven before the trade has travelled"
        )
    if min_rr > 0 and tp2 > 0 and tp2_rr < min_rr:
        violations.append(f"tp2 is only {tp2_rr:.2f}R from entry (minimum {min_rr:.2f}R)")
    if tp1 > 0 and tp2 > 0 and abs(tp2 - entry) < abs(tp1 - entry):
        violations.append("tp2 is nearer than tp1")

    # 3. Coherence between protection and the first target.
    #
    # Two wirings exist, and the check must judge the one that actually runs:
    #   * auto_move_sl_to_entry_after_tp1 (the wired promise): on a TP1 touch
    #     the stop moves to entry whatever TP1's value, so a "breakeven beyond
    #     TP1" ordering contradiction cannot arise. What must hold instead is
    #     that TP1 is far enough for the manager's R-guard (min_breakeven_rr)
    #     to let the touch arm the stop -- otherwise TP1 hits, the partial
    #     books, and the promised protection still does not apply.
    #   * legacy distance-only breakeven (auto_be false): a +N trigger beyond
    #     TP1 can never fire before the target it claims to precede.
    #
    # The 2026-08-04 A+ 97.8% map died under the old text: TP1 140.5 pts away
    # against a 150-pt trigger -- refused -- while the manager would have
    # armed breakeven at TP1 anyway. The validator judged a promise the
    # engine no longer exclusively makes.
    tm_cfg = (config.get("trade_management") or {}) if isinstance(config, dict) else {}
    breakeven_points = _safe_float(tm_cfg.get("early_breakeven_points"), 0.0)
    tp1_points = abs(price_to_points(tp1 - entry, symbol=symbol)) if tp1 > 0 else 0.0
    auto_be_at_tp1 = bool(tm_cfg.get("auto_move_sl_to_entry_after_tp1", True))
    if auto_be_at_tp1:
        min_be_rr = _safe_float(tm_cfg.get("min_breakeven_rr"), 0.0)
        if tp1 > 0 and min_be_rr > 0 and risk_points > 0 and tp1_rr < min_be_rr:
            violations.append(
                f"tp1 is only {tp1_rr:.2f}R away; the manager arms breakeven at TP1 "
                f"only from {min_be_rr:.2f}R travelled, so the promised protection "
                "would not apply"
            )
    elif breakeven_points > 0 and tp1_points > 0 and breakeven_points >= tp1_points:
        violations.append(
            f"breakeven trigger (+{breakeven_points:.0f} pts) is not reachable before "
            f"tp1 ({tp1_points:.0f} pts away); the promised protection cannot apply"
        )

    # 4. Reachability. A resting order the market cannot plausibly reach is
    #    not a plan, and one that can only fill by destroying a live winner is
    #    a bet against the system's own thesis. Both were published on
    #    2026-07-31 as a single SELL LIMIT 537 pts above a collapsing market.
    unreachable = _unreachable_pending_violation(decision, config)
    if unreachable:
        violations.append(unreachable)
    counter_bet = _counter_to_live_winner_violation(decision, config, open_trades)
    if counter_bet:
        violations.append(counter_bet)

    # 5. Provenance. A setup the SMC agent ranked and declined must not be
    #    traded, whatever its arithmetic looks like.
    rejected_setup = _rejected_setup_execution_block(decision, config)
    if rejected_setup:
        violations.append(rejected_setup)

    return violations


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



def _planner_display_confidence(
    base_decision: Dict[str, Any],
    plan: Dict[str, Any],
    candidate: Dict[str, Any],
    config: Dict[str, Any],
    *,
    direction: str,
) -> float:
    sig_cfg = (config.get("signal_requirements") or {}) if isinstance(config, dict) else {}
    min_agent_conf = float(sig_cfg.get("agent_min_confidence", 70) or 70)
    details = base_decision.get("agent_details") or {}
    support_confidences: List[float] = []
    oppose_confidences: List[float] = []
    for key in ["technical", "classical", "smc", "price_action", "multitimeframe"]:
        detail = (details or {}).get(key)
        if not isinstance(detail, dict):
            continue
        agent_direction = str(detail.get("direction") or "WAIT").upper()
        agent_confidence = _safe_float(detail.get("confidence"), 0.0)
        if agent_confidence < min_agent_conf:
            continue
        if agent_direction == direction:
            support_confidences.append(agent_confidence)
        elif agent_direction in {"BUY", "SELL"}:
            # A qualified agent voting the other way is evidence against the
            # thesis and is weighted with the same threshold as a supporter.
            oppose_confidences.append(agent_confidence)

    dominance = _safe_float(candidate.get("thesis_dominance_score"), 0.0)
    quality_score = _safe_float(candidate.get("quality_score"), _safe_float((candidate.get("setup_quality") or {}).get("score"), 0.0))
    if support_confidences:
        avg_support = sum(support_confidences) / max(len(support_confidences), 1)
        display_confidence = avg_support * 0.75 + dominance * 0.25
    else:
        display_confidence = max(dominance, quality_score * 0.80, 60.0)

    # Symmetry: confirmation adds, contradiction subtracts.
    #
    # Only agreement moved this number before. A qualified agent voting the
    # opposite way at 95% produced exactly the same confidence as an
    # unqualified one at 27%, and a macro read opposing the trade at 90% cost
    # nothing while a supporting one added 2.0. Every disagreement in the
    # system was silently rounded down to zero, so the published figure only
    # ever described the evidence that agreed with the trade.
    context_confirmation = _planner_context_confirmation(base_decision, config, direction)
    if len(support_confidences) >= 2 and context_confirmation.get("allow"):
        display_confidence += 2.0

    if oppose_confidences:
        avg_oppose = sum(oppose_confidences) / len(oppose_confidences)
        # Scale with both how many disagree and how strongly, so two strong
        # opponents cost more than one marginal one.
        display_confidence -= (avg_oppose / 100.0) * 6.0 * len(oppose_confidences)

    macro_direction = _macro_direction_for(base_decision)
    macro_bias = str(macro_direction.get("bias") or "").upper()
    macro_side = {"BULLISH_GOLD": "BUY", "BEARISH_GOLD": "SELL"}.get(macro_bias)
    macro_conf = _safe_float(macro_direction.get("confidence"), 0.0)
    if macro_side and macro_side != direction and macro_conf > 0:
        display_confidence -= 5.0 if macro_conf >= 80 else 3.0 if macro_conf >= 55 else 1.5

    cap = 95.0 if len(support_confidences) >= 3 else 92.0 if len(support_confidences) >= 2 else 88.0
    return round(max(50.0, min(cap, display_confidence)), 1)



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
    # Keep the structural stop: a market conversion below reprices from the
    # mapped invalidation, not from an already-floored stop.
    raw_stop_loss = stop_loss
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
        candidate=candidate,
    )
    if levels.get("reject_reason"):
        logger.info(
            "Session-plan leg rejected for %s %s: %s",
            symbol,
            direction,
            levels.get("reject_reason"),
        )
        return None
    stop_loss = levels["stop_loss"]
    order_type = f"{direction}_MARKET" if force_market else _planned_order_type(
        config, direction, entry_price, current_price, symbol, planned_stop=stop_loss,
    )
    if order_type == "NO_ENTRY":
        logger.info(
            "Session-plan leg skipped for %s %s: mapped stop %.2f does not "
            "protect a market fill at %.2f, and a stop entry is not permitted",
            symbol, direction, stop_loss, current_price,
        )
        return None
    # Price sitting inside the mapped area is the setup arriving, not a reason
    # to stand down. This branch used to return None whenever the leg priced as
    # MARKET, so a confirmed map produced an order only while price was still
    # far away, and refused one the moment price actually reached the level --
    # the map was published, price traded through the zone, and nothing was
    # ever placed.
    #
    # Convert instead of abandoning: the leg has already cleared the reward,
    # risk-floor and reject_reason checks above, and everything downstream
    # (planner gate, day-map sanity, duplicate and pending governors) still
    # runs. Entry is repriced to the live price so the recorded fill is the
    # real one, and the executed leg is never better than the mapped edge.
    market_conversion = False
    if order_type.endswith("MARKET") and not force_market:
        if not bool(_split_execution_cfg(config).get("convert_touched_zone_to_market", True)):
            logger.info(
                "Session-plan leg skipped for %s %s: price inside the mapped area "
                "and market conversion is disabled",
                symbol, direction,
            )
            return None
        market_conversion = True
        entry_price = current_price
        levels = _planner_trade_levels(
            config,
            direction=direction,
            entry_price=entry_price,
            stop_loss=raw_stop_loss,
            target_price=target_price,
            symbol=symbol,
            candidate=candidate,
        )
        if levels.get("reject_reason"):
            logger.info(
                "Session-plan market conversion rejected for %s %s: %s",
                symbol, direction, levels.get("reject_reason"),
            )
            return None
        stop_loss = levels["stop_loss"]
        logger.info(
            "Session-plan leg converted to market for %s %s at %.2f "
            "(price reached the mapped area)",
            symbol, direction, entry_price,
        )
    entry_kind = "MARKET" if (force_market or market_conversion) else order_type.split("_")[-1]
    zone = candidate.get("poi_zone") or {}
    if isinstance(zone, dict) and zone.get("top") is not None and zone.get("bottom") is not None:
        low = min(_safe_float(zone.get("top"), entry_price), _safe_float(zone.get("bottom"), entry_price))
        high = max(_safe_float(zone.get("top"), entry_price), _safe_float(zone.get("bottom"), entry_price))
    else:
        low = _safe_float(candidate.get("poi_low"), entry_price)
        high = _safe_float(candidate.get("poi_high"), entry_price)
        if low <= 0 or high <= 0:
            low = high = entry_price

    # Publish the area the planner actually mandates, not the raw POI.
    #
    # `session_planner.min_entry_zone_width_points` (60) is enforced by
    # SessionPlannerService._enforce_min_zone_width, which widens a narrow POI
    # symmetrically around the reference entry. That method is called from
    # _zone_payload -- the planner's own view of the map -- but this function
    # builds the order that is actually sent, and it read the raw POI instead.
    #
    # 2026-07-31, TRADE_..._b4f85832: the card published "Entry zone
    # 4029.85 → 4033.69", which is 38.4 points. The floor is 60. The planner,
    # asked directly, returns 4028.77 → 4034.77 = exactly 60.0.
    #
    # A published area narrower than the floor is the precise failure the
    # floor exists to prevent: the order rests at one price inside it, and a
    # touch that misses that price by a few points leaves the plan unfilled
    # while price runs to target. Risk does not drift from widening --
    # zone_touch_activation carries the stop the same distance it moves the
    # entry (preserve_planned_risk=true).
    if not (force_market or market_conversion) and high > low:
        try:
            widened_low, widened_high, _widened = SessionPlannerService(config)._enforce_min_zone_width(
                low, high, entry_price=entry_price, symbol=symbol,
            )
            if _widened and widened_low > 0 and widened_high > widened_low:
                logger.info(
                    "Entry area widened to the configured floor for %s %s: "
                    "%.2f-%.2f (%.0f pts) -> %.2f-%.2f (%.0f pts)",
                    symbol, direction, low, high,
                    abs(price_to_points(high - low, symbol=symbol)),
                    widened_low, widened_high,
                    abs(price_to_points(widened_high - widened_low, symbol=symbol)),
                )
                low, high = widened_low, widened_high
        except Exception as exc:  # noqa: BLE001 - never block a valid signal
            logger.warning("Could not apply the minimum entry-zone width: %s", exc)
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
            "confidence": _planner_display_confidence(base_decision, plan, candidate, config, direction=direction),
            "entry_mode": "session_plan_ladder_market" if (force_market or market_conversion) else "session_plan_ladder",
            "entry_path": 3,
            "reasons": [
                f"Session plan {plan.get('scenario_type')} ({role})",
                f"Execution leg: {hierarchy.get('execution_leg_label')}",
                *([f"Planner SL floored to {levels.get('min_sl_distance_points', 0):.0f} points minimum risk distance."] if levels.get("floor_applied") else []),
                f"Morning/session planner prepared this pending thesis before the move.",
            ],
            # Two different measurements, reported separately instead of being
            # blended. Mixing them (grade from the plan, score from max() of
            # both scales) produced nonsense like "C 100.0" -- a plan graded C
            # displaying a POI score of 100.
            "quality": {
                "grade": candidate.get("quality_grade") or plan.get("planner_grade") or "B",
                "score": _safe_float(
                    candidate.get("quality_score"),
                    _safe_float(plan.get("planner_confidence"), 0.0),
                ),
            },
            "planner_quality": {
                "grade": plan.get("planner_grade") or "B",
                "score": _safe_float(plan.get("planner_confidence"), 0.0),
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
            "low": round(current_price if (force_market or market_conversion) else low, 2),
            "high": round(current_price if (force_market or market_conversion) else high, 2),
            "kind": entry_kind,
            "order_type": order_type,
            "basis": basis_override or f"{hierarchy.get('execution_leg_label')} · session plan",
            "current_price": round(current_price, 2),
            "distance_points": 0.0 if (force_market or market_conversion) else abs(price_to_points(entry_price - current_price, symbol=symbol)),
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
        "risk_summary": f"Session planner {hierarchy.get('execution_leg_label')} {'market' if (force_market or market_conversion) else 'pending'} · {levels.get('target_method')}",
        "execution_leg": hierarchy.get("execution_leg"),
        "execution_leg_label": hierarchy.get("execution_leg_label"),
        "target_method": levels.get("target_method"),
    }
    decision["execution_leg"] = hierarchy.get("execution_leg")
    decision["execution_leg_label"] = hierarchy.get("execution_leg_label")
    return decision



def _macro_direction_for(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Read the macro bias payload, wherever this decision carries it."""
    news_context = decision.get("news_context") or {}
    macro_agent = (news_context.get("macro") or {}) if isinstance(news_context, dict) else {}
    macro_direction = (macro_agent.get("macro_direction") or {}) if isinstance(macro_agent, dict) else {}
    if not macro_direction:
        market_context = decision.get("market_context") or {}
        if isinstance(market_context, dict):
            macro_direction = market_context.get("macro_direction") or {}
    return macro_direction if isinstance(macro_direction, dict) else {}


def _planner_context_confirmation(decision: Dict[str, Any], config: Dict[str, Any], side: str) -> Dict[str, Any]:
    sig_cfg = (config.get("signal_requirements") or {}) if isinstance(config, dict) else {}
    two_agent_cfg = (sig_cfg.get("two_agent_entry") or {}) if isinstance(sig_cfg, dict) else {}
    macro_cfg = (two_agent_cfg.get("macro_confirmation") or {}) if isinstance(two_agent_cfg, dict) else {}
    gemini_cfg = (two_agent_cfg.get("gemini_confirmation") or {}) if isinstance(two_agent_cfg, dict) else {}
    macro_min = float(macro_cfg.get("min_confidence", 55) or 55)
    gemini_min = float(gemini_cfg.get("min_confidence", 70) or 70)

    news_context = decision.get("news_context") or {}
    macro_agent = (news_context.get("macro") or {}) if isinstance(news_context, dict) else {}
    macro_direction = (macro_agent.get("macro_direction") or {}) if isinstance(macro_agent, dict) else {}
    macro_bias = str(macro_direction.get("bias") or "").upper()
    macro_conf = _safe_float(macro_direction.get("confidence"), 0.0)
    expected_macro = "BULLISH_GOLD" if side == "BUY" else "BEARISH_GOLD"
    if macro_bias == expected_macro and macro_conf >= macro_min:
        return {
            "allow": True,
            "source": "macro",
            "confidence": round(macro_conf, 1),
            "reason": f"macro context confirms {side} ({macro_conf:.0f}% ≥ {macro_min:.0f}%)",
        }

    gemini_macro = decision.get("gemini_macro_review") or {}
    if isinstance(gemini_macro, dict) and gemini_macro.get("available"):
        verdict = str(gemini_macro.get("macro_verdict") or gemini_macro.get("verdict") or "").upper()
        conf = _safe_float(gemini_macro.get("confidence"), 0.0)
        if verdict == expected_macro and conf >= gemini_min:
            return {
                "allow": True,
                "source": "gemini",
                "confidence": round(conf, 1),
                "reason": f"gemini macro review confirms {side} ({conf:.0f}% ≥ {gemini_min:.0f}%)",
            }

    gemini_review = decision.get("gemini_review") or {}
    if isinstance(gemini_review, dict) and gemini_review.get("available"):
        verdict = str(gemini_review.get("verdict") or gemini_review.get("signal") or gemini_review.get("opinion") or "").upper()
        conf = _safe_float(gemini_review.get("confidence"), 0.0)
        if verdict == side and conf >= gemini_min:
            return {
                "allow": True,
                "source": "gemini",
                "confidence": round(conf, 1),
                "reason": f"gemini signal review confirms {side} ({conf:.0f}% ≥ {gemini_min:.0f}%)",
            }

    gemini_analysis = decision.get("gemini_analysis") or {}
    if isinstance(gemini_analysis, dict) and gemini_analysis.get("available"):
        bias = str(gemini_analysis.get("market_bias") or gemini_analysis.get("verdict") or "").upper()
        conf = _safe_float(gemini_analysis.get("confidence"), 0.0)
        if bias == side and conf >= gemini_min:
            return {
                "allow": True,
                "source": "gemini",
                "confidence": round(conf, 1),
                "reason": f"gemini market context confirms {side} ({conf:.0f}% ≥ {gemini_min:.0f}%)",
            }

    return {"allow": False}


def _planner_execution_gate(decision: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    side = str(decision.get("decision") or "").upper()
    # The planner is a separate admission path: it maps a direction in advance
    # and waits at a level, rather than reacting to the current bar. The live
    # consensus therefore often reads WAIT while a confirmed map exists, and
    # falling back to the mapped bias is what lets those pending orders be
    # placed at all. Without it a READY map is published every cycle and no
    # order is ever created, because the gate closes before the agents are
    # even counted. The count below still has to pass on its own.
    if side not in {"BUY", "SELL"}:
        plan = decision.get("session_plan") or {}
        if isinstance(plan, dict) and plan.get("plan_ready"):
            side = str(plan.get("session_bias") or plan.get("authority_direction") or "").upper()
    if side not in {"BUY", "SELL"}:
        return {"allow": False, "reason": "no approved directional admission"}

    sig_cfg = (config.get("signal_requirements") or {}) if isinstance(config, dict) else {}
    min_agents = int(sig_cfg.get("min_agents_agree", 3) or 3)
    min_agent_conf = float(sig_cfg.get("agent_min_confidence", 70) or 70)
    details = decision.get("agent_details") or {}
    support_count = 0
    support_agents: list[str] = []
    oppose_agents: list[str] = []
    for key in ["technical", "classical", "smc", "price_action", "multitimeframe"]:
        detail = (details or {}).get(key)
        if not isinstance(detail, dict):
            continue
        direction = str(detail.get("direction") or "WAIT").upper()
        confidence = _safe_float(detail.get("confidence"), 0.0)
        if confidence < min_agent_conf:
            continue
        if direction == side:
            support_count += 1
            support_agents.append(key)
        elif direction in {"BUY", "SELL"}:
            oppose_agents.append(key)

    # Opposition used to be reported here and enforced only by the planner,
    # on the grounds that a second veto would be redundant. It is not: the
    # planner tests opposition when it *builds* a map, while this gate is the
    # last point that sees the agents as they are right now, immediately
    # before an order is created. Those are different moments, and two paths
    # exploit the gap:
    #
    #   - a revived map (_revive_recent_ready_plan) replays a snapshot built
    #     hours earlier and never re-tests the current agent split;
    #   - a WAIT cycle falls back to the plan's session_bias above, so the
    #     agents are re-counted against a direction the live consensus did
    #     not choose.
    #
    # The result was an admission printed as "3 qualified agents aligned"
    # while three qualified agents were arguing the other way. Apply the same
    # ceiling the planner uses, at the moment it actually matters.
    planner_cfg = (config.get("session_planner") or {}) if isinstance(config, dict) else {}
    # Default to the planner's own ceiling of 1. `or 0` would be wrong here:
    # a config without a session_planner block would collapse the limit to
    # zero and refuse any dissent at all.
    _raw_max_opposing = planner_cfg.get("max_opposing_agents_for_ready", 1)
    try:
        max_opposing = int(_raw_max_opposing)
    except (TypeError, ValueError):
        max_opposing = 1
    oppose_count = len(oppose_agents)
    if oppose_count > max_opposing:
        return {
            "allow": False,
            "kind": "OPPOSED_BY_LIVE_AGENTS",
            "support_count": support_count,
            "support_agents": support_agents,
            "oppose_agents": oppose_agents,
            "oppose_count": oppose_count,
            "reason": (
                f"{oppose_count} qualified agents oppose the mapped {side} "
                f"(limit {max_opposing}): {', '.join(oppose_agents)}"
            ),
        }

    if support_count >= min_agents:
        return {
            "allow": True,
            "kind": "THREE_AGENT_ADMISSION",
            "support_count": support_count,
            "support_agents": support_agents,
            "oppose_agents": oppose_agents,
            "reason": f"{support_count} qualified agents aligned with the mapped direction",
        }

    confirm_source = str(decision.get("confirm_source") or "").lower()
    confirm_conf = _safe_float(decision.get("confirm_confidence"), 0.0)
    if support_count >= 2 and confirm_source in {"macro", "gemini"}:
        return {
            "allow": True,
            "kind": "TWO_AGENT_CONFIRMED_ADMISSION",
            "support_count": support_count,
            "support_agents": support_agents,
            "confirm_source": confirm_source,
            "confirm_confidence": round(confirm_conf, 1),
            "reason": f"{support_count} qualified agents + {confirm_source} confirmation",
        }

    direct_context_confirmation = _planner_context_confirmation(decision, config, side)
    if support_count >= 2 and direct_context_confirmation.get("allow"):
        return {
            "allow": True,
            "kind": "TWO_AGENT_CONTEXT_CONFIRMED_ADMISSION",
            "support_count": support_count,
            "support_agents": support_agents,
            "confirm_source": direct_context_confirmation.get("source"),
            "confirm_confidence": direct_context_confirmation.get("confidence"),
            "reason": f"{support_count} qualified agents + {direct_context_confirmation.get('reason')}",
        }

    plan = decision.get("session_plan") or {}
    if isinstance(plan, dict):
        objective_direction = str(plan.get("market_objective_direction") or "").upper()
        objective_alignment = str(plan.get("objective_alignment") or "").upper()
        scenario_type = str(plan.get("scenario_type") or "").upper()
        poi_classification = str(plan.get("poi_classification") or "").upper()
        structure_trend = str(plan.get("structure_trend") or "").upper()
        recent_sweep = plan.get("recent_sweep") or {}
        sweep_type = str((recent_sweep or {}).get("type") or "")
        aligned_sweep = (side == "BUY" and sweep_type == "sell_side") or (side == "SELL" and sweep_type == "buy_side")
        structure_aligned = structure_trend == ("BULLISH" if side == "BUY" else "BEARISH")
        objective_aligned = objective_direction == side and objective_alignment == "ALIGNED_WITH_MARKET_OBJECTIVE"
        quality_plan = poi_classification in {"EXTREME_POI", "HIGH_PROBABILITY_POI"}
        continuation_family = scenario_type in {"STRUCTURE_CONTINUATION", "ORDER_BLOCK_PULLBACK", "LIQUIDITY_REVERSAL"}
        has_smc = "smc" in support_agents
        has_local_confirmation = any(agent in support_agents for agent in {"price_action", "classical"})
        if (
            support_count >= 2
            and has_smc
            and has_local_confirmation
            and objective_aligned
            and structure_aligned
            and aligned_sweep
            and quality_plan
            and continuation_family
        ):
            return {
                "allow": True,
                "kind": "OBJECTIVE_ALIGNED_TWO_AGENT_OVERRIDE",
                "support_count": support_count,
                "support_agents": support_agents,
                "reason": "objective-aligned continuation override: 2 qualified agents including SMC + local confirmation, with aligned sweep and structure",
            }

    return {
        "allow": False,
        "support_count": support_count,
        "support_agents": support_agents,
        # Carry the dissent on the refusal too: a rejection that reports only
        # the support count reads as "not quite enough agreement" when the
        # real story may be active disagreement.
        "oppose_agents": oppose_agents,
        "oppose_count": len(oppose_agents),
        "reason": f"planner execution requires 3 qualified agents or 2 agents + macro/gemini; got {support_count}",
    }


def _add_leg_rejection_reason(
    plan: Dict[str, Any],
    primary: Dict[str, Any],
    standby: Dict[str, Any],
    config: Dict[str, Any],
) -> str | None:
    """Reject an add leg that cannot honestly improve a live main thesis.

    Two independent failure modes are checked at the execution boundary,
    because a plan may be persisted and replayed after the planner ran:

    1. The add entry sits beyond the main leg's stop loss. The only price path
       that fills it is main-fills -> main-stopped -> price continues, i.e.
       averaging into a rejected thesis while labelled "main then add".
    2. The add is both far away and structurally unlikely to be revisited, so
       it occupies a pending slot it will never use.
    """
    direction = str(plan.get("session_bias") or primary.get("direction") or standby.get("direction") or "").upper()
    if direction not in {"BUY", "SELL"}:
        return None

    add_entry = _safe_float(standby.get("entry_price"), 0.0)
    # Compare against the stop the main order will actually ship with, not the
    # raw structural stop: the risk floor can widen it substantially, and the
    # floored level is the real invalidation point for the live order.
    main_stop = _safe_float(primary.get("stop_loss"), 0.0)
    main_entry = _safe_float(primary.get("entry_price"), 0.0)
    main_target = _safe_float(primary.get("target_price") or primary.get("target_liquidity"), 0.0)
    if main_entry > 0 and main_stop > 0 and main_target > 0:
        try:
            main_stop = _safe_float(
                _planner_trade_levels(
                    config,
                    direction=direction,
                    entry_price=main_entry,
                    stop_loss=main_stop,
                    target_price=main_target,
                    symbol=str(plan.get("symbol") or config.get("symbol", "XAU/USD")),
                ).get("stop_loss"),
                main_stop,
            )
        except Exception:  # noqa: BLE001 - fall back to the structural stop
            pass
    if add_entry > 0 and main_stop > 0:
        beyond_invalidation = (
            (direction == "BUY" and add_entry <= main_stop)
            or (direction == "SELL" and add_entry >= main_stop)
        )
        if beyond_invalidation:
            return (
                f"add entry {add_entry:.2f} is beyond the main invalidation {main_stop:.2f} "
                f"({direction}); filling it would average into a rejected thesis"
            )

    planner_cfg = (config.get("session_planner") or {}) if isinstance(config, dict) else {}
    min_reach = _safe_float(planner_cfg.get("min_add_leg_return_probability", 25.0), 25.0)
    reach = _safe_float(standby.get("return_probability_score"), 0.0)
    revisit = str(standby.get("expected_revisit_window") or "").upper()
    if revisit == "LOW" and reach < min_reach:
        return (
            f"add leg revisit window is LOW with reach {reach:.1f} below {min_reach:.1f}; "
            "price is not expected to return to this area"
        )
    return None


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



def _revive_recent_ready_plan(
    database: Any,
    config: Dict[str, Any],
    *,
    symbol: str,
    now: datetime,
    base_decision: Dict[str, Any] | None = None,
    all_results: Dict[str, Any] | None = None,
) -> Dict[str, Any] | None:
    """Reuse a still-valid day map when this cycle could not rebuild one.

    Plans are rebuilt from scratch every cycle and never persisted for reuse,
    so a map only ever gets one chance to place its orders: the cycle that
    produced it. If price had not yet reached a workable distance in that
    cycle, or agents briefly disagreed in the next one, the map was silently
    forgotten -- which is why a confirmed A+ plan could be published and no
    order ever appear.

    A day map is a standing thesis with an explicit lifetime (plan_expires_at),
    so an unexpired one remains actionable. Reviving it changes nothing about
    the checks that follow: the admission gate, reward test, duplicate filter
    and price-distance rule all still run against live prices.
    """
    planner_cfg = (config.get("session_planner") or {}) if isinstance(config, dict) else {}
    if not bool(planner_cfg.get("revive_unexpired_plans", True)):
        return None
    try:
        rows = database.get_recent_session_plans(limit=12, symbol=symbol, plan_ready_only=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load recent session plans for revival: %s", exc)
        return None

    for row in rows or []:
        payload = row.get("payload") if isinstance(row, dict) else None
        if isinstance(payload, str):
            import json as _json
            try:
                payload = _json.loads(payload)
            except (ValueError, TypeError):
                payload = None
        if not isinstance(payload, dict) or not payload.get("plan_ready"):
            continue
        expires_at = _parse_datetime(str(payload.get("plan_expires_at") or ""))
        if not expires_at or now >= expires_at:
            continue
        if not (payload.get("primary_poi") or {}):
            continue

        # Re-authorise against the agents as they are *now*.
        #
        # The snapshot carries its own verdict -- authority CONFIRMED, an
        # archetype scored 86%, planner grade A+ -- and those fields were
        # replayed unchallenged. A live signal therefore went out headed
        # "3 qualified agents aligned" while the same message listed
        # Technical, Price Action and Multi-Timeframe all opposing, because
        # the stamp was hours old and the agents had since turned.
        #
        # Reviving a thesis is not the same as re-approving it: the map may
        # keep its shape, but the permission to trade it has to be earned
        # again from the current book. Refusing here leaves the cycle with no
        # plan, which is the correct outcome when the market has moved against
        # the map.
        revived_bias = str(payload.get("session_bias") or payload.get("authority_direction") or "").upper()
        if base_decision is not None and revived_bias in {"BUY", "SELL"}:
            recheck = _planner_execution_gate(
                {**base_decision, "decision": revived_bias, "session_plan": payload},
                config,
            )
            if not recheck.get("allow"):
                logger.info(
                    "Revived %s day map for %s rejected on re-authorisation: %s",
                    revived_bias, symbol, recheck.get("reason"),
                )
                continue

        age_minutes = 0.0
        created = _parse_datetime(str(row.get("analysis_run_at") or payload.get("created_at") or ""))
        if created:
            age_minutes = max(0.0, (now - created).total_seconds() / 60.0)
        logger.info(
            "Reusing unexpired %s day map for %s (built %.0f min ago, expires %s); "
            "this cycle could not rebuild one",
            payload.get("session_bias"), symbol, age_minutes, payload.get("plan_expires_at"),
        )
        revived = deepcopy(payload)
        revived["revived_from_snapshot"] = True
        revived["revived_age_minutes"] = round(age_minutes, 1)
        # An order created from this map is new, but it used to inherit the
        # original map's expiry -- so a plan built at 02:00 produced a 07:41
        # order that was already 71% through its life at birth, and the
        # planner cancelled it at 10:14 as "session plan expired" after only
        # 2.5 hours. Half an hour later the same area was republished, which
        # reads as the system contradicting itself.
        #
        # The thesis is still the original one, so the map keeps its identity
        # and its own age is recorded; what is refreshed is the deadline any
        # order derived from it will be judged against. The order's own
        # staleness window (pending_freshness.stale_after_hours) remains the
        # binding limit, which is the check that should have governed all
        # along.
        planner_cfg = (config.get("session_planner") or {}) if isinstance(config, dict) else {}
        expire_hours = _safe_float(planner_cfg.get("expire_after_hours"), 8.0) or 8.0
        renewed = now + timedelta(hours=expire_hours)
        revived["original_plan_expires_at"] = payload.get("plan_expires_at")
        revived["plan_expires_at"] = renewed.replace(microsecond=0).isoformat()
        revived["plan_expiry_renewed_on_revival"] = True
        # RE-JUDGE EXECUTION READINESS AGAINST THE LIVE BOOK.
        #
        # The gate recheck above re-counts the agents, but the readiness the
        # ladder's next gate reads was still the stamp written when the map
        # was BUILT. A map stored as WATCH_EXECUTION at 12:45 therefore stayed
        # "waiting for stronger execution confirmation" forever, even after
        # the live agents and macro lined up -- measured on 2026-08-04 17:25:
        # the revived BUY map was blocked by a readiness sentence describing
        # the 12:45 book while the same cycle confirmed BUY via 2 agents at
        # 92% and macro at 68%. The sentence looked live; it was a fossil.
        #
        # This is the readiness half of the same repair the re-authorisation
        # above already does for the agent count: reviving a thesis must earn
        # its permission to trade again from the current book, in BOTH gates.
        # The function recomputed here is the very one the planner uses, run
        # with fallback_day_map strictness, so a revived map is judged by the
        # same standard as a fresh one -- including the direction where live
        # support has DRIFFED and a stored READY must degrade back to WATCH.
        if isinstance(all_results, dict):
            try:
                old_readiness = payload.get("execution_readiness") or {}
                # Judge readiness from the SAME live evidence the agent gate
                # above was judged on: cycle agent results first, falling back
                # to this cycle's agent_details for any agent missing from
                # all_results. Two different books for the two gates is how a
                # fossil stamp survives under a fresh coat of paint.
                live_for_readiness = dict(all_results)
                details = (base_decision or {}).get("agent_details") or {}
                for _agent_name in ("technical", "classical", "smc",
                                    "price_action", "multitimeframe"):
                    if not isinstance(live_for_readiness.get(_agent_name), dict):
                        _detail = details.get(_agent_name)
                        if isinstance(_detail, dict):
                            live_for_readiness[_agent_name] = {
                                "signal": _detail.get("direction") or "WAIT",
                                "confidence": _detail.get("confidence") or 0.0,
                            }
                fresh_readiness = SessionPlannerService(config)._execution_readiness(
                    planner_source="fallback_day_map",
                    direction=revived_bias,
                    primary=payload.get("primary_poi") or {},
                    standby=payload.get("standby_poi") or None,
                    all_results=live_for_readiness,
                    preferred_execution_family=str(
                        payload.get("preferred_execution_family")
                        or payload.get("scenario_type") or ""
                    ),
                    macro=all_results.get("macro_fundamental") or {},
                )
                revived["readiness_at_revival"] = {
                    "stored_state": str(old_readiness.get("state") or ""),
                    "stored_reason": str(old_readiness.get("reason") or ""),
                }
                revived["execution_readiness"] = fresh_readiness
                revived["readiness_rejudged_on_revival"] = True
            except Exception as exc:  # noqa: BLE001 - keep the stored verdict rather than drop the map
                logger.warning(
                    "Readiness re-judgement failed on revival for %s; keeping the stored verdict: %s",
                    symbol, exc,
                )
        logger.info(
            "Revived day map expiry renewed: %s -> %s (orders judged by their "
            "own age, not the source map's)",
            payload.get("plan_expires_at"), revived["plan_expires_at"],
        )
        return revived
    return None


def _dynamic_risk_block_for_cycle(
    *,
    decision_type: str,
    decision: Dict[str, Any],
    session_plan: Dict[str, Any],
    dynamic_risk: Dict[str, Any],
) -> str | None:
    """Apply the dynamic-risk halt to whichever thesis this cycle can execute.

    Two routes can create an order: the direct BUY/SELL path and the planner
    ladder. The ladder builds from the plan's session_bias, so it can place
    orders on a cycle whose live consensus reads WAIT -- which is why the
    direction falls back to the plan here. A halt has to cover both.

    But the numbers must belong to the thesis being judged. When the fallback
    was used, `confidence` and `quality` were still read off the WAIT
    decision, where both are 0.0, so a CONFIRMED A+ 98.6% day map was refused
    as "Confidence 0.0% below Dynamic Risk requirement 65.0%" -- a sentence
    about a number the map never had. `planner_confidence` is the same field
    the planner-quality block already uses for a mapped plan.
    """
    plan_ready = isinstance(session_plan, dict) and bool(session_plan.get("plan_ready"))
    side = decision_type if decision_type in {"BUY", "SELL"} else (
        str(session_plan.get("session_bias") or "").upper() if plan_ready else ""
    )
    if side not in {"BUY", "SELL"}:
        return None
    probe = {**decision, "decision": side}
    if decision_type not in {"BUY", "SELL"} and plan_ready:
        planner_conf = _safe_float(session_plan.get("planner_confidence"), 0.0)
        probe["confidence"] = planner_conf
        probe["quality"] = {"score": planner_conf}
    return should_block_signal(probe, dynamic_risk)


#: Why the last session-plan ladder attempt produced no order.
#:
#: `execution_audit` recorded the planner GATE verdict and the order count,
#: and nothing in between. Measured on 2026-08-04: of 21 published maps only
#: 1 became an order, and 9 of the 20 failures carried a gate reason that
#: reads as a PASS ("3 qualified agents aligned with the mapped direction").
#: The gate had allowed them; something after it stopped the ladder, and the
#: audit had no field to say what.
#:
#: `_execute_session_plan_ladder` has nine early exits after that gate --
#: a live trade already open, a terminal setup state, an entry too close to
#: price to rest as a pending order, a scenario family kept, and so on. Each
#: logs its reason and returns 0. This carries the last one out so the audit
#: can name it.
_LAST_LADDER_STOP: Dict[str, Any] = {}


def _ladder_stop(reason: str, **detail: Any) -> int:
    """Record why the ladder produced no order, then return 0."""
    _LAST_LADDER_STOP.clear()
    _LAST_LADDER_STOP.update({"reason": reason, **detail})
    return 0


def _execute_session_plan_ladder(
    base_decision: Dict[str, Any],
    all_results: Dict[str, Any],
    open_trades: List[Dict[str, Any]],
    database: DatabaseService,
    telegram: TelegramService,
    config: Dict[str, Any],
) -> int:
    # Clear first: a stale reason from a previous cycle would be worse than
    # no reason at all, because it would read as fact.
    _LAST_LADDER_STOP.clear()
    planner_cfg = (config.get("session_planner") or {}) if isinstance(config, dict) else {}
    # Every exit from this function is logged. A plan can be published while no
    # order appears for several independent reasons, and silent returns made
    # that impossible to diagnose from the run output alone.
    if not bool(planner_cfg.get("create_pending_orders_from_plan", True)):
        logger.info("Session-plan ladder skipped: create_pending_orders_from_plan is disabled")
        return _ladder_stop("create_pending_orders_from_plan disabled")
    plan = base_decision.get("session_plan") or {}
    if not isinstance(plan, dict) or not plan.get("plan_ready"):
        # A map that could not be rebuilt this cycle is not necessarily gone:
        # agents drift in and out of agreement minute to minute, while the
        # thesis it expressed has its own lifetime. Fall back to the most
        # recent unexpired one before giving up.
        revived = _revive_recent_ready_plan(
            database, config, symbol=str(base_decision.get("symbol") or config.get("symbol", "XAU/USD")),
            now=datetime.now(timezone.utc),
            # Carries this cycle's agent_details so the snapshot is re-judged
            # against the live book rather than its own stored verdict -- and
            # all_results so EXECUTION READINESS is re-judged the same way,
            # not replayed as the stamp the build cycle wrote.
            base_decision=base_decision,
            all_results=all_results,
        )
        if not revived:
            logger.info(
                "Session-plan ladder skipped: plan not ready (status=%s, reason=%s) "
                "and no unexpired day map to fall back on",
                (plan or {}).get("plan_status") if isinstance(plan, dict) else "no-plan",
                (plan or {}).get("plan_reason") if isinstance(plan, dict) else "no session_plan on decision",
            )
            return _ladder_stop(
                "plan not ready and no unexpired day map",
                plan_status=(plan or {}).get("plan_status") if isinstance(plan, dict) else None,
            )
        plan = revived
        base_decision = deepcopy(base_decision)
        base_decision["session_plan"] = revived
    readiness = (plan.get("execution_readiness") or {}) if isinstance(plan.get("execution_readiness"), dict) else {}
    readiness_state = str(readiness.get("state") or "")
    if readiness_state and readiness_state not in {"PENDING_EXECUTION_READY", "MARKET_EXECUTION_READY"}:
        logger.info("Session-plan ladder blocked by execution readiness: %s", readiness.get("reason") or readiness_state or "unknown")
        return _ladder_stop(
            f"execution readiness {readiness_state or 'unknown'}",
            detail=readiness.get("reason"),
        )
    gate = _planner_execution_gate(base_decision, config)
    if not gate.get("allow"):
        logger.info("Session-plan ladder blocked: %s", gate.get("reason"))
        return _ladder_stop(str(gate.get("reason") or "planner gate refused"))
    symbol = str(base_decision.get("symbol") or plan.get("symbol") or config.get("symbol", "XAU/USD"))
    normalized_symbol = normalize_symbol(symbol)
    symbol_open_trades = [t for t in (open_trades or []) if normalize_symbol(t.get("symbol") or symbol) == normalized_symbol]
    scenario_review = ScenarioGovernor(config).review_new_plan(plan, symbol_open_trades, database=database)
    if scenario_review.get("action") == "KEEP_EXISTING_FAMILY":
        logger.info("Session-plan family kept for %s: %s", symbol, scenario_review.get("reason"))
        return _ladder_stop("scenario family kept", detail=scenario_review.get("reason"))
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
    live_now = [t for t in symbol_open_trades if str(t.get("status") or "").upper() in {"OPEN", "PARTIAL", "TP1_HIT"}]
    if live_now:
        logger.info(
            "Session-plan ladder skipped: %s live trade(s) already open on %s",
            len(live_now), symbol,
        )
        return _ladder_stop(
            f"{len(live_now)} live trade(s) already open", live_trades=len(live_now)
        )

    primary = plan.get("primary_poi") or {}
    standby = plan.get("standby_poi") or {}
    if not isinstance(primary, dict) or not primary:
        logger.info("Session-plan ladder skipped: plan carries no primary POI")
        return _ladder_stop("plan carries no primary POI")

    # Terminal-state guard at the execution boundary. The planner already
    # filters these out when ranking, but a plan can be persisted, replayed
    # from a snapshot, or invalidated between planning and execution -- so the
    # leg that actually creates orders must re-check rather than trust the map.
    if str(primary.get("setup_state") or "").upper() in TERMINAL_SETUP_STATES:
        logger.info(
            "Session-plan ladder blocked: primary leg is in terminal state %s",
            primary.get("setup_state"),
        )
        return _ladder_stop(
            f"primary leg terminal state {primary.get('setup_state')}"
        )
    if isinstance(standby, dict) and standby and str(standby.get("setup_state") or "").upper() in TERMINAL_SETUP_STATES:
        logger.info(
            "Session-plan add leg dropped: standby is in terminal state %s",
            standby.get("setup_state"),
        )
        standby = {}

    if isinstance(standby, dict) and standby:
        drop_reason = _add_leg_rejection_reason(plan, primary, standby, config)
        if drop_reason:
            logger.info("Session-plan add leg dropped: %s", drop_reason)
            standby = {}

    split_decisions = _split_execution_decisions(base_decision, plan, primary, standby if isinstance(standby, dict) else None, config)
    if split_decisions:
        plan_decisions = split_decisions
    else:
        primary_decision = _build_plan_ladder_decision(base_decision, plan, primary, config)
        if not primary_decision:
            # Most often the market has walked into the mapped area, so the leg
            # prices as MARKET rather than a pending order and this function
            # declines it. Say so, instead of returning zero in silence.
            entry_price = _safe_float(primary.get("entry_price"), 0.0)
            current_price = _safe_float(base_decision.get("current_price"), 0.0)
            distance = abs(price_to_points(entry_price - current_price, symbol=symbol)) if entry_price and current_price else 0.0
            logger.info(
                "Session-plan ladder skipped: primary leg produced no pending order "
                "(entry %.2f vs price %.2f, %.0f pts apart; inside the market threshold "
                "means it would execute now rather than rest as a pending order)",
                entry_price, current_price, distance,
            )
            return _ladder_stop(
                "primary entry too close to price to rest as a pending order",
                entry=round(entry_price, 2), price=round(current_price, 2),
                points_apart=round(distance, 1),
            )
        plan_decisions = [primary_decision] + ([ _build_plan_ladder_decision(base_decision, plan, standby, config) ] if isinstance(standby, dict) and standby else [])

    created = 0
    staged_trades = list(symbol_open_trades)
    for ladder_decision in plan_decisions:
        if not ladder_decision:
            continue
        ladder_decision["planner_execution_gate"] = deepcopy(gate)
        ladder_decision.setdefault("reasons", []).append(f"Planner admission: {gate.get('reason')}")
        role = _decision_ladder_role(ladder_decision)
        # EVERY exit from the order loop names itself. On 2026-08-04 nine
        # published maps that produced no order carried a gate reason that
        # reads as a PASS, and the audit said "stop not recorded" -- because
        # these in-loop refusals returned without telling `_LAST_LADDER_STOP`.
        # The measured replay of the A+ 97.8% Asia Morning map died at final
        # validation here (breakeven beyond TP1) and left no trace in the row.
        if any(_trade_scenario_id(t) == _decision_scenario_id(ladder_decision) and _trade_ladder_role(t) == role for t in staged_trades):
            _ladder_stop(f"{role} leg already staged for this scenario")
            continue
        duplicate_reason = duplicate_signal_reason(ladder_decision, database, config)
        if duplicate_reason:
            logger.info("Session-plan ladder %s blocked for %s: %s", role, symbol, duplicate_reason)
            _ladder_stop(f"{role} blocked by duplicate filter: {duplicate_reason}")
            if role in {"PRIMARY", "STARTER"}:
                return created
            continue
        ladder_violations = validate_signal_before_send(ladder_decision, config, staged_trades)
        if ladder_violations:
            logger.error(
                "Session-plan ladder %s failed final validation for %s: %s",
                role, symbol, "; ".join(ladder_violations),
            )
            _ladder_stop(f"{role} failed final validation: {'; '.join(ladder_violations)}")
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
            _ladder_stop(f"{role} telegram delivery failed; order not recorded")
            if role in {"PRIMARY", "STARTER"}:
                return created
            continue
        database.save_trade(ladder_decision)
        _record_decision_audit(
            database, ladder_decision, config,
            stage="delivered", outcome="SENT", reason=f"planner ladder {role}",
        )
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


def _trade_tp2_price(trade: Dict[str, Any]) -> float | None:
    """The TP2 level a closed trade was actually settled at."""
    for key in ("tp2", "take_profit_2"):
        try:
            value = float(trade.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    snapshot = _trade_setup_context(trade)
    if isinstance(snapshot, dict):
        try:
            value = float((snapshot.get("signal") or {}).get("tp2") or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return None


def _trim_zero(value: float) -> str:
    """Format a possibly-fractional hour count without lying about it.

    ``f"{2.5:.0f}"`` renders "2", so a 2.5-hour window announced itself as
    "within 2h" -- the message contradicted the rule enforcing it. Keeping
    one decimal only when it carries information means 3.0 stays "3" and
    2.5 stays "2.5".
    """
    text = f"{float(value):.1f}"
    return text[:-2] if text.endswith(".0") else text


def _post_tp2_reentry_block(
    decision: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    config: Dict[str, Any],
    *,
    now: datetime,
    symbol: str,
    entry_price: float,
    direction: str,
) -> str | None:
    """After a trade takes TP2, refuse a same-direction re-entry too near it.

    TP2 is where a move ENDS, because it is where the liquidity that fuelled
    it was consumed. Re-entering the same direction at that same level is
    entering into the bounce, and the existing cooldown could not see it: it
    measures distance from the previous trade's ENTRY, not from the level it
    closed at.

    2026-07-31 is the case that motivated this rule. d917b1d5 took TP2 at
    4029.17 around 13:50. Twenty-one minutes later b4f85832 was published as
    a SELL LIMIT at 4031.77 -- 26 points above that TP2 -- and by 15:21 it
    was 116 points offside. The old guard skipped it because it sat 430
    points from the prior ENTRY (4074.78), outside the 200-pt duplicate zone.

    Measuring from TP2 is the whole point of this check.

    BOTH DIRECTIONS, MIRRORED
    -------------------------
    The safe side is always AWAY from the exhausted level, back toward where
    the move began:

        SELL: TP2 sits below entry, so a new SELL must be ABOVE it
              -> entry - tp2 >= min_distance_points
        BUY:  TP2 sits above entry, so a new BUY must be BELOW it
              -> tp2 - entry >= min_distance_points

    A re-entry on the far side of TP2 -- already beyond the level -- is not a
    repeat of the exhausted move and is left alone.

    Only a same-direction trade arms the rule: a BUY after a SELL's TP2 is a
    reversal, not a repeat.

    Also:
      * Time-boxed: the block expires after ``window_hours``.
      * Overridden by a genuinely new thesis -- the same
        ``_post_exit_revalidation_review`` evidence already used elsewhere
        (a different POI, a fresh sweep after the exit, or a state
        progression with a stronger trigger). A trend that keeps giving new
        structure is not blocked; a lazy re-entry at an exhausted level is.

    No risk setting is touched: this refuses a signal, it never resizes,
    re-prices or re-stops one.
    """
    cfg = (config.get("post_tp2_reentry") or {}) if isinstance(config, dict) else {}
    if _tr.post_tp2_rule({"post_tp2_reentry": cfg})["enabled"] is False:
        return None
    if direction not in {"BUY", "SELL"}:
        return None

    rule = _tr.post_tp2_rule({"post_tp2_reentry": cfg})
    min_distance_points = rule["min_distance_points"]
    window_hours = rule["window_hours"]
    if min_distance_points <= 0 or window_hours <= 0:
        return None

    for trade in candidates:
        if _trade_outcome(trade) == "OPEN":
            continue
        if str(trade.get("status") or "").upper() != "TP2_HIT":
            continue
        # Only the same direction repeats an exhausted move.
        if _trade_direction(trade) != direction:
            continue
        tp2 = _trade_tp2_price(trade)
        if tp2 is None or tp2 <= 0:
            continue

        closed_at = _trade_reference_time(trade, now)
        hours_since = (now - closed_at).total_seconds() / 3600.0
        if hours_since < 0 or hours_since > window_hours:
            continue

        # Operator directive 2026-08-07 (داac4022): the rule is ABSOLUTE for
        # the whole window. A new BUY must sit at least min_distance_points
        # BELOW the exhausted TP2 (a SELL, above it) -- an entry ON or BEYOND
        # the exhausted level is chasing the consumed move, which is exactly
        # what the rule exists to refuse. The earlier "far side is not a
        # repeat" exemption let the 10:02 BUY at 4317.26 sail 172 pts ABOVE
        # the 4300.00 TP2 taken hours earlier.
        if direction == "SELL":
            distance = price_to_points(entry_price - tp2, symbol=symbol)
            side_word = "above"
        else:
            distance = price_to_points(tp2 - entry_price, symbol=symbol)
            side_word = "below"
        if distance >= min_distance_points:
            continue

        # 2026-08-07 (operator option 1): NO early release. The "new thesis"
        # revalidation override is gone; the block expires only by the
        # window. _post_exit_revalidation_review stays in use by the other
        # post-exit cooldown path, untouched.
        if distance < 0:
            where = (f"{-distance:.0f} pts BEYOND the TP2 (chasing the "
                     f"consumed move)")
        else:
            where = f"only {distance:.0f} pts {side_word} the TP2"
        return (
            f"Post-TP2 re-entry blocked: {direction} entry {entry_price:.2f} is "
            f"{where} {tp2:.2f} taken {hours_since:.1f}h ago "
            # The window accepts fractions (2.5 since 2026-08-03). ".0f"
            # rounded it for display, so a 2.5-hour rule announced itself as
            # "within 2h" -- a message that contradicts the rule it is
            # reporting. Trim only a trailing ".0" so whole numbers still
            # read "3h" rather than "3.0h".
            f"(needs ≥{min_distance_points:.0f} pts {side_word} within "
            f"{_trim_zero(window_hours)}h; absolute, no early release)."
        )
    return None


def _post_tp2_reentry_reason(
    decision: Dict[str, Any], database: DatabaseService, config: Dict[str, Any]
) -> str | None:
    """The post-TP2 block, callable independently of the duplicate filter.

    ``duplicate_signal_reason`` runs this check as part of its sweep, but the
    adaptive-execution path skips that whole function on purpose (a
    replacement order is meant to resemble the order it replaces). An
    exhausted target level is exhausted regardless of which route the signal
    took, so this wrapper lets the block be evaluated on its own.

    Same candidate set as the duplicate filter -- open trades plus recent
    history in the same direction and symbol -- so the two paths cannot drift
    apart in what they look at.
    """
    direction = str(decision.get("decision") or "").upper()
    if direction not in {"BUY", "SELL"}:
        return None
    signal = decision.get("signal") or {}
    entry_info = signal.get("entry") or {}
    entry_price = _safe_float(
        entry_info.get("price") if isinstance(entry_info, dict) else None, 0.0
    ) or _safe_float(decision.get("current_price"), 0.0)
    if entry_price <= 0:
        return None

    symbol = str(
        decision.get("symbol") or signal.get("symbol") or config.get("symbol", "XAU/USD")
    )
    normalized = normalize_symbol(symbol)

    candidates: List[Dict[str, Any]] = []
    seen: set = set()
    # A guard that cannot see the trade cannot guard against it.
    #
    # `get_recent_trades` orders by created_at and truncates. A trade that
    # OPENED at 11:26 and took TP2 at 13:38 is old by creation order, so once
    # enough newer rows exist -- cancelled pendings, ladder legs, the other
    # symbol -- it falls out of the window entirely.
    #
    # 2026-08-03: TP2 was taken at 4022.31 at 13:38 and a SELL LIMIT went out
    # at 4037.48 at 14:10, 152 points above it. The rule was configured, the
    # code was deployed, and it allowed the signal because the closed trade
    # was no longer in the list it was handed. Reproduced with 60 newer rows:
    # the guard returns ALLOWED and the TP2 row is absent from limit=50.
    #
    # The window this rule cares about is TIME SINCE CLOSING, so ask for
    # enough history to cover it rather than trusting a fixed row count. The
    # filter below still discards anything outside the configured hours.
    window_hours = _tr.post_tp2_rule(config or {})["window_hours"]
    # ~12 five-minute cycles an hour, and a cycle can write several rows.
    # Scaled to the window with a floor that keeps the old behaviour for
    # short windows, and a ceiling so a misconfiguration cannot pull the
    # whole table.
    lookback_rows = int(min(500, max(50, window_hours * 120)))
    since_iso = (
        datetime.now(timezone.utc) - timedelta(hours=window_hours)
    ).replace(microsecond=0).isoformat()
    try:
        pool = list(database.get_open_trades() or []) + list(
            database.get_recent_trades(limit=lookback_rows) or []
        )
        # Ask the database directly for trades that CLOSED inside the window.
        # This is the authoritative source for this rule; the wider row scan
        # above stays as a fallback for schemas without a closed_at column.
        if hasattr(database, "get_trades_closed_since"):
            try:
                pool += list(
                    database.get_trades_closed_since(since_iso, symbol=symbol) or []
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Post-TP2 guard could not read closed trades: %s", exc)
    except Exception as exc:  # noqa: BLE001 - a guard must not break the cycle
        logger.warning("Post-TP2 guard could not read trade history: %s", exc)
        return None
    for trade in pool:
        if not isinstance(trade, dict):
            continue
        if normalize_symbol(trade.get("symbol") or symbol) != normalized:
            continue
        if _trade_direction(trade) != direction:
            continue
        tid = str(trade.get("id") or "")
        if tid and tid in seen:
            continue
        if tid:
            seen.add(tid)
        candidates.append(trade)

    return _post_tp2_reentry_block(
        decision, candidates, config,
        now=datetime.now(timezone.utc), symbol=symbol,
        entry_price=entry_price, direction=direction,
    )


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

    # A target that was reached is an exhausted level, not a fresh one.
    post_tp2 = _post_tp2_reentry_block(
        decision, candidates, config,
        now=now, symbol=symbol, entry_price=entry_price, direction=direction,
    )
    if post_tp2:
        return post_tp2

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


def _session_plan_execution_audit(plan: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    manual_plan = (plan.get("manual_plan") or {}) if isinstance(plan, dict) else {}
    primary = (plan.get("primary_poi") or {}) if isinstance(plan, dict) else {}
    standby = (plan.get("standby_poi") or {}) if isinstance(plan, dict) else {}
    return {
        "plan_ready": bool(plan.get("plan_ready")),
        "plan_status": plan.get("plan_status"),
        "plan_reason": plan.get("plan_reason"),
        "authority_state": plan.get("authority_state"),
        "authority_direction": plan.get("authority_direction"),
        "market_objective": plan.get("market_objective"),
        "market_objective_direction": plan.get("market_objective_direction"),
        "day_archetype": plan.get("day_archetype"),
        "day_archetype_confidence": plan.get("day_archetype_confidence"),
        "preferred_execution_family": plan.get("preferred_execution_family"),
        "objective_alignment": plan.get("objective_alignment"),
        "same_box_ladder": bool(plan.get("same_box_ladder") or manual_plan.get("same_box_ladder")),
        "execution_readiness_state": str(((plan.get("execution_readiness") or {}) if isinstance(plan.get("execution_readiness"), dict) else {}).get("state") or ""),
        "execution_readiness_reason": str(((plan.get("execution_readiness") or {}) if isinstance(plan.get("execution_readiness"), dict) else {}).get("reason") or ""),
        "reversal_watch_active": bool((plan.get("reversal_watch") or {}).get("active")) if isinstance(plan.get("reversal_watch"), dict) else False,
        "primary_entry_price": plan.get("primary_entry_price") or primary.get("entry_price"),
        "standby_entry_price": plan.get("standby_entry_price") or standby.get("entry_price"),
        **extra,
    }


def _active_reversal_watch(database: DatabaseService, *, symbol: str) -> Dict[str, Any]:
    try:
        recent = database.get_recent_trades(limit=20)
    except Exception:
        recent = []
    for trade in recent or []:
        if normalize_symbol(trade.get("symbol") or symbol) != normalize_symbol(symbol):
            continue
        snapshot = trade.get("signal_snapshot") or {}
        if isinstance(snapshot, str):
            try:
                import json as _json
                snapshot = _json.loads(snapshot)
            except Exception:
                snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        watch = snapshot.get("reversal_watch") or {}
        if not isinstance(watch, dict) or not watch:
            continue
        if not bool(watch.get("active", True)):
            continue
        direction = str(watch.get("direction") or "").upper()
        expires_at = _parse_datetime(watch.get("expires_at"))
        if direction in {"BUY", "SELL"} and (expires_at is None or expires_at > datetime.now(timezone.utc)):
            return dict(watch)
    return {}


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


def _crash_site(exc: BaseException) -> str:
    """The deepest frame inside this repository, as ``file.py:line in func``.

    An exception message describes the symptom. ``'NoneType' object has no
    attribute 'get'`` fits every one of the hundreds of ``.get(`` calls under
    the planner, so on its own it is not actionable -- five crashed cycles
    appeared in every rejection report from 2026-08-04 onward with no way to
    find them.

    The traceback is already written to the run log by ``logger.exception``,
    but the report reads the stored row, not the log. Folding the frame into
    the stored reason puts the location where the analysis can see it.

    Frames from site-packages and the standard library are skipped: the last
    frame is usually inside a dependency, while the line worth fixing is the
    last one we own.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    site_marker = os.sep + "site-packages" + os.sep
    best = None
    try:
        for frame in traceback.extract_tb(exc.__traceback__):
            path = os.path.abspath(frame.filename)
            if site_marker in path or not path.startswith(root):
                continue
            best = frame
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        return "unknown"
    if best is None:
        return "outside project"
    return f"{os.path.basename(best.filename)}:{best.lineno} in {best.name}"


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


def _report_data_outage(
    telegram: Any,
    config: Dict[str, Any],
    symbol: str,
    reason: str,
    delivery: Any = None,
) -> None:
    """Announce a cycle abandoned before analysis could run.

    A data failure returns before any decision exists, so the normal status
    message cannot be built. Staying silent here is indistinguishable from a
    quiet market, which hides an outage that may already be hours long.
    """
    if not should_send_hourly_status(config):
        return
    # This outage note replaces the generic hourly status for this cycle.
    if delivery is not None:
        delivery.mark_sent()
    try:
        telegram.send_message(
            "⚠️ <b>SmartSignal — Analysis skipped</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {html.escape(str(symbol))}\n"
            "🎯 Decision: NONE (cycle stopped before analysis)\n\n"
            f"<b>Reason:</b>\n• {html.escape(str(reason))}\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send data-outage status for %s: %s", symbol, exc)


class _HourlyStatusDelivery:
    """Guarantee the hourly status is sent exactly once per analysis cycle.

    The status send used to sit on the WAIT branch at the end of the function.
    Any of the fourteen earlier `return` statements — a directional decision, a
    filter, a data problem — skipped it entirely, so an hour could pass with no
    message and a green workflow. This tracker is armed once the cycle knows a
    status is due and flushed from a `finally` block, so no exit path can
    swallow it. `send_once` keeps a real trade signal from being duplicated by
    a status message in the same cycle.
    """

    def __init__(self, telegram: Any, config: Dict[str, Any]) -> None:
        self.telegram = telegram
        self.config = config
        self.due = False
        self.sent = False
        self.decision: Dict[str, Any] = {}
        self.all_results: Dict[str, Any] = {}
        self.database: Any = None
        self.note: str | None = None

    def arm(self, *, decision, all_results, database, note=None) -> None:
        self.due = True
        self.decision = decision or {}
        self.all_results = all_results or {}
        self.database = database
        if note:
            self.note = str(note)

    def mark_sent(self) -> None:
        self.sent = True

    def flush(self) -> None:
        if not self.due or self.sent:
            return
        self.sent = True
        # A cycle can be armed and then die before the database handle is
        # attached, or die inside the message builder itself (Supabase down,
        # a malformed trade row). Both used to end in silence, which is the
        # exact failure this class exists to prevent: an hour with no message
        # and a green workflow. Fall back to a minimal note that needs no
        # database and no formatting.
        body = None
        if self.database is not None:
            try:
                body = _build_market_status_message(
                    self.decision, self.all_results, self.database, self.config
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to build hourly status message: %s", exc)
        if body is None:
            symbol = str(
                self.decision.get("symbol")
                or self.all_results.get("symbol")
                or self.config.get("symbol")
                or "XAU/USD"
            )
            decision_txt = str(self.decision.get("decision") or "UNKNOWN").upper()
            body = (
                "🟡 <b>SmartSignal — Market Status</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 {html.escape(symbol)}\n"
                f"🎯 Decision: {html.escape(decision_txt)}\n\n"
                "<i>Full status unavailable this cycle "
                "(database or formatting error — see workflow log).</i>\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
        if self.note:
            body = f"{html.escape(self.note)}\n{body}"
        try:
            self.telegram.send_message(body)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to send hourly status: %s", exc)


def _record_decision_audit(
    database: Any,
    decision: Dict[str, Any],
    config: Dict[str, Any],
    *,
    stage: str,
    outcome: str,
    reason: Any = None,
) -> None:
    """Write one row describing how this cycle ended.

    Deliberately best-effort: an audit failure must never stop a trade or a
    refusal from happening. The row carries the numbers needed to judge the
    decision later -- the R-multiples especially, since a target too close to
    entry was the fault that took longest to see.
    """
    if database is None:
        return
    try:
        symbol = str(decision.get("symbol") or config.get("symbol", "XAU/USD"))
        signal = decision.get("signal") or {}
        entry_info = (signal.get("entry") or {}) if isinstance(signal, dict) else {}
        entry = _safe_float(entry_info.get("price"), 0.0) or _safe_float(decision.get("current_price"), 0.0)
        stop = _safe_float(signal.get("stop_loss"), 0.0) if isinstance(signal, dict) else 0.0
        tp1 = _safe_float(signal.get("tp1"), 0.0) if isinstance(signal, dict) else 0.0
        tp2 = _safe_float(signal.get("tp2"), 0.0) if isinstance(signal, dict) else 0.0
        risk = abs(price_to_points(entry - stop, symbol=symbol)) if entry > 0 and stop > 0 else 0.0
        gate = decision.get("planner_execution_gate") or {}
        database.save_decision_audit({
            "symbol": symbol,
            "stage": stage,
            "outcome": outcome,
            "side": str(decision.get("decision") or "").upper(),
            "reason": str(reason or "")[:500] or None,
            "entry_price": round(entry, 2) if entry else None,
            "stop_loss": round(stop, 2) if stop else None,
            "tp1": round(tp1, 2) if tp1 else None,
            "tp2": round(tp2, 2) if tp2 else None,
            "tp1_rr": round(abs(price_to_points(tp1 - entry, symbol=symbol)) / risk, 2) if risk > 0 and tp1 > 0 else None,
            "tp2_rr": round(abs(price_to_points(tp2 - entry, symbol=symbol)) / risk, 2) if risk > 0 and tp2 > 0 else None,
            "confidence": _safe_float(decision.get("confidence"), 0.0) or None,
            "support_count": gate.get("support_count") if isinstance(gate, dict) else None,
            "oppose_count": gate.get("oppose_count") if isinstance(gate, dict) else None,
            "entry_mode": decision.get("entry_mode"),
            "trade_id": decision.get("trade_id"),
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to record decision audit (%s): %s", stage, exc)


def _notify_blocked_directional_signal(
    *,
    telegram: Any,
    decision: Dict[str, Any],
    all_results: Dict[str, Any],
    database: DatabaseService,
    config: Dict[str, Any],
    send_hourly_now: bool,
    stage: str,
    reason: Any,
    delivery: Any = None,
) -> None:
    """Annotate the pending hourly status with why a directional signal stopped.

    Delivery itself is owned by `_HourlyStatusDelivery`, so this only records
    the stage and reason; the message goes out even if this is never called.

    Two independent switches can open this path: the hourly status window, and
    `notify_on_blocked_signal` -- the setting that exists specifically to
    surface rejection reasons. Only the first was ever consulted, which left
    `should_send_status` defined and unused while the setting it reads did
    nothing.
    """
    # Persist first, notify second. Telegram delivery is optional and gated by
    # the status window; the audit trail is not. Recording only what was
    # announced would leave the quiet refusals -- the majority -- invisible,
    # which is how a filter can block every signal for a week unnoticed.
    _record_decision_audit(
        database, decision, config, stage=stage, outcome="BLOCKED", reason=reason,
    )
    if not (send_hourly_now or should_send_status(config)):
        return
    side = str(decision.get("decision") or decision.get("signal") or "").upper()
    reason_text = str(reason or "").strip() or "no reason recorded"
    note = f"🚫 {side} signal blocked at {stage} — {reason_text}"
    if delivery is not None:
        delivery.arm(
            decision=decision, all_results=all_results,
            database=database, note=note,
        )
        return
    try:
        telegram.send_message(
            html.escape(note) + "\n"
            + _build_market_status_message(decision, all_results, database, config)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send blocked-signal status for %s: %s", decision.get("symbol"), exc)


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
                tp2 = _safe_float(t.get("tp2"), 0.0)
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
                # Operator directive (2026-08-06): show entry / TP1 / TP2 / P&L
                # clearly for every open trade in the status message.
                levels_txt = ""
                if entry:
                    levels_txt += f" · Entry {entry:.2f}"
                if tp1:
                    levels_txt += f" · TP1 {tp1:.2f}"
                if tp2:
                    levels_txt += f" · TP2 {tp2:.2f}"
                trade_lines.append(
                    f"{marker} {direction} <code>#{html.escape(short)}</code>{levels_txt} · "
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


#: Roles SMCAgent assigns to the candidates it actually selected. Anything
#: else -- in practice "REJECTED" -- is a candidate it ranked and declined.
_SELECTED_SETUP_ROLES = {"PRIMARY", "STANDBY", "STARTER", "ADD_ON"}


def _select_setup_candidate(decision_type: str, all_results: Dict[str, Any]) -> Dict[str, Any]:
    """The setup a signal is allowed to describe itself with.

    SMCAgent ranks every candidate and records the verdict in
    ``selection_role``: rank 1 becomes PRIMARY, a qualifying rank 2 becomes
    STANDBY, and everything else is labelled REJECTED (agents/smc_agent.py
    :1148). REJECTED is not a neutral label -- it means the agent that found
    the setup looked at it and did not choose it.

    This function used to re-sort ALL candidates by quality score and return
    the top one, never reading the role. A declined candidate could therefore
    be handed straight to a live order by having the best score among the
    leftovers.

    2026-07-31, TRADE_20260731_152110_326407_a5520ee6 shipped as a SELL LIMIT
    carrying "role REJECTED · quality C · dominance 47.6 · reach 40.8". Both
    of those numbers sit below the planner's own floors (min_primary_dominance
    50, min_return_probability 42), which is why the planner path would have
    refused it -- but the dual-agent path reads this function instead, so the
    floors were never consulted.

    Selected roles are now preferred absolutely: a PRIMARY or STANDBY always
    outranks a REJECTED, whatever their scores. Within the selected group the
    existing quality ordering is unchanged.

    A rejected candidate is still RETURNED when nothing else exists, because
    the payload it builds is also used for reporting and diagnostics. Refusing
    to publish is a separate decision, taken by
    ``_rejected_setup_execution_block`` at the point an order is created.
    """
    smc = all_results.get("smc", {}) or {}
    candidates = list(smc.get("setup_candidates") or [])
    if not candidates:
        return {}

    def _score(candidate: Dict[str, Any]) -> float:
        quality = candidate.get("setup_quality") or {}
        return float(quality.get("score", candidate.get("quality_score", 0)) or 0)

    def _was_selected(candidate: Dict[str, Any]) -> bool:
        role = str(candidate.get("selection_role") or "").upper()
        # An absent role predates the labelling and is treated as selected,
        # so older snapshots behave exactly as they did before.
        return role in _SELECTED_SETUP_ROLES or not role

    # Sort by (selected first, then quality). Python's sort is stable, so
    # equal-score candidates keep their original ranking.
    def _ordered(pool: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(pool, key=lambda c: (0 if _was_selected(c) else 1, -_score(c)))

    side = str(decision_type or "").upper()
    if side in {"BUY", "SELL"}:
        directional = [c for c in candidates if str(c.get("direction", "")).upper() == side]
        if directional:
            return _ordered(directional)[0]
    return _ordered(candidates)[0]


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
    # Operator audit 2026-08-07 (card 17:02): never publish a wrong-side
    # target liquidity (BUY target below market, SELL above) from ANY source.
    _ref = float(decision.get("current_price") or 0.0)
    _tl = payload.get("target_liquidity")
    if _tl and _ref > 0:
        _wrong = (_tl <= _ref) if decision_type == "BUY" else (_tl >= _ref)
        if _wrong:
            payload["target_liquidity"] = None
    # (the final filter drops the None, so the card omits wrong-side targets)
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
    status_delivery = _HourlyStatusDelivery(telegram, config)
    try:
        database = DatabaseService(config)
        symbol = str(config.get("symbol", "XAU/USD"))
        # Arm immediately, not 400 lines later at the decision. An agent that
        # raises, a market-data timeout, or a Supabase error all abort the
        # cycle long before a decision exists; without this the `finally`
        # flush has nothing to send and the hour passes silently. The payload
        # is upgraded in place once the real decision is known.
        if should_send_hourly_status(config):
            status_delivery.arm(
                decision={"symbol": symbol, "decision": "PENDING"},
                all_results={"symbol": symbol},
                database=database,
            )
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
                status_delivery.mark_sent()
                telegram.send_message("🟡 <b>SmartSignal — Market Status</b>\n━━━━━━━━━━━━━━━━━━━━\n📈 Price: N/A\n🎯 Decision: WAIT\n📊 Outside trading hours\n\n<b>Reason:</b>\n• Outside trading hours\n━━━━━━━━━━━━━━━━━━━━")
            if post_news_was_sent:
                status_delivery.mark_sent()
            return
        market_data = MarketDataService(config)
        data = market_data.get_gold_data()
        if not data:
            _report_data_outage(
                telegram, config, symbol, delivery=status_delivery,
                reason="No market data returned (provider quota, rate limit, or outage).",
            )
            return
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
            _report_data_outage(
                telegram, config, symbol, delivery=status_delivery,
                reason=f"Data source '{integrity.get('source') or data.get('source')}' cannot support signal generation.",
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
            _report_data_outage(
                telegram, config, symbol, delivery=status_delivery,
                reason=f"Price {_cp:.2f} failed the sanity range [{_sane_min:.0f}-{_sane_max:.0f}].",
            )
            return
        persisted_macro_context = database.get_macro_context()
        # The verified snapshot is an input to three of the five voting agents,
        # so it has to exist before any of them are asked anything. It is built
        # purely from `data` and `config`, both already in hand.
        verified_snapshot = build_market_snapshot(data, config)
        data["verified_snapshot"] = verified_snapshot
        if has_symbol_active_trades:
            high, low = _latest_candle_extremes(data)
            recent_candles = (((data.get("timeframes", {}) or {}).get("5m") or {}).get("data") or data.get("data") or [])[-6:]
            try:
                news_pre_cfg = {**config, "macro_context": persisted_macro_context} if persisted_macro_context else config
                news_pre = NewsRiskAgent(news_pre_cfg).check()
            except Exception:
                news_pre = {}
            news_blocked_pre = bool(news_pre.get("can_trade") is False or str(news_pre.get("market_status", "")).upper() in {"DANGER", "HIGH_VOLATILITY"})
            # Give the exit the same agent read the rest of the cycle gets.
            #
            # The thesis exit used to decide on two candles alone, 23 lines
            # before these agents were polled -- so on 2026-07-30 it closed a
            # SELL while Classical 71, SMC 90 and Multi-Timeframe 83 were all
            # still arguing SELL, and the planner republished that exact zone
            # as an A+ map hours later.
            #
            # These are the same six calls the cycle makes below, on the same
            # `data`. Failure is non-fatal: an empty book makes the exit behave
            # exactly as it did before.
            exit_agent_details: Dict[str, Any] | None = None
            try:
                pre_exit_results = {
                    "technical": run_agent("technical", TechnicalAgent(config), data),
                    "classical": run_agent("classical", ClassicalAgent(config), data),
                    "smc": run_agent("smc", SMCAgent(config), data),
                    "price_action": run_agent("price_action", PriceActionAgent(config), data),
                    "multitimeframe": run_agent("multitimeframe", MultiTimeframeAgent(config), data),
                }
                exit_agent_details = _compact_agent_details(pre_exit_results)
                logger.info(
                    "Exit agent book for %s: %s",
                    symbol,
                    {k: f"{v.get('direction')} {v.get('confidence')}" for k, v in exit_agent_details.items()},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not build the exit agent book for %s: %s", symbol, exc)
                exit_agent_details = None
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
                agent_details=exit_agent_details,
            )
        if not session.get("trading_allowed"): return
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
        try:
            all_results["reversal_watch"] = _active_reversal_watch(database, symbol=symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load active reversal watch for %s: %s", symbol, exc)
            all_results["reversal_watch"] = {}

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
            session_plan["execution_audit"] = _session_plan_execution_audit(session_plan, stage="plan_built")
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
            if session_plan_snapshot_id:
                try:
                    database.merge_session_plan_payload(
                        session_plan_snapshot_id,
                        {
                            "execution_audit": _session_plan_execution_audit(
                                session_plan,
                                stage="delivery_evaluated",
                                delivery_send_planned=bool(session_plan_delivery_meta.get("send")),
                                delivery_reason=session_plan_delivery_meta.get("reason"),
                                delivery_kind=session_plan_delivery_meta.get("kind"),
                            )
                        },
                    )
                except Exception as audit_exc:  # noqa: BLE001
                    logger.warning("Failed to merge session plan execution audit: %s", audit_exc)
        except Exception as exc:  # noqa: BLE001
            # 21 of the last 300 cycles died here with a bare
            # "'NoneType' object has no attribute 'get'" and no traceback, so
            # the failing line was unknowable from the logs. A crash rate of
            # 7% is not a rounding error: those are cycles that produced no
            # map at all, and nobody could see why.
            logger.exception("Failed to build session plan for %s", symbol)
            all_results["session_plan"] = {
                "enabled": True,
                "plan_ready": False,
                "plan_status": "ERROR",
                # Keep the type in the stored reason so the rejection report
                # can separate real refusals from crashes at a glance.
                #
                # ...and the LOCATION. "'NoneType' object has no attribute
                # 'get'" names the symptom, never the site: there are
                # hundreds of `.get(` calls under build_plan and the message
                # fits every one of them. Five crashed cycles have been
                # sitting in every report since #16 with no way to act on
                # them, because `logger.exception` writes the traceback to
                # the run log while the REPORT reads the stored row, and the
                # stored row had only the text.
                #
                # The last in-project frame is the one that matters -- the
                # deepest line inside this repository, skipping site-packages
                # -- so it is folded into the reason the report already
                # groups on. Truncated because that grouping key is 48 chars.
                "plan_reason": (
                    f"planner crashed: {type(exc).__name__}: {exc}"
                    f" @ {_crash_site(exc)}"
                ),
                "crash_site": _crash_site(exc),
                "crash_traceback": "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )[-4000:],
            }
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

                    # ── The risk agent's verdict is not advisory ──
                    #
                    # This path rebuilt the signal straight out of
                    # ``all_results["risk"]`` -- its entry, its stop, its
                    # targets -- while ignoring the one field that says
                    # whether that plan is tradeable at all. So a setup the
                    # risk agent had already refused was published as a live
                    # pending order, carrying the refused numbers.
                    #
                    # 2026-08-03 16:11, TRADE_..._d0c708d9: SELL 4045.99 with
                    # a 398-point stop. ``rr_filter`` was False (no mapped
                    # level pays for that stop) and ``trade_grade_filter`` was
                    # False, so ``approved`` was False. The card went out
                    # anyway as "SELL LIMIT ... Status: Pending order".
                    #
                    # Every other execution route honours this flag; the
                    # scale-in path checks it at line ~2552. This one did not,
                    # which made the whole risk layer optional on the busiest
                    # path in the system.
                    #
                    # Refusing here costs nothing that was ever legitimate: if
                    # the plan clears the risk agent, ``approved`` is True and
                    # the branch runs exactly as before.
                    if (macro_confirmed or gemini_confirmed) and not (
                        all_results.get("risk", {}) or {}
                    ).get("approved", True):
                        _risk = all_results.get("risk", {}) or {}
                        _failed = [
                            name for name, ok in
                            ((_risk.get("risk_metrics") or {}).get("checks") or {}).items()
                            if not ok
                        ]
                        logger.info(
                            "❌ Path 2 blocked: risk agent refused this plan (%s; failed: %s)",
                            _risk.get("rejection_reason") or "not approved",
                            ", ".join(_failed) or "unspecified",
                        )
                        macro_confirmed = False
                        gemini_confirmed = False

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
                        # RECORD THE PRE-FLOOR STOP SO IT CAN BE AUDITED.
                        #
                        # `analyze_sl_floor` answers the only question that
                        # decides whether the noise floor earns its keep:
                        # would the structural stop have survived? It reads
                        # that stop from `signal_snapshot.session_plan`, which
                        # only the planner path writes.
                        #
                        # Every consensus and dual-agent trade therefore fell
                        # into `no_structural_stop` and was silently excluded
                        # -- including 36e5cc8a, the exact trade that raised
                        # the question. The measurement would have covered the
                        # minority path and reported it as the whole picture.
                        #
                        # The risk agent already computes this; it just was
                        # never persisted on this route.
                        _rm = (risk.get("risk_metrics") or {})
                        decision["risk_geometry"] = {
                            "structural_sl_points": _rm.get("structural_sl_points"),
                            "shipped_sl_points": sl.get("distance_points"),
                            "floor_points": _rm.get("min_sl_distance_points"),
                            "target_method": _rm.get("target_method"),
                            "sl_method": sl.get("method"),
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
            elif action == "ALLOW_MAP_RETIRED":
                # The live book has outvoted a map that no longer describes
                # it. Write the verdict onto the plan before anything else
                # reads it: DayMapSanityService refuses any side that
                # disagrees with a CONFIRMED map, so leaving the old stamp in
                # place would block this same signal one gate later, and the
                # retirement would be an announcement with no effect.
                logger.info(
                    "Directional authority retired the %s day map for %s: %s",
                    authority_review.get("authority_direction"), symbol,
                    authority_review.get("reason"),
                )
                for _plan_ref in (
                    all_results.get("session_plan"),
                    decision.get("session_plan"),
                ):
                    DirectionalAuthorityService.apply_retirement(_plan_ref, authority_review)
                decision.setdefault("reasons", []).append(str(authority_review.get("reason") or "Day map retired by the live agent book"))

        send_hourly_now = should_send_hourly_status(config)
        # Arm as soon as the cycle has a decision: every later exit is covered
        # by the flush in `finally`, whatever branch it takes.
        if send_hourly_now:
            status_delivery.arm(decision=decision, all_results=all_results, database=database)
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
                            database.merge_session_plan_payload(
                                session_plan_snapshot_id,
                                {"execution_audit": {"delivery_sent": True, "delivery_sent_reason": session_plan_delivery_meta.get("reason")}},
                            )
                        except Exception as mark_exc:  # noqa: BLE001
                            logger.warning("Failed to mark session plan Telegram delivery: %s", mark_exc)
                    logger.info("Session plan Telegram sent for %s (%s)", symbol, session_plan_delivery_meta.get("reason"))
                else:
                    logger.warning("Session plan Telegram returned False for %s", symbol)
            except Exception as delivery_exc:  # noqa: BLE001
                logger.warning("Failed to deliver session plan Telegram for %s: %s", symbol, delivery_exc)

        # Dynamic risk is the account-level circuit breaker: it halts trading
        # after a losing streak, after the daily loss limit is spent, and
        # raises the confidence/quality bar in between. Its verdict was being
        # computed into all_results and then never read, so none of those
        # protections actually stopped anything -- the system kept opening
        # trades no matter how much it had just lost.
        #
        # The check sits here, ahead of BOTH execution routes (planner ladder
        # and the direct BUY/SELL path), because a halt must apply to every
        # way an order can be created, not just the one that happens to run.
        #
        # The ladder builds its legs from the plan's session_bias rather than
        # from decision_type, so it can place orders on a cycle that reads
        # WAIT. Gating on decision_type alone would leave that route open
        # during a halt, which is precisely the path that fired yesterday.
        dynamic_block = _dynamic_risk_block_for_cycle(
            decision_type=decision_type,
            decision=decision,
            session_plan=all_results.get("session_plan") or {},
            dynamic_risk=all_results.get("dynamic_risk", {}) or {},
        )
        if dynamic_block:
            logger.info(
                "Dynamic risk blocked %s for %s: %s",
                decision_type, symbol, dynamic_block,
            )
            decision["dynamic_risk_block"] = dynamic_block
            _notify_blocked_directional_signal(
                telegram=telegram, decision=decision, all_results=all_results,
                database=database, config=config, send_hourly_now=send_hourly_now,
                delivery=status_delivery,
                stage="dynamic risk", reason=dynamic_block,
            )
            return

        # Phase 2: if the morning/session planner already prepared a strong
        # PRIMARY / STANDBY thesis before the move, publish those pending ladder
        # orders now instead of waiting for a late one-off signal after price has
        # already traveled.
        planner_gate_preview = _planner_execution_gate(decision, config) if isinstance((all_results.get("session_plan") or {}), dict) and (all_results.get("session_plan") or {}).get("plan_ready") else None
        ladder_created = _execute_session_plan_ladder(
            decision,
            all_results,
            [t for t in open_trades_snapshot if normalize_symbol(t.get("symbol") or symbol) == normalized_symbol],
            database,
            telegram,
            config,
        )
        if session_plan_snapshot_id and planner_gate_preview:
            try:
                database.merge_session_plan_payload(
                    session_plan_snapshot_id,
                    {
                        "execution_audit": {
                            "planner_gate_allow": bool(planner_gate_preview.get("allow")),
                            "planner_gate_kind": planner_gate_preview.get("kind"),
                            "planner_gate_reason": planner_gate_preview.get("reason"),
                            "ladder_created": int(ladder_created or 0),
                            # The gate verdict alone was not enough. On
                            # 2026-08-04, 9 of 20 maps that produced no order
                            # carried a gate reason that reads as a PASS --
                            # "3 qualified agents aligned with the mapped
                            # direction" -- because the gate had allowed them
                            # and a LATER check stopped the ladder. Without
                            # this field the audit pointed at the wrong step.
                            "ladder_stop_reason": (
                                _LAST_LADDER_STOP.get("reason")
                                if not ladder_created and _LAST_LADDER_STOP
                                else None
                            ),
                            "ladder_stop_detail": (
                                {k: v for k, v in _LAST_LADDER_STOP.items() if k != "reason"}
                                or None
                            ) if not ladder_created else None,
                            # THE AGENT COUNT, AS IT STOOD.
                            #
                            # "requires 3 qualified agents ... got 2" is the
                            # largest single reason a READY map produces no
                            # order. Whether that is the bar working or the
                            # bar missing by a hair depends on HOW FAR the
                            # agents that agreed fell short -- and nothing
                            # recorded it.
                            #
                            # `_session_plan_agent_opinions` already computes
                            # exactly this, but only inside
                            # `_decorate_session_plan_for_delivery`, which
                            # builds a throwaway copy for the Telegram card.
                            # The persisted row never carried it, so the
                            # question could not be answered from history.
                            "agent_reads": _session_plan_agent_opinions(
                                decision.get("agent_details") or {}
                            ),
                            "agent_min_confidence": _safe_float(
                                ((config.get("signal_requirements") or {})
                                 .get("agent_min_confidence")), 70.0
                            ),
                            "mapped_side": str(
                                (all_results.get("session_plan") or {}).get("session_bias")
                                or ""
                            ).upper(),
                        }
                    },
                )
            except Exception as audit_exc:  # noqa: BLE001
                logger.warning("Failed to merge planner gate audit: %s", audit_exc)
        if ladder_created:
            logger.info("Session-plan ladder created %s pending order(s) for %s", ladder_created, symbol)
            # The ladder sends its own pending-order alerts.
            status_delivery.mark_sent()
            return
        if decision_type in {"BUY", "SELL"}:
            symbol_trades = [t for t in open_trades_snapshot if normalize_symbol(t.get("symbol") or symbol) == normalized_symbol]
            adaptive = AdaptiveExecutionService(config).review(decision, symbol_trades)
            adaptive_action = str(adaptive.get("action") or "ALLOW_NEW")
            if adaptive_action == "KEEP_PENDING":
                logger.info("Adaptive execution kept pending for %s %s: %s", decision_type, symbol, adaptive.get("reason"))
                _notify_blocked_directional_signal(
                    telegram=telegram, decision=decision, all_results=all_results,
                    database=database, config=config, send_hourly_now=send_hourly_now, delivery=status_delivery,
                    stage="adaptive execution — kept pending", reason=adaptive.get("reason"),
                )
                return
            if adaptive_action == "NO_TRADE_MISSED_MOVE":
                logger.info("Adaptive execution skipped %s %s as missed move: %s", decision_type, symbol, adaptive.get("reason"))
                _notify_blocked_directional_signal(
                    telegram=telegram, decision=decision, all_results=all_results,
                    database=database, config=config, send_hourly_now=send_hourly_now, delivery=status_delivery,
                    stage="adaptive execution — missed move", reason=adaptive.get("reason"),
                )
                return
            if adaptive_action in {"PROMOTE_TO_MARKET", "REPLACE_WITH_CONTINUATION"}:
                decision = adaptive.get("decision") or decision
                decision["adaptive_execution"] = {
                    "action": adaptive_action,
                    "reason": adaptive.get("reason"),
                }
                logger.info("Adaptive execution %s for %s %s: %s", adaptive_action, decision_type, symbol, adaptive.get("reason"))

            # ── Golden dual-entry exception (operator 2026-08-07) ────────
            # Only while a same-direction pending is alive: price tags 0.618,
            # a candle CLOSES back beyond it, >=2 qualified supporters, and
            # the pending rests inside the 0.618-0.786 band -> FULL market
            # now + the pending kept as a second full entry (the single
            # exception to the >=200-pt separation rule).
            golden_dual = None
            if decision_type in {"BUY", "SELL"} and open_trades_snapshot:
                try:
                    from services.golden_dual_entry import review_golden_dual_entry
                    from utils.indicators import detect_swing_points as _gsp
                    _g_candles = (((data.get("timeframes", {}) or {}).get("5m") or {})
                                  .get("data") or data.get("data") or [])
                    _pendings = [
                        t for t in open_trades_snapshot
                        if str(t.get("status") or "").upper() == "PENDING"
                        and str(t.get("type") or t.get("side") or "").upper() == decision_type
                    ]
                    if _g_candles and _pendings:
                        _sw = _gsp(_g_candles)
                        _hi = float((_sw.get("highs") or [{}])[-1].get("price") or 0.0)
                        _lo = float((_sw.get("lows") or [{}])[-1].get("price") or 0.0)
                        _min_conf = float(
                            ((config.get("signal_requirements") or {})
                             .get("agent_min_confidence"))
                            or config.get("agent_min_confidence") or 67)
                        _sup = 0
                        for _an in ("technical", "classical", "smc", "price_action",
                                  "multitimeframe", "macro_fundamental"):
                            _ar = all_results.get(_an) or {}
                            if (float(_ar.get("confidence") or 0) >= _min_conf
                                    and str(_ar.get("direction") or "").upper() == decision_type):
                                _sup += 1
                        golden_dual = review_golden_dual_entry(
                            direction=decision_type, candles=_g_candles,
                            swing_low=_lo, swing_high=_hi,
                            pending_entry=float(_pendings[0].get("entry_price") or 0.0),
                            qualified_support=_sup, config=config)
                except Exception as exc:  # noqa: BLE001 - never break the cycle
                    logger.warning("Golden dual review failed: %s", exc)
            if golden_dual and golden_dual.get("action") == "GOLDEN_DUAL_ENTRY":
                decision["golden_dual_entry"] = golden_dual
                logger.info("Golden dual entry armed for %s %s: %s",
                            decision_type, symbol, golden_dual.get("reason"))
                if not str((decision.get("signal") or {}).get("order_type") or ""
                           ).upper().endswith("MARKET"):
                    decision = AdaptiveExecutionService(config)._promote_to_market(
                        decision, float(decision.get("current_price") or 0.0))
                # Both entries carry stops/targets from the unified law
                # (liquidity rule stop + liquidity/RR targets) computed from
                # the market entry's OWN price. The pending keeps the levels
                # it was created with; management stays per-trade.
                try:
                    _g_cand = _select_setup_candidate(decision_type, all_results) or {}
                    _g_lv = _planner_trade_levels(
                        config, direction=decision_type,
                        entry_price=float(decision.get("current_price") or 0.0),
                        stop_loss=float((decision.get("signal") or {})
                                      .get("stop_loss") or 0.0),
                        target_price=float(_g_cand.get("target_liquidity")
                                         or _g_cand.get("target_price") or 0.0),
                        symbol=symbol, candidate=_g_cand)
                    _g_sig = decision.setdefault("signal", {})
                    _g_sig["stop_loss"] = _g_lv["stop_loss"]
                    _g_sig["tp1"] = _g_lv["tp1"]
                    _g_sig["tp2"] = _g_lv["tp2"]
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Golden dual level rebuild failed: %s", exc)

            # Phase E: even if legacy path 1 / path 2 found an entry, it must
            # still be inside or near the confirmed day map. This prevents small
            # local execution zones from bypassing a stronger planner view.
            day_map_review = DayMapSanityService(config).review(decision, all_results.get("session_plan", {}) or {})
            decision["day_map_sanity"] = day_map_review
            if str(day_map_review.get("action") or "ALLOW") != "ALLOW":
                logger.info("Day-map sanity blocked %s for %s: %s", decision_type, symbol, day_map_review.get("reason"))
                _notify_blocked_directional_signal(
                    telegram=telegram, decision=decision, all_results=all_results,
                    database=database, config=config, send_hourly_now=send_hourly_now, delivery=status_delivery,
                    stage="day-map sanity", reason=day_map_review.get("reason"),
                )
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
                    _notify_blocked_directional_signal(
                        telegram=telegram, decision=decision, all_results=all_results,
                        database=database, config=config, send_hourly_now=send_hourly_now, delivery=status_delivery,
                        stage="cross-path distance", reason=_cross_block,
                    )
                    return

            if golden_dual and golden_dual.get("action") == "GOLDEN_DUAL_ENTRY":
                governance = {
                    "action": "ALLOW_NEW",
                    "reason": "golden dual entry exception (0.618 close-confirm, pending at >= 0.70 fibo)",
                    "cancelled_ids": [],
                }
            elif adaptive_action in {"PROMOTE_TO_MARKET", "REPLACE_WITH_CONTINUATION"}:
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
                _notify_blocked_directional_signal(
                    telegram=telegram, decision=decision, all_results=all_results,
                    database=database, config=config, send_hourly_now=send_hourly_now, delivery=status_delivery,
                    stage="pending governor", reason=governance.get("reason"),
                )
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

            # Adaptive execution may waive the DUPLICATE filter, never the
            # post-TP2 block.
            #
            # PROMOTE_TO_MARKET and REPLACE_WITH_CONTINUATION exist to let a
            # stronger thesis replace a stale pending order, so skipping the
            # duplicate check is deliberate: the new order is meant to look
            # like the old one. But that skip took the whole of
            # duplicate_signal_reason with it, and the post-TP2 re-entry guard
            # lives inside that function.
            #
            # 2026-08-03: SELL 79fb5a6e took TP2 at 4022.31 at 13:38. Three
            # minutes later a new SELL LIMIT went out at 4037.48 -- 152 points
            # above that TP2, inside both the 250-point distance bar and the
            # 3-hour window. The guard was correct and never ran.
            #
            # An exhausted level is exhausted whatever route the signal took
            # to reach execution, so the block is evaluated separately and
            # unconditionally.
            duplicate_reason = None if adaptive_action in {"PROMOTE_TO_MARKET", "REPLACE_WITH_CONTINUATION"} else duplicate_signal_reason(decision, database, config)
            if not duplicate_reason:
                duplicate_reason = _post_tp2_reentry_reason(decision, database, config)
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
                _notify_blocked_directional_signal(
                    telegram=telegram, decision=decision, all_results=all_results,
                    database=database, config=config, send_hourly_now=send_hourly_now, delivery=status_delivery,
                    stage="duplicate / re-entry filter", reason=duplicate_reason,
                )
                return
            # Selective memory: how has this exact pattern done lately, in
            # this session? The learning service already consumes the same
            # rows nightly to retune agent weights, but nothing asked the
            # question at the moment of execution -- so a setup could fail
            # three times in a session and still be taken at full confidence
            # the next morning.
            try:
                setup_review = SetupPerformanceService(database, config).review(decision)
                decision["setup_performance"] = setup_review
                penalty = _safe_float(setup_review.get("confidence_penalty"), 0.0)
                if setup_review.get("veto"):
                    logger.info(
                        "Setup memory vetoed %s for %s: %s",
                        decision_type, symbol, setup_review.get("reason"),
                    )
                    _notify_blocked_directional_signal(
                        telegram=telegram, decision=decision, all_results=all_results,
                        database=database, config=config, send_hourly_now=send_hourly_now,
                        delivery=status_delivery,
                        stage="setup memory", reason=setup_review.get("reason"),
                    )
                    return
                if penalty > 0:
                    before = _safe_float(decision.get("confidence"), 0.0)
                    decision["confidence"] = round(max(0.0, before - penalty), 1)
                    decision.setdefault("warnings", []).append(str(setup_review.get("reason")))
                    logger.info(
                        "Setup memory lowered confidence %.1f%% → %.1f%% for %s: %s",
                        before, decision["confidence"], symbol, setup_review.get("reason"),
                    )
            except Exception as exc:  # noqa: BLE001 - memory must never block a trade
                logger.warning("Setup performance review failed: %s", exc)

            signal_violations = validate_signal_before_send(decision, config, open_trades_snapshot)
            if signal_violations:
                logger.error(
                    "Signal validation failed for %s %s: %s",
                    decision_type, symbol, "; ".join(signal_violations),
                )
                _notify_blocked_directional_signal(
                    telegram=telegram, decision=decision, all_results=all_results,
                    database=database, config=config, send_hourly_now=send_hourly_now,
                    delivery=status_delivery,
                    stage="final validation", reason="; ".join(signal_violations),
                )
                return
            trade_id = database.new_trade_id()
            decision["trade_id"] = trade_id
            delivered = False
            try:
                delivered = bool(telegram.send_signal(decision))
                if delivered:
                    # The trade alert already carries this cycle's context.
                    status_delivery.mark_sent()
            except Exception as exc:  # noqa: BLE001
                telegram.send_error_alert(f"Signal delivery failed: {exc}")
                return
            if delivered and not decision.get("golden_dual_entry"):
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
                # The delivered half of the trail. Without it the audit can
                # count refusals but not the rate they represent.
                _record_decision_audit(
                    database, decision, config,
                    stage="delivered", outcome="SENT",
                    reason=decision.get("entry_mode"),
                )
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
                status_delivery.arm(decision=decision, all_results=all_results, database=database)
            else:
                # Silence here is intentional, but it used to be indistinguishable
                # from a bug. Record which gate closed so a missing status can be
                # explained from the run log alone.
                notif = config.get("notifications", {}) or {}
                logger.info(
                    "Market status suppressed for %s: WAIT with status gate off "
                    "(event=%s, SEND_STATUS_ON_MANUAL=%s, hourly_status=%s, "
                    "send_no_signal_updates=%s)",
                    symbol,
                    os.environ.get("GITHUB_EVENT_NAME") or "local",
                    os.environ.get("SEND_STATUS_ON_MANUAL") or "unset",
                    notif.get("hourly_status", False),
                    notif.get("send_no_signal_updates", False),
                )
    except Exception as exc:
        # Without the traceback a crash here is invisible: the workflow still
        # prints nothing but the Telegram alert text, which is rarely enough
        # to locate the failing line.
        logger.exception("Analysis cycle failed for %s", config.get("symbol", "unknown"))
        telegram.send_error_alert(str(exc))
    finally:
        status_delivery.flush()

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
