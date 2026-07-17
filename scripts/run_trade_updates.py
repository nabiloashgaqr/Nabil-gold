"""سكريبت تحديث الصفقات المفتوحة.

يعمل كل 5 دقائق عبر GitHub Actions. يجلب السعر الحالي، يمرر الصفقات المفتوحة
إلى OpenTradesManager، ثم يتم تحديث Supabase وإرسال تحديثات Telegram.
أحداث نقل الستوب / trailing / TP / SL تُرسل فوراً عند تشغيل هذا السكريبت ولا
تنتظر رسالة الـ heartbeat/الساعة.
✏️ يتحقق من ساعات التداول ويخرج إذا كان خارج الجلسات إلا إذا كان التحديث خارج
الساعات مفعلاً في config.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.news_risk_agent import NewsRiskAgent
from agents.open_trades_manager import OpenTradesManager
from agents.trading_session_agent import TradingSessionAgent
from services.database import DatabaseService
from services.market_data import MarketDataService
from services.telegram_bot import TelegramService
from utils.helpers import calculate_pips, load_config, setup_logging
from utils.instruments import config_for_instrument, normalize_symbol

setup_logging()
logger = logging.getLogger(__name__)


def _short_id(trade_id: str) -> str:
    """Compact, readable trade id: keep the date + last hex chunk.

    TRADE_20260623_120035_726925_7cf3f415 -> #7cf3f415
    Falls back to the last 8 chars if the format differs.
    """
    s = str(trade_id or "")
    if not s:
        return "#?"
    parts = s.split("_")
    last = parts[-1]
    if len(parts) > 1 and len(last) >= 4:
        return f"#{last}"
    # Single chunk (or too-short tail): use the last 8 characters.
    return f"#{s[-8:]}"


def _build_status_message(open_trades, evaluations, current_price: float) -> str:
    """Build a SHORT hourly status with live trades separated from pending orders."""
    import html as _html

    now_txt = datetime.now(timezone.utc).strftime("%H:%M UTC")
    if not open_trades:
        return (
            "🔄 <b>Trade Update</b> · " + now_txt + "\n"
            f"Price {current_price:.2f}\n"
            "📭 No open trades."
        )

    by_id = {str(ev.get("trade_id")): ev for ev in (evaluations or [])}
    live_statuses = {"OPEN", "PARTIAL", "TP1_HIT"}
    pending_statuses = {"PENDING"}
    live_trades = [t for t in open_trades if str(t.get("status") or "OPEN").upper() in live_statuses]
    pending_trades = [t for t in open_trades if str(t.get("status") or "").upper() in pending_statuses]

    headline_parts = []
    if live_trades:
        headline_parts.append(f"{len(live_trades)} open")
    if pending_trades:
        headline_parts.append(f"{len(pending_trades)} pending")
    lines = [
        f"🔄 <b>Trade Update</b> · {now_txt}",
        f"Price {current_price:.2f} · {' / '.join(headline_parts) if headline_parts else '0 active'}",
        "───────────────",
    ]

    net_points = 0.0
    for t in live_trades[:20]:
        tid = str(t.get("id", ""))
        ev = by_id.get(tid, {})
        direction = str(t.get("type") or t.get("side") or "BUY").upper()
        pnl = float(ev.get("pnl_points", 0) or 0)
        net_points += pnl
        usd = pnl / 10.0
        sign = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "➖"
        prog = ev.get("progress_to_tp1")
        prog_txt = ""
        if prog is not None:
            try:
                prog_txt = f" · {float(prog) * 100:.0f}%➜TP1"
            except (TypeError, ValueError):
                prog_txt = ""
        status = str(ev.get("new_status") or t.get("status") or "OPEN")
        status_txt = "" if status == "OPEN" else f" [{_html.escape(status)}]"
        lines.append(
            f"{sign} {direction:<4} <code>{_short_id(tid)}</code>  "
            f"{pnl:+.0f} pts ({usd:+.1f}$){prog_txt}{status_txt}"
        )
    if len(live_trades) > 20:
        lines.append(f"… and {len(live_trades) - 20} more")
    if live_trades:
        net_usd = net_points / 10.0
        net_emoji = "🟢" if net_points > 0 else "🔴" if net_points < 0 else "➖"
        lines.append("───────────────")
        lines.append(f"{net_emoji} <b>Net:</b> {net_points:+.0f} pts ({net_usd:+.1f}$)")

    if pending_trades:
        if live_trades:
            lines.append("───────────────")
        lines.append(f"⏳ <b>Pending Orders ({len(pending_trades)})</b>")
        for t in pending_trades[:20]:
            tid = str(t.get("id", ""))
            ev = by_id.get(tid, {})
            direction = str(t.get("type") or t.get("side") or "BUY").upper()
            entry = float(t.get("entry_price") or 0)
            order_type = str(t.get("order_type") or t.get("order_kind") or "PENDING").upper()
            pts_to_fill = ev.get("pending_distance_points")
            if pts_to_fill is None:
                pts_to_fill = abs(calculate_pips(current_price, entry, trade_type=direction, symbol=str(t.get("symbol") or "XAU/USD")))
            hours_open = ev.get("hours_open")
            if hours_open is None:
                try:
                    opened = datetime.fromisoformat(str(t.get("entry_time") or t.get("created_at") or "").replace("Z", "+00:00"))
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                    hours_open = max(0.0, (datetime.now(timezone.utc) - opened.astimezone(timezone.utc)).total_seconds() / 3600.0)
                except Exception:
                    hours_open = None
            age_txt = f"waiting {float(hours_open):.1f}h" if hours_open is not None else "waiting"
            lines.append(f"🟡 {direction:<4} <code>{_short_id(tid)}</code>  @ {entry:.2f} [{_html.escape(order_type)}] · {float(pts_to_fill):.0f} pts to fill · {age_txt}")
        if len(pending_trades) > 20:
            lines.append(f"… and {len(pending_trades) - 20} more")
    return "\n".join(lines)


def main() -> None:
    """تحديث الصفقات المفتوحة."""
    logger.info("Starting trade updates: %s", datetime.now(timezone.utc).isoformat())
    config = load_config()

    # ── فحص ساعات التداول ──
    session = TradingSessionAgent(config).check()
    logger.info(
        "🔍 Session: %s | Quality: %s | Allowed: %s",
        session.get("current_session") or "خارج Session",
        session.get("session_quality", "N/A"),
        session.get("trading_allowed"),
    )

    update_outside_hours = bool(config.get("trade_management", {}).get("update_outside_trading_hours", False))
    force_update = os.environ.get("FORCE_TRADE_UPDATE", "false").lower() in {"1", "true", "yes"}
    if not session.get("trading_allowed") and not update_outside_hours and not force_update:
        logger.info(
            "🚫 Outside trade update hours (%s) - No update. Reason: %s",
            session.get("current_session") or "غير محدد",
            session.get("reason", ""),
        )
        return  # ══ لا تحديث خارج الجلسات إلا عند FORCE_TRADE_UPDATE ══
    if not session.get("trading_allowed") and (update_outside_hours or force_update):
        logger.info(
            "ℹ️ Outside normal update hours, but continuing due to update_outside_trading_hours or FORCE_TRADE_UPDATE"
        )

    try:
        telegram = TelegramService(config)
        database = DatabaseService(config)

        # Critical quota/cost optimization: do not fetch market data, initialize
        # trade management, or consume Twelve Data calls when there is no active
        # trade/pending order to manage. This still starts a tiny GitHub workflow
        # when triggered externally, but the workflow pre-check should normally
        # skip the heavy steps before Python dependencies are installed.
        open_trades = database.get_open_trades()
        logger.info("Active/pending trades count: %s", len(open_trades))
        if not open_trades:
            logger.info("لا توجد صفقات مفتوحة أو أوامر معلقة — skipping تحديث الصفقات بدون Telegram وبدون جلب سعر.")
            return

        # In the consolidated end-of-day digest, suppress this script's own
        # Telegram messages (trade events + confirmation). DB updates still run;
        # the daily report aggregates everything into one message. We achieve
        # this by passing telegram=None to the manager so it sends nothing.
        eod_quiet = os.environ.get("EOD_QUIET", "").lower() in {"1", "true", "yes"}
        telegram_for_events = None if eod_quiet else telegram

        # Group active trades by symbol and fetch each market price separately.
        # This is mandatory now that the bot supports Gold, FX pairs, and WTI.
        grouped: dict[str, list] = {}
        for trade in open_trades:
            symbol = normalize_symbol(trade.get("symbol") or config.get("symbol", "XAU/USD"))
            grouped.setdefault(symbol, []).append(trade)

        evaluations = []
        current_price = 0.0
        for symbol, symbol_trades in grouped.items():
            symbol_config = config_for_instrument(config, {"symbol": symbol})
            market_data = MarketDataService(symbol_config)
            manager = OpenTradesManager(symbol_config)

            # Use the SAME base timeframe as analysis (5m) to benefit from
            # the 60-second cache. Only need current price for trade management,
            # not full 15m OHLCV data.
            base_tf = symbol_config.get("data_source", {}).get("base_timeframe", "5m")
            price_payload = market_data.get_ohlcv(base_tf, outputsize=5)
            allow_synthetic = bool(symbol_config.get("data_source", {}).get("allow_synthetic_in_production", False))
            if os.environ.get("GITHUB_ACTIONS") == "true" and price_payload.get("source") == "synthetic_demo" and not allow_synthetic:
                # For trade management only, try a live XAU/USD spot quote before
                # giving up. This does not provide historical candles for
                # analysis, but it is safer than stopping SL/TP management and
                # safer than using GC=F futures as a proxy.
                quote_payload = market_data.get_spot_quote_payload()
                if quote_payload:
                    logger.warning("Using Swissquote spot quote fallback for %s trade updates", symbol)
                    price_payload = quote_payload
                else:
                    logger.error("Trade updates stopped for %s: all real data sources failed", symbol)
                    telegram.send_error_alert(
                        "Trade updates stopped: all real data sources failed "
                        "(Twelve Data quota/key and Swissquote spot quote unavailable)"
                    )
                    continue
            symbol_price = price_payload.get("current_price")
            if not symbol_price:
                logger.error("Failed to fetch price for symbol %s", symbol)
                continue
            current_price = float(symbol_price)
            # Check news hard-block for pending-order activation logic.
            try:
                news_cfg = {**symbol_config, "macro_context": database.get_macro_context()} if hasattr(database, 'get_macro_context') else symbol_config
                news_result = NewsRiskAgent(news_cfg).check()
            except Exception:
                news_result = {}
            news_blocked = bool(news_result.get("can_trade") is False or str(news_result.get("market_status", "")).upper() in {"DANGER", "HIGH_VOLATILITY"})
            # ── Global price sanity: reject obviously corrupt ticks ──
            # XAU/USD realistic range: 2500-5500. WTI: 30-150.
            # Anything outside this is a data provider glitch.
            _sane_min = 2500.0 if symbol.startswith("XAU") else 30.0
            _sane_max = 5500.0 if symbol.startswith("XAU") else 150.0
            if current_price < _sane_min or current_price > _sane_max:
                logger.error(
                    "PRICE SANITY FAILED (global): %s current_price=%.2f outside range [%.0f-%.0f]. Skipping trade updates this cycle.",
                    symbol, current_price, _sane_min, _sane_max,
                )
                if not eod_quiet:
                    telegram.send_error_alert(
                        f"Trade updates skipped for {symbol}: corrupted price {current_price:.2f} "
                        f"(expected {_sane_min:.0f}-{_sane_max:.0f}). Data provider glitch."
                    )
                continue
            latest_candle = (price_payload.get("data") or [{}])[-1] or {}
            try:
                candle_high = float(latest_candle.get("high") or current_price)
                candle_low = float(latest_candle.get("low") or current_price)
            except (TypeError, ValueError):
                candle_high = current_price
                candle_low = current_price
            if price_payload.get("source") == "swissquote_spot_quote_fallback" and any(str(t.get("status") or "").upper() == "PENDING" for t in symbol_trades):
                logger.warning(
                    "Pending touch detection degraded for %s: using Swissquote quote fallback without reliable intrabar OHLC. Pending orders will wait for a real candle source before activation.",
                    symbol,
                )
            evaluations.extend(manager.update_trades(
                open_trades=symbol_trades,
                current_price=current_price,
                candle_high=candle_high,
                candle_low=candle_low,
                database=database,
                telegram=telegram_for_events,
                now=datetime.now(timezone.utc),
                news_blocked=news_blocked,
                news_context=news_result,
                market_data_source=str(price_payload.get("source") or ""),
            ))
        total_events = 0
        for evaluation in evaluations:
            if evaluation.get("events"):
                total_events += len(evaluation.get("events", []))
                logger.info(
                    "Trade update %s: %s | %s -> %s | PnL=%s",
                    evaluation.get("trade_id"),
                    ",".join(evaluation.get("events", [])),
                    evaluation.get("old_status"),
                    evaluation.get("new_status"),
                    evaluation.get("pnl_points"),
                )

        # ── Hourly heartbeat / status message ───────────────────────────────
        # CRITICAL FIX: We deliberately suppress the "Trades Update" heartbeat
        # when there are **zero open trades** on scheduled runs.
        #
        # Reason: It was sending useless "📭 No open trades." messages that
        # collided in time with the much more useful "Market Status" from
        # the analysis script (which already explains WAIT + reasons + Daily Bias).
        #
        # New rule:
        # - Always send on manual runs (workflow_dispatch)
        # - On scheduled runs: ONLY send if there is at least 1 open trade
        manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" or force_update
        notif = config.get("notifications", {}) or {}
        heartbeat = bool(notif.get("heartbeat_on_trade_update", True))
        notify_updates = bool(notif.get("notify_on_trade_update", False))

        heartbeat_interval = int(notif.get("heartbeat_interval_minutes", 60) or 60)
        if manual:
            heartbeat_due = True
        elif heartbeat_interval <= 30:
            heartbeat_due = True
        else:
            heartbeat_due = datetime.now(timezone.utc).minute < 30

        has_open_trades = len(open_trades) > 0

        # Only send the optional "Trades Update" heartbeat when explicitly
        # enabled. It must NOT be forced by a manual run: the production rule is
        # "message only on real change" (SL moved / trailing moved / TP / SL / BE
        # / order fill). Those event messages are already sent above by
        # OpenTradesManager. With heartbeat_on_trade_update=false and
        # notify_on_trade_update=false, no Telegram message is sent when there is
        # no trade-state change.
        should_send = (
            (notify_updates or (heartbeat and has_open_trades))
            and heartbeat_due
            and total_events == 0
            and not eod_quiet
        )

        if should_send:
            telegram.send_message(
                _build_status_message(open_trades, evaluations, float(current_price))
            )

        logger.info("Trade update completed (events=%s, open=%s)", total_events, len(open_trades))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Update error: %s", exc)


if __name__ == "__main__":
    main()
