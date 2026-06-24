"""سكريبت التحليل الرئيسي.

يعمل كل 10 دقائق عبر GitHub Actions. يجلب بيانات الذهب، يشغل الوكلاء (مع AI)،
يطبق إدارة المخاطر والقرار، ثم يحفظ ويرسل الإشارة إذا كانت مؤهلة.
"""

from __future__ import annotations

import logging
import os
import sys
import html
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

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
from services.news_interpreter import NewsInterpreter
from services.telegram_bot import TelegramService
from services.ai_service import get_ai_service
from services.learning_service import get_learning_service
from utils.helpers import load_config, setup_logging

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


def should_send_status(config: Dict[str, Any]) -> bool:
    """Send status/no-signal Telegram messages on manual runs OR when enabled.

    Scheduled analysis runs every 10 minutes. We want blocked-signal reasons
    (duplicate / dynamic-risk / limits) to be visible on scheduled runs too, so
    the user can see *why* nothing arrived instead of guessing. This is the
    applied recommendation: drive visibility from config.
    """
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return True
    notif = config.get("notifications", {}) or {}
    # Either the general no-signal flag or the dedicated blocked-signal flag.
    return bool(notif.get("send_no_signal_updates", False)) or bool(notif.get("notify_on_blocked_signal", False))


def should_send_hourly_status(config: Dict[str, Any]) -> bool:
    """Send a clean market status update roughly once per hour (user preference).
    Runs every 10 min, so we only send when minute < 10.
    """
    from datetime import datetime, timezone
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return True
    notif = config.get("notifications", {}) or {}
    if not (bool(notif.get("send_no_signal_updates", False)) or bool(notif.get("hourly_status", False))):
        return False
    now = datetime.now(timezone.utc)
    interval = int(notif.get("hourly_status_interval_minutes", 60) or 60)
    # Map the interval onto the 10-minute schedule: only fire in the first slot
    # of each interval window so we send ~once per interval, never every run.
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

    def _points_away(prev_price: float) -> float:
        return abs(entry_price - prev_price) * 10.0

    # ── Collect same-direction candidates (open + recently closed) ─────────
    candidates: List[Dict[str, Any]] = []
    seen_ids: set = set()

    def _add(trade: Dict[str, Any]) -> None:
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
    cause - e.g. the rule-based NewsRiskAgent ("News blocked: ...") AND the Groq
    news interpreter ("AI News blocked trading: ...") both fire for the same
    upcoming event. Showing both is noise. This:
      * drops exact duplicates,
      * keeps only the FIRST news-block warning (rule-based, most concise) and
        drops the later AI-news one when both are present,
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


def run_agent(agent_name: str, agent: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    """Run one agent safely so one failure does not stop the workflow."""
    try:
        logger.info("Running agent: %s", agent_name)
        return agent.analyze(data)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent %s failed", agent_name)
        return {"agent": agent_name, "signal": "WAIT", "confidence": 0, "reasoning": f"Agent failed: {exc}"}


async def run_analysis_async() -> None:
    """الدالة الرئيسية للتحليل (async)"""

    config = load_config()
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
                    "<i>Periodic market status • Next check in ~10 min</i>"
                )
            return  # ══ لا تحليل خارج الجلسات ══

        # ── تهيئة الخدمات ──
        market_data = MarketDataService(config)
        database = DatabaseService(config)

        # ── تهيئة خدمة AI ──
        ai_service = None
        ai_config = config.get("ai_service", {})

        if ai_config.get("enabled", False):
            try:
                ai_service = get_ai_service(config)
                logger.info("🤖 AI Service مفعّل: %s", ai_config.get("provider", "unknown"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("⚠️ فشل تهيئة AI: %s", exc)
                if not bool(ai_config.get("fallback_to_classic", True)):
                    telegram.send_error_alert(f"Groq is required but AI initialization failed: {exc}")
                    return

        logger.info("جلب بيانات السوق...")
        data = market_data.get_gold_data()
        if not data:
            logger.error("فشل في جلب البيانات")
            # Don't fail silently on scheduled runs — surface it so a recurring
            # data outage is visible instead of looking like "no signal".
            telegram.send_error_alert(
                "Analysis aborted: failed to fetch market data (Twelve Data). "
                "No signal will be generated this cycle."
            )
            return

        # Safety: never send production signals from synthetic/demo prices on GitHub Actions.
        allow_synthetic = bool(config.get("data_source", {}).get("allow_synthetic_in_production", False))
        synthetic_sources = synthetic_timeframe_sources(data)
        if os.environ.get("GITHUB_ACTIONS") == "true" and synthetic_sources and not allow_synthetic:
            message = f"Analysis blocked: synthetic_demo data detected in production timeframes: {', '.join(sorted(set(synthetic_sources)))}. Configure TWELVE_DATA_API_KEY."
            logger.error(message)
            telegram.send_error_alert(message)
            return

        # سياق الحساب/المحفظة
        open_trades = database.get_open_trades()
        today_signals = database.get_today_signals_count()
        consecutive_losses = database.get_consecutive_losses()

        # ── تشغيل وكلاء التحليل ──
        all_results: Dict[str, Any] = {
            "technical": run_agent("technical", TechnicalAgent(config, ai_service), data),
            "classical": run_agent("classical", ClassicalAgent(config, ai_service), data),
            "smc": run_agent("smc", SMCAgent(config, ai_service), data),
            "price_action": run_agent("price_action", PriceActionAgent(config, ai_service), data),
            "multitimeframe": run_agent("multitimeframe", MultiTimeframeAgent(config, ai_service), data),
            "current_price": data["current_price"],
            "spread_points": data.get("spread_points"),
            "portfolio": {
                "open_trades_count": len(open_trades),
                "today_signals_count": today_signals,
                "consecutive_losses": consecutive_losses,
            },
        }

        # ── تشغيل وكلاء إضافية (بدون AI) ──
        all_results["session"] = session
        all_results["news"] = NewsRiskAgent(config).check()
        all_results["daily_bias"] = run_agent("daily_bias", DailyBiasAgent(config), data)
        if config.get("ai_news_interpretation", {}).get("enabled", True):
            all_results["news_ai"] = await NewsInterpreter(config).interpret(
                all_results["news"],
                {
                    "current_price": all_results.get("current_price"),
                    "daily_bias": all_results.get("daily_bias"),
                    "technical_summary": all_results.get("technical", {}).get("summary"),
                },
            )
            all_results["news"]["ai_interpretation"] = all_results["news_ai"]
        all_results["risk"] = RiskManagementAgent(config).evaluate(all_results)
        all_results["dynamic_risk"] = DynamicRiskManager(config).evaluate(database)
        all_results["memory_rules"] = database.get_active_memory_rules(
            limit=int(config.get("ai_memory_rules", {}).get("max_active_rules_in_prompt", 8))
        )
        logger.info("🧠 قواعد الذاكرة النشطة المحملة: %s", len(all_results["memory_rules"]))

        # ── تشغيل وكيل القرار (مع AI) ──
        logger.info("تشغيل وكيل القرار (AI-enabled)...")

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
        decision = await DecisionAgent(config, ai_service, learning_service).decide_async(all_results)

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
        groq_obs_cfg = config.get("groq_observation_mode", {}) or {}
        if operation_mode == "observation" and groq_obs_cfg.get("enabled", False) and groq_obs_cfg.get("allow_single_agent_context", False):
            decision["decision_mode"] = "One-Agent + Groq"
        else:
            decision["decision_mode"] = "Groq Observation" if operation_mode == "observation" and groq_obs_cfg.get("enabled", False) else "Production Strict"
        decision["requires_three_agents"] = bool((config.get("operation_modes", {}).get(operation_mode, {}) or {}).get("requires_three_agents", operation_mode != "observation"))
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

            # Replace any stale not-yet-filled pending order with this fresh
            # signal (user preference: a new signal supersedes the old pending one).
            try:
                cancelled = database.cancel_pending_orders("Replaced by a newer signal")
                if cancelled:
                    logger.info("🚫 أُلغيت %s أوامر معلّقة قديمة قبل حفظ الإشارة الجديدة", cancelled)
            except Exception as cexc:  # noqa: BLE001
                logger.warning("Could not cancel old pending orders: %s", cexc)

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
                warnings = _dedupe_warnings(decision.get("warnings") or [])
                warnings_text = "\n".join(f"• {html.escape(str(w))}" for w in warnings[:6]) or "• No special warnings"
                # Ensure price exactly 2 decimal places
                price_text = f"{float(decision.get('current_price', all_results.get('current_price', 0))):.2f}"
                ai = decision.get("ai", {}) or {}
                classic = decision.get("classic", {}) or {}

                agent_thr = decision.get("agent_min_confidence", 60)
                groq_thr = decision.get("groq_min_confidence", 51)
                groq_c = ai.get('confidence', decision.get('confidence', 0))
                groq_reason = ai.get("reasoning") or ai.get("opposite_risk") or ai.get("risk_notes") or ""

                reason_lines = []
                if groq_c < groq_thr:
                    reason_lines.append(f"• Groq returned {groq_c:.0f}% — below {groq_thr}% threshold")
                else:
                    reason_lines.append(f"• Groq accepted direction at {groq_c:.0f}%")

                if groq_reason:
                    reason_lines.append(f"• {groq_reason[:150]}")

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

                reason_text = "\n".join(reason_lines)

                telegram.send_message(
                    "🟡 <b>Gold AI Signals — Market Status</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 Price: {price_text}\n"
                    f"🎯 Decision: WAIT\n"
                    f"📊 Groq: {groq_c:.0f}%  •  Agents ≥{agent_thr}%  •  Groq ≥{groq_thr}%\n\n"
                    f"<b>Reason:</b>\n{html.escape(reason_text)}\n\n"
                    f"<b>Notes:</b>\n{warnings_text}\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "<i>Periodic market status • Next check in ~10 min</i>"
                )

        logger.info("✅ اكتمل التحليل بنجاح")

    except Exception as exc:  # noqa: BLE001
        logger.exception("خطأ في التحليل")
        telegram.send_error_alert(str(exc))


def main() -> None:
    """نقطة الدخول الرئيسية."""
    import asyncio

    asyncio.run(run_analysis_async())


if __name__ == "__main__":
    main()
