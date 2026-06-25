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

from agents.open_trades_manager import OpenTradesManager
from agents.trading_session_agent import TradingSessionAgent
from services.database import DatabaseService
from services.market_data import MarketDataService
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging

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
    """Build a SHORT hourly status: one line per trade with P/L, plus a total.

    Example:
        🔄 Trades Update · 14:00 UTC
        Price 4136.12 · 2 open
        ───────────────
        🔴 SELL #7cf3f415  -113 pts (-11.3$) · 0%➜TP1
        🟢 BUY  #a1b2c3d4  +48 pts (+4.8$) · 36%➜TP1
        ───────────────
        Net: -65 pts (-6.5$)
    """
    import html as _html

    now_txt = datetime.now(timezone.utc).strftime("%H:%M UTC")
    if not open_trades:
        return (
            "🔄 <b>Trades Update</b> · " + now_txt + "\n"
            f"Price {current_price:.2f}\n"
            "📭 No open trades."
        )

    # Index evaluations by trade id for quick lookup of pnl/progress.
    by_id = {str(ev.get("trade_id")): ev for ev in (evaluations or [])}

    lines = [
        f"🔄 <b>Trades Update</b> · {now_txt}",
        f"Price {current_price:.2f} · {len(open_trades)} open",
        "───────────────",
    ]
    net_points = 0.0
    for t in open_trades[:20]:
        tid = str(t.get("id", ""))
        ev = by_id.get(tid, {})
        direction = str(t.get("type") or t.get("side") or "BUY").upper()
        pnl = float(ev.get("pnl_points", 0) or 0)
        net_points += pnl
        usd = pnl / 10.0  # 10 points = 1 USD on gold
        sign = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "➖"
        # Progress toward TP1 (0..100). Show only when meaningful.
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
    if len(open_trades) > 20:
        lines.append(f"… and {len(open_trades) - 20} more")

    net_usd = net_points / 10.0
    net_emoji = "🟢" if net_points > 0 else "🔴" if net_points < 0 else "➖"
    lines.append("───────────────")
    lines.append(f"{net_emoji} <b>Net:</b> {net_points:+.0f} pts ({net_usd:+.1f}$)")
    return "\n".join(lines)


def main() -> None:
    """تحديث الصفقات المفتوحة."""
    logger.info("بدء تحديث الصفقات: %s", datetime.now(timezone.utc).isoformat())
    config = load_config()

    # ── فحص ساعات التداول ──
    session = TradingSessionAgent(config).check()
    logger.info(
        "🔍 الجلسة: %s | الجودة: %s | مسموح: %s",
        session.get("current_session") or "خارج الجلسة",
        session.get("session_quality", "N/A"),
        session.get("trading_allowed"),
    )

    update_outside_hours = bool(config.get("trade_management", {}).get("update_outside_trading_hours", False))
    force_update = os.environ.get("FORCE_TRADE_UPDATE", "false").lower() in {"1", "true", "yes"}
    if not session.get("trading_allowed") and not update_outside_hours and not force_update:
        logger.info(
            "🚫 خارج ساعات تحديث الصفقات (%s) - لا تحديث حالياً. السبب: %s",
            session.get("current_session") or "غير محدد",
            session.get("reason", ""),
        )
        return  # ══ لا تحديث خارج الجلسات إلا عند FORCE_TRADE_UPDATE ══
    if not session.get("trading_allowed") and (update_outside_hours or force_update):
        logger.info(
            "ℹ️ خارج ساعات التحديث العادية، لكن التحديث مستمر بسبب update_outside_trading_hours أو FORCE_TRADE_UPDATE"
        )

    try:
        market_data = MarketDataService(config)
        telegram = TelegramService(config)
        database = DatabaseService(config)
        manager = OpenTradesManager(config)

        # Use an OHLC payload instead of blind quote fallback so production never
        # manages/closes trades using synthetic_demo prices if the market API fails.
        price_payload = market_data.get_ohlcv(config.get("primary_timeframe", "15m"), outputsize=60)
        allow_synthetic = bool(config.get("data_source", {}).get("allow_synthetic_in_production", False))
        if os.environ.get("GITHUB_ACTIONS") == "true" and price_payload.get("source") == "synthetic_demo" and not allow_synthetic:
            logger.error("تم إيقاف تحديث الصفقات: السعر من synthetic_demo. راجع TWELVE_DATA_API_KEY.")
            telegram.send_error_alert("Trade updates stopped: price is from synthetic_demo. Check TWELVE_DATA_API_KEY.")
            return
        current_price = price_payload.get("current_price")
        if not current_price:
            logger.error("فشل في جلب السعر")
            return

        open_trades = database.get_open_trades()
        logger.info("عدد الصفقات المفتوحة: %s", len(open_trades))

        # In the consolidated end-of-day digest, suppress this script's own
        # Telegram messages (trade events + confirmation). DB updates still run;
        # the daily report aggregates everything into one message. We achieve
        # this by passing telegram=None to the manager so it sends nothing.
        eod_quiet = os.environ.get("EOD_QUIET", "").lower() in {"1", "true", "yes"}
        telegram_for_events = None if eod_quiet else telegram

        evaluations = manager.update_trades(
            open_trades=open_trades,
            current_price=float(current_price),
            database=database,
            telegram=telegram_for_events,
            now=datetime.now(timezone.utc),
        )
        total_events = 0
        for evaluation in evaluations:
            if evaluation.get("events"):
                total_events += len(evaluation.get("events", []))
                logger.info(
                    "تحديث الصفقة %s: %s | %s -> %s | PnL=%s",
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

        logger.info("اكتمل تحديث الصفقات (events=%s, open=%s)", total_events, len(open_trades))
    except Exception as exc:  # noqa: BLE001
        logger.exception("خطأ في التحديث: %s", exc)


if __name__ == "__main__":
    main()
