"""Interactive Telegram command handler for Gold AI Signals.

Polling-based (works on GitHub Actions, no server). A scheduled workflow calls
``poll_and_handle`` which fetches new updates, runs each command, and replies.

Supported commands:
  /start, /help        -> intro + command list
  /status              -> current price + latest signal snapshot
  /open                -> currently open / pending trades (+ floating PnL)
  /today               -> today's performance (wins/losses/net)
  /stats               -> overall performance (recent window)
  /price               -> current gold price only
  /rules               -> latest AI memory rules (learning)

Update-offset is persisted in storage/telegram_offset.json so the same update is
never processed twice across runs.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

OPEN_LIVE = {"OPEN", "PARTIAL", "TP1_HIT"}
PENDING = "PENDING"
_OFFSET_FILE = Path(__file__).resolve().parents[1] / "storage" / "telegram_offset.json"


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _pnl_points(t: Dict[str, Any]) -> float:
    for k in ("final_pnl", "current_pnl", "current_pnl_points"):
        if t.get(k) is not None:
            return _f(t.get(k))
    typ = str(t.get("type") or t.get("trade_type") or "BUY").upper()
    entry = _f(t.get("entry_price"))
    px = _f(t.get("close_price") or t.get("current_price") or entry)
    return ((px - entry) if typ == "BUY" else (entry - px)) * 10.0


def _ttype(t: Dict[str, Any]) -> str:
    return str(t.get("type") or t.get("trade_type") or "").upper()


def _short_id(tid: str) -> str:
    s = str(tid or "")
    parts = s.split("_")
    return "#" + (parts[-1] if parts and len(parts[-1]) >= 4 else s[-6:] or "?")


# ── offset persistence ─────────────────────────────────────────────────────
def _load_offset() -> int:
    try:
        return int(json.loads(_OFFSET_FILE.read_text()).get("offset", 0))
    except Exception:  # noqa: BLE001
        return 0


def _save_offset(offset: int) -> None:
    try:
        _OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
        _OFFSET_FILE.write_text(json.dumps({"offset": offset}), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not persist telegram offset: %s", exc)


# ── command builders ───────────────────────────────────────────────────────
def cmd_help() -> str:
    return (
        "🤖 <b>Gold AI Signals — Bot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "الأوامر المتاحة / Available commands:\n\n"
        "📊 /status — السعر + آخر إشارة\n"
        "🔄 /open — الصفقات المفتوحة/المعلّقة\n"
        "📅 /today — أداء اليوم\n"
        "📈 /stats — الأداء العام\n"
        "💰 /price — سعر الذهب الحالي\n"
        "🧠 /rules — قواعد التعلّم الأخيرة\n"
        "❓ /help — هذه القائمة\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Educational / paper trading — not financial advice.</i>"
    )


def _current_price(config: Dict[str, Any]) -> float | None:
    try:
        from services.market_data import MarketDataService
        md = MarketDataService(config)
        return md.get_current_price()
    except Exception as exc:  # noqa: BLE001
        logger.warning("price fetch failed: %s", exc)
        return None


def cmd_price(config: Dict[str, Any]) -> str:
    p = _current_price(config)
    if p is None:
        return "💰 السعر غير متاح حالياً / Price unavailable."
    return f"💰 <b>XAU/USD:</b> {p:,.2f}\n<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>"


def cmd_status(database, config: Dict[str, Any]) -> str:
    p = _current_price(config)
    price_line = f"💰 Price: <b>{p:,.2f}</b>" if p is not None else "💰 Price: —"
    latest = None
    try:
        recent = database.get_recent_trades(limit=1) or []
        latest = recent[0] if recent else None
    except Exception:  # noqa: BLE001
        latest = None
    lines = ["📊 <b>Status</b>", "━━━━━━━━━━━━━━━━━━━━", price_line]
    if latest:
        typ = _ttype(latest)
        emoji = "🟢" if typ == "BUY" else "🔴" if typ == "SELL" else "⚪"
        st = str(latest.get("status", "")).upper()
        lines.append(
            f"\n🎯 Latest signal: {emoji} <b>{typ or '—'}</b> @ {_f(latest.get('entry_price')):,.2f}"
            f"\n   Status: {html.escape(st)} · Conf {int(_f(latest.get('confidence')))}%"
        )
    else:
        lines.append("\n🎯 No signals yet.")
    lines.append("\n<i>Use /open and /today for more.</i>")
    return "\n".join(lines)


def cmd_open(database) -> str:
    try:
        trades = database.get_open_trades() or []
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ Could not fetch open trades: {html.escape(str(exc))}"
    live = [t for t in trades if str(t.get("status", "")).upper() in OPEN_LIVE]
    pend = [t for t in trades if str(t.get("status", "")).upper() == PENDING]
    if not live and not pend:
        return "🔄 <b>Open Trades</b>\n━━━━━━━━━━━━━━━━━━━━\nNo open or pending trades."
    lines = ["🔄 <b>Open Trades</b>", "━━━━━━━━━━━━━━━━━━━━"]
    net = 0.0
    for t in live[:15]:
        p = _pnl_points(t)
        net += p
        sign = "🟢" if p > 0 else "🔴" if p < 0 else "➖"
        lines.append(
            f"{sign} {_ttype(t)} <code>{_short_id(t.get('id'))}</code> "
            f"@ {_f(t.get('entry_price')):,.2f} · {p:+.0f} pts ({p/10:+.1f}$) · {html.escape(str(t.get('status','')).upper())}"
        )
    if pend:
        lines.append("\n⏳ <b>Pending</b>")
        for t in pend[:10]:
            lines.append(
                f"• {_ttype(t)} <code>{_short_id(t.get('id'))}</code> "
                f"@ {_f(t.get('entry_price')):,.2f} (waiting for touch)"
            )
    if live:
        lines.append(f"\n📊 Floating Net: <b>{net:+.0f} pts ({net/10:+.1f}$)</b>")
    return "\n".join(lines)


def cmd_today(database, config: Dict[str, Any]) -> str:
    try:
        from agents.daily_report_agent import DailyReportAgent
        trades = database.get_today_trades() or []
        stats = DailyReportAgent(config).generate(trades)["stats"]
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ Could not build today's report: {html.escape(str(exc))}"
    net = _f(stats.get("net_points"))
    return (
        "📅 <b>Today's Performance</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• Trades: {stats.get('total', 0)} (✅ {stats.get('wins', 0)} · ❌ {stats.get('losses', 0)} · 🔄 {stats.get('open', 0)})\n"
        f"• Win rate: {stats.get('win_rate', 0)}%\n"
        f"• Net: {net:+.0f} pts ({net/10:+.1f}$)\n"
        f"• Best {_f(stats.get('best_trade')):+.0f} · Worst {_f(stats.get('worst_trade')):+.0f} · PF {stats.get('profit_factor', 0)}"
    )


def cmd_stats(database, config: Dict[str, Any]) -> str:
    try:
        from agents.daily_report_agent import DailyReportAgent
        trades = database.get_recent_trades(limit=100) or []
        stats = DailyReportAgent(config).generate(trades)["stats"]
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ Could not build stats: {html.escape(str(exc))}"
    net = _f(stats.get("net_points"))
    by = stats.get("by_direction", {}) or {}
    return (
        "📈 <b>Overall Performance</b> <i>(recent 100)</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• Trades: {stats.get('total', 0)} · Win rate: {stats.get('win_rate', 0)}%\n"
        f"• Net: {net:+.0f} pts ({net/10:+.1f}$) · PF {stats.get('profit_factor', 0)}\n"
        f"• Avg win {_f(stats.get('avg_win')):+.0f} · Avg loss -{_f(stats.get('avg_loss')):.0f}\n"
        f"• BUY net {_f(by.get('BUY', {}).get('net')):+.0f} · SELL net {_f(by.get('SELL', {}).get('net')):+.0f}"
    )


def cmd_rules(database) -> str:
    try:
        rules = database.get_active_memory_rules(limit=6) or []
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ Could not fetch rules: {html.escape(str(exc))}"
    if not rules:
        return "🧠 <b>Memory Rules</b>\n━━━━━━━━━━━━━━━━━━━━\nNo active rules yet."
    lines = ["🧠 <b>Active Memory Rules</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for r in rules:
        lines.append(
            f"• [{html.escape(str(r.get('category', 'MEMORY')))}/{html.escape(str(r.get('applies_to', 'BOTH')))}] "
            f"{html.escape(str(r.get('rule_text', ''))[:180])}"
        )
    return "\n".join(lines)


def handle_command(text: str, database, config: Dict[str, Any]) -> str | None:
    """Map a command string to a reply. Returns None for non-commands."""
    cmd = text.strip().split()[0].lower()
    # Strip @botname suffix (groups send /status@MyBot).
    cmd = cmd.split("@")[0]
    if cmd in {"/start", "/help"}:
        return cmd_help()
    if cmd == "/status":
        return cmd_status(database, config)
    if cmd == "/open":
        return cmd_open(database)
    if cmd == "/today":
        return cmd_today(database, config)
    if cmd == "/stats":
        return cmd_stats(database, config)
    if cmd == "/price":
        return cmd_price(config)
    if cmd == "/rules":
        return cmd_rules(database)
    return None


def poll_and_handle(telegram, database, config: Dict[str, Any], max_updates: int = 40) -> int:
    """Fetch new updates and reply to commands. Returns count handled."""
    offset = _load_offset()
    updates = telegram.get_updates(offset=offset or None, timeout=0)
    handled = 0
    last_id = offset
    for upd in updates[:max_updates]:
        last_id = max(last_id, int(upd.get("update_id", 0)) + 1)
        msg = upd.get("message") or upd.get("channel_post") or {}
        text = str(msg.get("text", "") or "")
        chat = (msg.get("chat") or {}).get("id")
        mid = msg.get("message_id")
        if not text.startswith("/") or chat is None:
            continue
        try:
            reply = handle_command(text, database, config)
        except Exception as exc:  # noqa: BLE001
            logger.exception("command failed")
            reply = f"⚠️ Error: {html.escape(str(exc))}"
        if reply:
            telegram.reply(chat, reply, reply_to=mid)
            handled += 1
    if last_id and last_id != offset:
        _save_offset(last_id)
    return handled
