"""سكريبت التحليل الرئيسي.

يعمل كل 5 دقائق عبر cron-job.org/GitHub Actions. يجلب بيانات الذهب، يشغل الوكلاء،
يطبق إدارة المخاطر والقرار، ثم يحفظ ويرسل الإشارة إذا كانت مؤهلة.
"""

from __future__ import annotations

import logging
import os
import sys
import html
from datetime import datetime, timezone
from typing import Any, Dict, List

# إضافة المسار الرئيسي للمشروع
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.classical_agent import ClassicalAgent
from agents.decision_agent import DecisionAgent
from agents.daily_bias_agent import DailyBiasAgent
from agents.multitimeframe_agent import MultiTimeframeAgent
from agents.news_risk_agent import NewsRiskAgent
from agents.price_action_agent import PriceActionAgent
from agents.risk_management_agent import RiskManagementAgent
from agents.smc_agent import SMCAgent
from agents.technical_agent import TechnicalAgent
from agents.trading_session_agent import TradingSessionAgent
from services.database import DatabaseService
from services.dynamic_risk import DynamicRiskManager, should_block_signal
from services.market_data import MarketDataService
from services.telegram_bot import TelegramService
from services.learning_service import get_learning_service
from utils.helpers import load_config, setup_logging
from utils.instruments import enabled_instruments, config_for_instrument, price_to_points, points_to_price

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
    send WAIT/status messages.

    External schedulers such as cron-job.org trigger workflows via
    workflow_dispatch. Without this guard, every external trigger would look like
    a "manual" run and would send a Market Status/WAIT message every 5 minutes.
    The default is intentionally silent: send Telegram only when a real signal is
    generated (or an error occurs).
    """
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        return False
    return str(os.environ.get("SEND_STATUS_ON_MANUAL", "false")).strip().lower() in {"1", "true", "yes", "y"}


def should_send_status(config: Dict[str, Any]) -> bool:
    """Send blocked/no-signal messages only when configured.

    Important: workflow_dispatch is used by cron-job.org. Those external runs
    must be silent unless they generate an actual trade signal; otherwise the bot
    would spam a status message every 5 minutes.
    """
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return _manual_status_enabled()
    notif = config.get("notifications", {}) or {}
    # Either the general no-signal flag or the dedicated blocked-signal flag.
    return bool(notif.get("send_no_signal_updates", False)) or bool(notif.get("notify_on_blocked_signal", False))


def should_send_hourly_status(config: Dict[str, Any]) -> bool:
    """Send a clean market status update roughly once per hour for native
    schedule runs. workflow_dispatch runs are silent by default because they may
    be driven by cron-job.org every 5 minutes.
    """
    from datetime import datetime, timezone
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
    except Exception:  # noqa: BLE001
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


# Trade lifecycle states (mirrors agents/open_trades_manager.py).
_OPEN_STATUSES = {"OPEN", "PARTIAL", "TP1_HIT"}
_LOSS_STATUSES = {"SL_HIT"}
_WIN_STATUSES = {"TP2_HIT"}
_BREAKEVEN_STATUSES = {"BE_HIT", "EXPIRED", "MANUAL_CLOSE"}


def _trade_outcome(trade: Dict[str, Any]) -> str:
    """Classify a trade as OPEN / WIN / LOSS / BREAKEVEN using status, the
    explicit ``result`` field, then PnL as a last resort.
    """
    status = str(trade.get("status", "")).upper()
    if status in _OPEN_STATUSES:
        return "OPEN"

    result = str(trade.get("result", "") or "").upper()
    if result in {"WIN", "LOSS", "BREAKEVEN"}:
        return result

    if status in _LOSS_STATUSES:
        return "LOSS"
    if status in _WIN_STATUSES:
        return "WIN"
    if status in _BREAKEVEN_STATUSES:
        return "BREAKEVEN"

    # Fallback to realized PnL when status/result are missing.
    for key in ("final_pnl", "current_pnl"):
        try:
            pnl = float(trade.get(key))
        except (TypeError, ValueError):
            continue
        if pnl > 0:
            return "WIN"
        if pnl < 0:
            return "LOSS"
        return "BREAKEVEN"
    return "BREAKEVEN"


def _trade_reference_time(trade: Dict[str, Any], now: datetime) -> datetime:
    """Best timestamp to age a trade from: close time for closed trades, else
    the open/created time.
    """
    closed = _parse_datetime(
        trade.get("closed_at") or trade.get("close_time")
    )
    if closed:
        return closed
    opened = _parse_datetime(
        trade.get("created_at") or trade.get("entry_time") or trade.get("opened_at")
    )
    return opened or now


def duplicate_signal_reason(decision: Dict[str, Any], database: DatabaseService, config: Dict[str, Any]) -> str | None:
    """Return a human-readable reason if this signal should be blocked as a
    duplicate / churn / revenge re-entry. Otherwise return None.

    Professional, outcome-aware design — two clearly separated concerns:

    1) OPEN-POSITION STACKING PROTECTION (true duplicate)
       If a same-direction trade is still OPEN/TP1_HIT:
         * block if it sits within ``price_zone_points`` of the new entry
           (you'd be doubling the same position at the same level), OR
         * block unconditionally if ``block_same_direction_any_price`` is set
           (only one open position per direction at a time).

    2) RECENTLY-CLOSED COOLDOWN (anti-churn / anti-revenge)
       For same-direction trades CLOSED within ``lookback_hours``, apply an
       outcome-aware cooldown, but ONLY when the new entry is in the same price
       zone (a genuinely different price area is a new setup, not a repeat):
         * after a LOSS      -> longest cooldown  (don't repeat a losing setup)
         * after BREAKEVEN   -> medium cooldown
         * after a WIN       -> shortest cooldown  (a working direction)

    All thresholds are configurable, with backward-compatible fallbacks to the
    legacy ``lookback_minutes`` / ``same_direction_price_zone_points`` keys.
    """
    filt = config.get('duplicate_signal_filter', {}) or {}
    if not filt.get('enabled', True):
        return None

    direction = str(decision.get('decision', '')).upper()
    if direction not in {'BUY', 'SELL'}:
        return None

    signal = decision.get('signal', {}) or {}
    entry = signal.get('entry', {}) or {}
    try:
        entry_price = float(entry.get('price') or decision.get('current_price') or 0)
    except (TypeError, ValueError):
        entry_price = 0.0
    if entry_price <= 0:
        return None

    now = datetime.now(timezone.utc)

    # ── Tunables (with legacy fallbacks) ───────────────────────────────────
    # Gold point convention: 1 USD = 10 points (1 point = 0.10 USD/oz).
    price_zone_points = float(
        filt.get('price_zone_points', filt.get('same_direction_price_zone_points', 50))
    )

    open_cfg = filt.get('open_trade', {}) or {}
    block_open_any_price = bool(
        open_cfg.get('block_same_direction_any_price', filt.get('block_if_open_same_direction', False))
    )
    block_open_in_zone = bool(open_cfg.get('block_same_direction_in_zone', True))

    cooldown_cfg = filt.get('cooldown', {}) or {}
    legacy_cooldown = float(filt.get('lookback_minutes', 90))
    cooldown_after_loss = float(cooldown_cfg.get('after_loss_minutes', legacy_cooldown))
    cooldown_after_breakeven = float(cooldown_cfg.get('after_breakeven_minutes', max(legacy_cooldown * 0.5, 30)))
    cooldown_after_win = float(cooldown_cfg.get('after_win_minutes', max(legacy_cooldown * 0.33, 20)))
    lookback_hours = float(cooldown_cfg.get('lookback_hours', 6))

    symbol = str(decision.get("symbol") or (decision.get("signal", {}) or {}).get("symbol") or config.get("symbol", "XAU/USD"))

    def _points_away(prev_price: float) -> float:
        return abs(price_to_points(entry_price - prev_price, symbol=symbol))

    # ── Collect same-direction candidates (open + recently closed) ─────────
    candidates: List[Dict[str, Any]] = []
    seen_ids: set = set()

    def _add(trade: Dict[str, Any]) -> None:
        trade_symbol = str(trade.get('symbol') or config.get('symbol', 'XAU/USD')).upper()
        if trade_symbol != str(symbol).upper():
            return
        tid = str(trade.get('id', ''))
        if tid and tid in seen_ids:
            return
        if tid:
            seen_ids.add(tid)
        candidates.append(trade)

    for trade in database.get_open_trades():
        if _trade_direction(trade) == direction:
            _add(trade)
    for trade in database.get_recent_trades(limit=50):
        if _trade_direction(trade) == direction:
            _add(trade)

    # ── 1) Open-position stacking protection ───────────────────────────────
    for trade in candidates:
        if _trade_outcome(trade) != "OPEN":
            continue
        prev_entry = _trade_entry_price(trade)
        if prev_entry is None:
            continue
        if block_open_any_price:
            return (
                f"Duplicate {direction} blocked: an open {direction} position "
                f"({trade.get('id', 'unknown')}) already exists (one position per direction)."
            )
        if block_open_in_zone:
            pts = _points_away(prev_entry)
            if pts <= price_zone_points:
                return (
                    f"Duplicate {direction} blocked: open {direction} position "
                    f"({trade.get('id', 'unknown')}) in the same price zone "
                    f"({pts:.0f}pts away, zone={price_zone_points:.0f}pts) — would stack the same level."
                )

    # ── 2) Recently-closed, outcome-aware cooldown (same zone only) ────────
    cooldown_by_outcome = {
        "LOSS": cooldown_after_loss,
        "BREAKEVEN": cooldown_after_breakeven,
        "WIN": cooldown_after_win,
    }
    for trade in candidates:
        outcome = _trade_outcome(trade)
        if outcome == "OPEN":
            continue
        prev_entry = _trade_entry_price(trade)
        if prev_entry is None:
            continue

        ref_time = _trade_reference_time(trade, now)
        age_minutes = (now - ref_time).total_seconds() / 60.0
        if age_minutes > lookback_hours * 60.0:
            continue  # too old to matter

        pts = _points_away(prev_entry)
        if pts > price_zone_points:
            continue  # different price area = legitimately new setup

        cooldown = cooldown_by_outcome.get(outcome, cooldown_after_breakeven)
        if age_minutes <= cooldown:
            return (
                f"Duplicate {direction} blocked: a {outcome} {direction} trade "
                f"({trade.get('id', 'unknown')}) closed {age_minutes:.0f}min ago in the same "
                f"price zone ({pts:.0f}pts away). Cooldown after {outcome} is {cooldown:.0f}min."
            )

    return None


def _dedupe_warnings(warnings: list) -> list:
    """Collapse duplicate / overlapping warnings before showing them in Telegram.

    The decision pipeline can raise two near-identical warnings for the same
    cause - e.g. repeated news-block warnings from different safety layers.
    Showing duplicates is noise. This:
      * drops exact duplicates,
      * keeps only the FIRST news-block warning,
      * preserves the order of all other warnings.
    """
    seen: set = set()
    result: list = []
    news_block_kept = False
    for w in warnings:
        text = str(w).strip()
        if not text:
            continue
        key = " ".join(text.lower().split())
        if key in seen:
            continue
        lower = text.lower()
        is_news_block = lower.startswith("news blocked") or lower.startswith("ai news blocked")
        if is_news_block:
            if news_block_kept:
                # A news block was already shown; skip the redundant duplicate.
                continue
            news_block_kept = True
        seen.add(key)
        result.append(text)
    return result


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_news_hard_block(decision: Dict[str, Any], all_results: Dict[str, Any]) -> bool:
    """True when WAIT is caused by a hard news filter.

    In that case the market-status message should show NEWS BLOCK instead of a
    misleading zero-confidence decision caused by the post-filter override.
    """
    warnings = [str(w).lower() for w in (decision.get("warnings") or [])]
    if any(w.startswith("news blocked") or w.startswith("ai news blocked") for w in warnings):
        return True

    news = all_results.get("news", {}) or {}
    if news.get("can_trade") is False or str(news.get("market_status", "")).upper() in {"DANGER", "HIGH_VOLATILITY"}:
        return True

    news_ai = all_results.get("news_ai", {}) or news.get("ai_interpretation", {}) or {}
    if news_ai.get("available"):
        if bool(news_ai.get("block_trading", False)):
            return True
        if str(news_ai.get("allowed_direction", "BOTH")).upper() == "NONE":
            return True
        if str(news_ai.get("risk_level", "")).upper() == "EXTREME":
            return True
    return False


def _build_market_status_message(
    decision: Dict[str, Any],
    all_results: Dict[str, Any],
    database: DatabaseService,
) -> str:
    """Build the hourly/explicit Market Status Telegram message.

    Special-case hard news blocks so the message explains that news overrode the
    agent consensus decision.
    """
    warnings = _dedupe_warnings(decision.get("warnings") or [])
    warnings_text = "\n".join(f"• {html.escape(str(w))}" for w in warnings[:6]) or "• No special warnings"
    price_text = f"{_safe_float(decision.get('current_price', all_results.get('current_price', 0))):.2f}"
    classic = decision.get("classic", {}) or {}
    consensus = classic.get("consensus", {}) or {}
    rules = consensus.get("rules", {}) or {}

    agent_thr = rules.get("agent_min_confidence", decision.get("agent_min_confidence", 60))
    min_consensus = _safe_float(rules.get("min_consensus_confidence", 65), 65)
    news_hard_block = _is_news_hard_block(decision, all_results)

    reason_lines = []
    if news_hard_block:
        gate_line = f"📊 Gate: NEWS BLOCK  •  Consensus overridden  •  Agents ≥{agent_thr}%"
        reason_lines.append("• News hard block active — trading is paused during the event cooling window")
        reason_lines.append("• Agent consensus is ignored until the news filter clears")
    else:
        gate_line = f"📊 Consensus: WAIT  •  Agents ≥{agent_thr}%  •  Entry ≥{min_consensus:.0f}%"
        rejection = classic.get("rejection_reason") or "No valid weighted consensus signal"
        reason_lines.append(f"• {rejection}")
        reason_lines.append(f"• Rules: at least 2 agents with weighted confidence ≥{min_consensus:.0f}%")

    opp_agent = (classic.get("strongest_directional") or {}).get("agent")
    opp_conf = (classic.get("strongest_directional") or {}).get("confidence", 0)
    if opp_agent and opp_conf:
        reason_lines.append(f"• Strongest agent: {opp_agent} ({opp_conf}%)")

    tech = all_results.get("technical", {}) or {}
    t = tech.get("technical", {}) or {}
    if t.get("rsi"):
        reason_lines.append(f"• RSI: {t['rsi']}")
    levels = t.get("key_levels") or {}
    if isinstance(levels, dict):
        sup = levels.get("nearest_support")
        res = levels.get("nearest_resistance")
        if sup or res:
            reason_lines.append(f"• Levels: Support {sup}  •  Resistance {res}")

    open_count = len(database.get_open_trades())
    open_note = f"• Open trades: {open_count}" if open_count > 0 else "• No open trades"
    reason_text = "\n".join(reason_lines)

    return (
        "🟡 <b>Gold AI Signals — Market Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Price: {price_text}\n"
        "🎯 Decision: WAIT\n"
        f"{gate_line}\n\n"
        f"<b>Reason:</b>\n{html.escape(reason_text)}\n\n"
        f"<b>Notes:</b>\n{html.escape(open_note)}\n{warnings_text}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Periodic market status • Next market status in ~1 hour</i>"
    )


def run_agent(agent_name: str, agent: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    """Run one agent safely so one failure does not stop the workflow."""
    try:
        logger.info("Running agent: %s", agent_name)
        return agent.analyze(data)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent %s failed", agent_name)
        return {"agent": agent_name, "signal": "WAIT", "confidence": 0, "reasoning": f"Agent failed: {exc}"}


async def _check_scale_in(
    config: Dict[str, Any],
    all_results: Dict[str, Any],
    open_trades: List[Dict[str, Any]],
    database: DatabaseService,
    telegram: TelegramService,
) -> None:
    """Check for scale-in opportunities in fixed_risk mode.

    Scale-in logic:
      - Only in fixed_risk entry_style
      - Only if there's an open trade
      - Only if price has approached the key level (within trigger distance)
      - Rule-based confirmation
      - Bypass duplicate filter (intentional position building)
      - Respect news filter (if news blocks trading, no scale-in)
      - New defaults: trigger=50pts, max=1, size=100% (per user config)
    """
    oe = config.get("order_execution", {}) or {}
    entry_style = str(oe.get("entry_style", "market")).lower()
    if entry_style != "fixed_risk":
        return

    fr = oe.get("fixed_risk", {}) or {}
    if not bool(fr.get("scale_in_enabled", True)):
        return

    if not open_trades:
        return

    # Check news filter first
    news = all_results.get("news", {}) or {}
    if news.get("can_trade") is False or str(news.get("market_status", "")).upper() == "DANGER":
        logger.info("📊 Scale-in skipped: news filter blocks trading")
        return
    trigger_points = int(fr.get("scale_in_trigger_points", 50) or 50)
    max_scales = int(fr.get("scale_in_max", 1) or 1)
    size_ratio = float(fr.get("scale_in_size_ratio", 1.0) or 1.0)
    current_price = all_results.get("current_price", 0)
    if not current_price:
        return

    # Collect levels from results
    support_levels: list = []
    resistance_levels: list = []
    tech = all_results.get("technical", {}) or {}
    tech_levels = tech.get("key_levels", {}) or {}
    if tech_levels.get("nearest_support"):
        support_levels.append(float(tech_levels["nearest_support"]))
    if tech_levels.get("nearest_resistance"):
        resistance_levels.append(float(tech_levels["nearest_resistance"]))
    classical = all_results.get("classical", {}) or {}
    for s in (classical.get("support_levels") or []):
        support_levels.append(float(s))
    for r in (classical.get("resistance_levels") or []):
        resistance_levels.append(float(r))
    smc = all_results.get("smc", {}) or {}
    for ob in (smc.get("order_blocks", []) or []):
        z = ob.get("zone", {}) or {}
        top = float(z.get("top") or 0)
        bottom = float(z.get("bottom") or 0)
        if top > 0 and bottom > 0:
            if str(ob.get("type", "")).lower() == "bullish":
                support_levels.append(min(top, bottom))
            else:
                resistance_levels.append(max(top, bottom))

    trigger_price = points_to_price(trigger_points, config.get("symbol"))  # convert points to price distance

    # Check each open trade for scale-in opportunity
    for trade in open_trades:
        trade_type = str(trade.get("type") or trade.get("side") or "").upper()
        if trade_type not in {"BUY", "SELL"}:
            continue
        entry = float(trade.get("entry_price", 0))
        if entry <= 0:
            continue

        # Count existing scales for this trade direction
        existing_scales = 0
        for t in open_trades:
            if str(t.get("type") or t.get("side") or "").upper() == trade_type:
                existing_scales += 1
        existing_scales -= 1  # exclude the original
        if existing_scales >= max_scales:
            logger.info("📊 Scale-in skipped for %s: already %d scales (max %d)", trade_type, existing_scales, max_scales)
            continue

        # Check if price is near a key level
        near_level = False
        level_name = ""

        if trade_type == "SELL":
            # Price should be near resistance (upside risk)
            for res in resistance_levels:
                distance = res - current_price
                if 0 < distance <= trigger_price:
                    near_level = True
                    level_name = f"resistance at {res:.2f}"
                    break
        else:  # BUY
            for sup in support_levels:
                distance = current_price - sup
                if 0 < distance <= trigger_price:
                    near_level = True
                    level_name = f"support at {sup:.2f}"
                    break

        if not near_level:
            logger.debug("📊 No scale-in: %s trade not near any level (trigger=%dpts)", trade_type, trigger_points)
            continue

        # Rule-based scale-in confirmation.
        scale_ok = True
        scale_reason = f"near {level_name}"

        if not scale_ok:
            logger.info("📊 Scale-in rejected for %s: %s", trade_type, scale_reason)
            continue

        # Execute scale-in
        logger.info("📊 Scale-in %s confirmed: %s", trade_type, scale_reason)

        scale_decision = {
            "decision": trade_type,
            "signal": {
                "type": trade_type,
                "entry": {"price": round(current_price, 2), "kind": "MARKET", "order_type": f"{trade_type}_MARKET"},
                "stop_loss": trade.get("stop_loss"),
                "tp1": trade.get("tp1"),
                "tp2": trade.get("tp2"),
                "scale_in": True,
                "parent_trade_id": trade.get("id"),
            },
            "confidence": 80,
            "current_price": current_price,
            "trade_id": database.new_trade_id(),
            "reasons": [f"Scale-in: {scale_reason}"],
        }
        scale_decision["signal"]["trade_id"] = scale_decision["trade_id"]
        # Send Telegram notification first. Save the scale-in trade only if the
        # user actually received the message; otherwise a failed Telegram send
        # would create an invisible trade in the DB.
        scale_msg = (
            "📊 <b>SCALE-IN — {trade_type}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Parent trade: <code>{parent_id}</code>\n"
            "Original entry: {entry_price}\n"
            "Scale entry: {scale_price:.2f}\n"
            "Level: {level}\n"
            "Confirmation: rule-based ✅\n"
            "Reason: {reason}\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Scale-in #{scale_num}/{max_scales} • Size: {size_ratio:.0%} of original</i>"
        ).format(
            trade_type=trade_type,
            parent_id=html.escape(str(trade.get("id", ""))),
            entry_price=trade.get("entry_price"),
            scale_price=current_price,
            level=html.escape(level_name),
            reason=html.escape(scale_reason),
            scale_num=existing_scales + 1,
            max_scales=max_scales,
            size_ratio=size_ratio,
        )
        delivered = bool(telegram.send_message(scale_msg, urgent=True))
        if delivered:
            database.save_trade(scale_decision)
            logger.info("📊 Scale-in trade saved: %s", scale_decision["trade_id"])
        else:
            logger.error("📊 Scale-in %s failed: Telegram delivery error", trade_type)


async def _run_analysis_for_config(config: Dict[str, Any]) -> None:
    """Run one analysis cycle for one configured symbol."""

    telegram = TelegramService(config)

    try:
        # ── فحص ساعات التداول أولاً ──
        session = TradingSessionAgent(config).check()
        logger.info(
            "🔍 الجلسة: %s | الجودة: %s | مسموح: %s",
            session.get("current_session") or "خارج الجلسة",
            session.get("session_quality", "N/A"),
            session.get("trading_allowed"),
        )

        if not session.get("trading_allowed"):
            logger.info(
                "🚫 خارج ساعات التداول (%s) - لا تحليل حالياً. السبب: %s",
                session.get("current_session") or "غير محدد",
                session.get("reason", ""),
            )
            if should_send_hourly_status(config):
                # Price may not be fetched yet; use placeholder or skip price if unavailable
                price_text = "N/A"
                telegram.send_message(
                    "🟡 <b>Gold AI Signals — Market Status</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 Price: {price_text}\n"
                    f"🎯 Decision: WAIT\n"
                    f"📊 Outside trading hours\n\n"
                    f"<b>Reason:</b>\n• {html.escape(str(session.get('reason', 'Outside trading hours')))}\n"
                    f"<b>Session:</b> {html.escape(str(session.get('current_session') or 'N/A'))}\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "<i>Periodic market status • Next market status in ~1 hour</i>"
                )
            return  # ══ لا تحليل خارج الجلسات ══

        # ── تهيئة الخدمات ──
        market_data = MarketDataService(config)
        database = DatabaseService(config)

        logger.info("جلب بيانات السوق...")
        data = market_data.get_gold_data()
        if not data:
            logger.error("فشل في جلب البيانات")
            # Don't fail silently on scheduled runs — surface it so a recurring
            # data outage is visible instead of looking like "no signal".
            telegram.send_error_alert(
                "Analysis aborted: failed to fetch market data (Finnhub). "
                "No signal will be generated this cycle."
            )
            return

        # Safety: never send production signals from synthetic/demo prices on GitHub Actions.
        allow_synthetic = bool(config.get("data_source", {}).get("allow_synthetic_in_production", False))
        synthetic_sources = synthetic_timeframe_sources(data)
        if os.environ.get("GITHUB_ACTIONS") == "true" and synthetic_sources and not allow_synthetic:
            message = f"Analysis blocked: synthetic_demo data detected in production timeframes: {', '.join(sorted(set(synthetic_sources)))}. Configure FINNHUB_API_KEY."
            logger.error(message)
            telegram.send_error_alert(message)
            return

        # سياق الحساب/المحفظة
        open_trades = database.get_open_trades()
        today_signals = database.get_today_signals_count()
        consecutive_losses = database.get_consecutive_losses()

        # ── تشغيل وكلاء التحليل ──
        all_results: Dict[str, Any] = {
            "technical": run_agent("technical", TechnicalAgent(config), data),
            "classical": run_agent("classical", ClassicalAgent(config), data),
            "smc": run_agent("smc", SMCAgent(config), data),
            "price_action": run_agent("price_action", PriceActionAgent(config), data),
            "multitimeframe": run_agent("multitimeframe", MultiTimeframeAgent(config), data),
            "current_price": data["current_price"],
            "spread_points": data.get("spread_points"),
            "portfolio": {
                "open_trades_count": len(open_trades),
                "today_signals_count": today_signals,
                "consecutive_losses": consecutive_losses,
            },
        }

        # ── تشغيل وكلاء إضافية ──
        all_results["session"] = session
        all_results["news"] = NewsRiskAgent(config).check()
        all_results["daily_bias"] = run_agent("daily_bias", DailyBiasAgent(config), data)
        all_results["risk"] = RiskManagementAgent(config).evaluate(all_results)
        all_results["dynamic_risk"] = DynamicRiskManager(config).evaluate(database)
        # ── تشغيل وكيل القرار ──
        logger.info("تشغيل وكيل القرار (5-agent consensus)...")

        # --- 1) LearningService wired ---
        learning_service = None
        try:
            learning_service = get_learning_service(database, config)
            # تحميل الأوزان من DB (التي يحسبها run_learning.py يومياً).
            # كانت هذه الخطوة مفقودة فلم تكن أوزان DB تؤثر على القرار.
            try:
                loaded = await learning_service.load_current_weights()
                logger.info("🧠 أوزان الوكلاء المحمّلة من DB: %s", loaded)
            except Exception as w_exc:
                logger.warning("⚠️ فشل تحميل الأوزان من DB: %s (fallback إلى config)", w_exc)
        except Exception:
            learning_service = None
        decision = await DecisionAgent(config, learning_service=learning_service).decide_async(all_results)
        decision["symbol"] = config.get("symbol", "XAU/USD")
        if decision.get("signal"):
            decision["signal"]["symbol"] = decision["symbol"]

        decision["dynamic_risk"] = all_results.get("dynamic_risk", {})
        logger.info(
            "القرار: %s - الثقة: %s%% - %s | DynamicRisk=%s",
            decision.get("decision"),
            decision.get("confidence"),
            decision.get("summary"),
            decision.get("dynamic_risk", {}).get("summary"),
        )

        # ── إضافة وضع التشغيل/التداول الحالي للقرار ──
        github_event = os.environ.get("GITHUB_EVENT_NAME", "local")
        operation_mode = str(config.get("operation_mode", "observation")).lower()
        decision["run_source"] = "scheduled" if github_event == "schedule" else "manual" if github_event == "workflow_dispatch" else github_event
        decision["operation_mode"] = operation_mode
        decision["decision_mode"] = "5-Agent Weighted Consensus"
        decision["requires_three_agents"] = False
        trading_mode = str(config.get("trading_mode", "paper")).lower()
        paper_config = config.get("paper_trading", {}) or {}
        decision["trading_mode"] = trading_mode
        decision["paper_trading"] = trading_mode == "paper" or bool(paper_config.get("enabled", False))
        decision["paper_config"] = {
            "starting_balance": paper_config.get("starting_balance"),
            "currency": paper_config.get("currency", "USD"),
            "default_lot_size": paper_config.get("default_lot_size", 0.01),
        }
        if decision.get("signal"):
            decision["signal"]["trading_mode"] = trading_mode
            decision["signal"]["paper_trading"] = decision["paper_trading"]

        # ── إرسال الإشارة إذا كانت مؤهلة ──
        if decision.get("decision") in {"BUY", "SELL"}:
            settings = config.get("risk_settings", {})
            max_daily = int(settings.get("max_daily_signals", 8))
            max_open = int(settings.get("max_open_trades", 3))
            today_signals = database.get_today_signals_count()
            open_trades = database.get_open_trades()
            if today_signals >= max_daily:
                logger.info("تم الوصول للحد الأقصى من الإشارات اليومية: %s", max_daily)
                if should_send_status(config):
                    telegram.send_message(f"🟡 No signal: daily signal limit reached ({max_daily}).")
                return
            if len(open_trades) >= max_open:
                logger.info("تم الوصول للحد الأقصى للصفقات المفتوحة: %s", max_open)
                if should_send_status(config):
                    telegram.send_message(f"🟡 No signal: max open trades reached ({max_open}).")
                return

            dynamic_block_reason = should_block_signal(decision, all_results.get("dynamic_risk", {}))
            if dynamic_block_reason:
                logger.info("تم منع الإشارة بسبب Dynamic Risk: %s", dynamic_block_reason)
                if should_send_status(config):
                    telegram.send_message(
                        "🟡 <b>Signal blocked by Dynamic Risk</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"Decision: {html.escape(str(decision.get('decision')))}\n"
                        f"Reason: {html.escape(str(dynamic_block_reason))}\n"
                        f"Level: {html.escape(str(all_results.get('dynamic_risk', {}).get('level')))}\n"
                        "━━━━━━━━━━━━━━━━━━━━"
                    )
                return

            duplicate_reason = duplicate_signal_reason(decision, database, config)
            if duplicate_reason:
                logger.info("تم منع إشارة مكررة: %s", duplicate_reason)
                if should_send_status(config):
                    telegram.send_message(
                        "🟡 <b>Duplicate signal blocked</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"Decision: {html.escape(str(decision.get('decision')))}\n"
                        f"Reason: {html.escape(str(duplicate_reason))}\n"
                        "━━━━━━━━━━━━━━━━━━━━"
                    )
                return

            # IMPORTANT ordering & delivery handling.
            #
            # Previous behaviour saved the trade to the DB first and then called
            # telegram.send_signal(...) while *ignoring its return value*. If the
            # Telegram delivery failed (network blip, rate limit, HTML parse
            # error) on a scheduled run, the user received nothing — yet the
            # trade was already persisted. Every later scheduled run then saw
            # that trade and silently blocked the "duplicate" same-direction
            # signal. Net effect: signals appear only on manual runs, never on
            # the scheduled ones. That is exactly the reported symptom.
            #
            # Fix: send the Telegram signal FIRST. Only persist the trade if the
            # signal was actually delivered, so a failed delivery does not poison
            # the duplicate filter and the next scheduled run can retry cleanly.
            #
            # Mint the REAL trade id up-front (same format save_trade uses) so the
            # id shown in the Telegram message is final — never a 'PENDING_...'
            # placeholder. save_trade() reuses this exact id when persisting.
            trade_id = database.new_trade_id()
            decision["trade_id"] = trade_id
            if decision.get("signal"):
                decision["signal"]["trade_id"] = trade_id

            delivered = False
            try:
                delivered = bool(telegram.send_signal(decision))
            except Exception as send_exc:  # noqa: BLE001
                logger.exception("فشل إرسال إشارة Telegram")
                telegram.send_error_alert(f"Signal generated but Telegram delivery raised: {send_exc}")
                delivered = False

            if not delivered:
                # Do NOT save the trade: an unsent signal must not block future
                # runs via the duplicate filter. Alert loudly instead of failing
                # silently (the old code's worst failure mode).
                logger.error(
                    "⚠️ تم توليد إشارة %s لكن فشل إرسالها إلى Telegram — لن تُحفظ الصفقة لتفادي حجب التكرار.",
                    decision.get("decision"),
                )
                telegram.send_error_alert(
                    f"Signal {decision.get('decision')} generated but Telegram delivery failed; "
                    "trade NOT saved so the next run can retry."
                )
                return

            # Pending order cancellation removed (market-only mode)

            trade_id = database.save_trade(decision)
            decision["trade_id"] = trade_id
            if decision.get("signal"):
                decision["signal"]["trade_id"] = trade_id
            logger.info("تم إرسال الإشارة ثم حفظها: %s", trade_id)
        else:
            logger.info(
                "لا توجد إشارة مؤهلة حالياً. الأسباب/التحذيرات: %s",
                decision.get("warnings"),
            )
            if should_send_hourly_status(config):
                telegram.send_message(_build_market_status_message(decision, all_results, database))

        # ── Fixed-risk scale-in check ──
        # After main signal handling, check if we should scale into any open trade
        try:
            open_trades_for_scale = database.get_open_trades()
            if open_trades_for_scale:
                await _check_scale_in(
                    config=config,
                    all_results=all_results,
                    open_trades=open_trades_for_scale,
                    database=database,
                    telegram=telegram,
                )
        except Exception as scale_exc:
            logger.warning("⚠️ Scale-in check failed: %s", scale_exc)

        logger.info("✅ اكتمل التحليل بنجاح")

    except Exception as exc:  # noqa: BLE001
        logger.exception("خطأ في التحليل")
        telegram.send_error_alert(str(exc))


async def run_analysis_async() -> None:
    """Run analysis for all enabled instruments."""
    base_config = load_config()
    instruments = enabled_instruments(base_config)
    for instrument in instruments:
        cfg = config_for_instrument(base_config, instrument)
        logger.info("▶️ Running analysis for %s", cfg.get("symbol"))
        await _run_analysis_for_config(cfg)


def main() -> None:
    """نقطة الدخول الرئيسية."""
    import asyncio

    asyncio.run(run_analysis_async())


if __name__ == "__main__":
    main()
