"""Main analysis script.

Runs every 5 minutes via cron-job.org/GitHub Actions. Fetches market data, runs agents,
يطبق إدارة المخاطر وDecision، ثم يحفظ ويرسل الإشارة إذا كانت مؤهلة.
"""

from __future__ import annotations

import logging
import os
import sys
import html
from datetime import datetime, timezone
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
from services.telegram_bot import TelegramService
from services.learning_service import get_learning_service
from services.llm_review import get_gemini_review_service
from utils.helpers import load_config, setup_logging
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
    for key in ('entry_price', 'current_price'):
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
            if age_minutes <= cooldown: return f"Duplicate {direction} blocked: recently closed {outcome} trade in same zone."
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
        weights = config.get("agent_weights", {}) or {}
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
        reason = f"Price within {distance_points:.0f} points of {'support' if side == 'BUY' else 'resistance'} level {nearest_level:.2f}"
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
        message = (
            f"➕ <b>Scale-In {html.escape(symbol)} — {html.escape(side)}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Parent:</b> {html.escape(parent_id)}\n"
            f"• <b>Entry:</b> {entry_price:.2f}\n"
            f"• <b>Level:</b> {nearest_level:.2f} ({distance_points:.0f} pts away)\n"
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


def _build_market_status_message(
    decision: Dict[str, Any],
    all_results: Dict[str, Any],
    database: DatabaseService,
    config: Dict[str, Any] | None = None,
) -> str:
    current_symbol = str(decision.get("symbol") or all_results.get("symbol") or (config or {}).get("symbol") or "XAU/USD")
    current_price = _safe_float(decision.get("current_price", all_results.get("current_price", 0)), 0.0)
    prices_text = _market_prices_text(config, current_symbol, current_price)

    # ── Open trades summary ──────────────────────────────────────────────
    open_trades = database.get_open_trades()
    trades_section = ""
    if open_trades:
        from utils.instruments import price_to_points
        trade_lines: List[str] = []
        net_pts = 0.0
        for t in open_trades[:20]:
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
            # TP1 progress
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
        if len(open_trades) > 20:
            trade_lines.append(f"… and {len(open_trades) - 20} more")
        net_usd = net_pts / 10.0
        net_marker = "🟢" if net_pts > 0 else "🔴" if net_pts < 0 else "➖"
        trades_section = (
            f"──────────────────\n"
            f"📊 <b>Open Trades ({len(open_trades)})</b>\n"
            + "\n".join(trade_lines) + "\n"
            f"{net_marker} <b>Net:</b> {net_pts:+.0f}pts ({net_usd:+.1f}$)\n"
        )

    # ── Gemini review (keep concise) ─────────────────────────────────────
    gemini_context = ""
    gemini_analysis = decision.get("gemini_analysis", {}) or {}
    if gemini_analysis.get("available"):
        bias = gemini_analysis.get("market_bias", "NEUTRAL")
        reason = gemini_analysis.get("reason", "")
        gemini_context = (
            f"🧠 <b>Gemini:</b> {html.escape(str(bias))} — {html.escape(str(reason))}\n"
        )

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
        direction = str(result.get("direction") or result.get("signal") or "WAIT").upper()
        if direction in {"NEUTRAL", "HOLD", "NO_TRADE", "NONE", ""}: direction = "WAIT"
        signals = result.get("signals") or result.get("reasons") or []
        if not signals and key == "technical": signals = (result.get("technical", {}) or {}).get("reasons") or []
        if not isinstance(signals, list): signals = [signals] if signals else []
        summary = result.get("summary") or result.get("reasoning") or ""
        details[key] = {"label": label, "direction": direction, "confidence": result.get("confidence", 0), "summary": summary, "signals": [str(x) for x in signals[:4] if x]}
    return details


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
            if should_send_hourly_status(config):
                telegram.send_message("🟡 <b>SmartSignal — Market Status</b>\n━━━━━━━━━━━━━━━━━━━━\n📈 Price: N/A\n🎯 Decision: WAIT\n📊 Outside trading hours\n\n<b>Reason:</b>\n• Outside trading hours\n━━━━━━━━━━━━━━━━━━━━")
            return
        market_data = MarketDataService(config)
        data = market_data.get_gold_data()
        if not data: return
        if has_symbol_active_trades:
            high, low = _latest_candle_extremes(data)
            OpenTradesManager(config).update_trades(open_trades=[t for t in open_trades_snapshot if normalize_symbol(t.get("symbol") or symbol) == normalized_symbol], current_price=float(data.get("current_price", 0)), candle_high=high, candle_low=low, database=database, telegram=telegram, now=datetime.now(timezone.utc))
        if not session.get("trading_allowed"): return
        verified_snapshot = build_market_snapshot(data, config)
        data["verified_snapshot"] = verified_snapshot
        persisted_macro_context = database.get_macro_context()
        macro_input = {**data, "macro_context": persisted_macro_context} if persisted_macro_context else data
        macro = run_agent("macro_fundamental", MacroFundamentalAgent(config), macro_input)
        news_config = {**config, "macro_context": persisted_macro_context} if persisted_macro_context else config
        news = NewsRiskAgent(news_config).check()
        if isinstance(news, dict) and isinstance(macro, dict) and macro.get("macro_direction"):
            news["macro_direction"] = macro.get("macro_direction")
            news["macro_agent"] = macro
        all_results = {"technical": run_agent("technical", TechnicalAgent(config), data), "classical": run_agent("classical", ClassicalAgent(config), data), "smc": run_agent("smc", SMCAgent(config), data), "price_action": run_agent("price_action", PriceActionAgent(config), data), "multitimeframe": run_agent("multitimeframe", MultiTimeframeAgent(config), data), "macro_fundamental": macro, "current_price": data["current_price"], "symbol": symbol, "session": session, "verified_snapshot": verified_snapshot, "news": news, "daily_bias": run_agent("daily_bias", DailyBiasAgent(config), data)}
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
            await learning_service.load_current_weights()
        except Exception: pass
        decision = await DecisionAgent(config, learning_service=learning_service).decide_async(all_results)
        decision["agent_details"] = _compact_agent_details(all_results)
        decision["symbol"] = symbol
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
        decision_type = str(decision.get("decision") or "").upper()
        send_hourly_now = should_send_hourly_status(config)
        if (decision_type in {"BUY", "SELL"}) or (decision_type == "WAIT" and send_hourly_now):
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
                    decision["gemini_news_review"] = gemini.interpret_news_context({"symbol": symbol, "current_price": data.get("current_price"), "session": all_results.get("session"), "news": all_results.get("news"), "daily_bias": all_results.get("daily_bias"), "technical_context": all_results.get("technical")})
                    _log_gemini_result("news review", decision.get("gemini_news_review"))
            except Exception:
                logger.exception("🧠 Gemini analysis block failed")
        elif decision_type == "WAIT":
            logger.info("🧠 Gemini skipped: normal WAIT without hourly status")
        if decision_type in {"BUY", "SELL"}:
            if duplicate_signal_reason(decision, database, config): return
            trade_id = database.new_trade_id()
            decision["trade_id"] = trade_id
            delivered = False
            try:
                delivered = bool(telegram.send_signal(decision))
            except Exception as exc:  # noqa: BLE001
                telegram.send_error_alert(f"Signal delivery failed: {exc}")
                return
            if delivered:
                database.save_trade(decision)
            else:
                telegram.send_error_alert("Signal delivery failed: Telegram returned False; trade was not saved.")
        elif send_hourly_now:
            telegram.send_message(_build_market_status_message(decision, all_results, database, config))
    except Exception as exc:
        telegram.send_error_alert(str(exc))

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
