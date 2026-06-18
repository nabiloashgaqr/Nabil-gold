"""HTML dashboard generator for Gold AI Signals.

Creates a self-contained HTML dashboard from recent trades and AI reviews.
No external assets/CDNs are used so the artifact can be opened offline.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


OPEN_STATUSES = {"OPEN", "PARTIAL", "TP1_HIT", "PENDING"}
WIN_STATUSES = {"TP2_HIT"}
LOSS_STATUSES = {"SL_HIT"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _status_badge(status: str) -> str:
    status = str(status or "UNKNOWN").upper()
    if status in {"OPEN", "PARTIAL", "TP1_HIT"}:
        cls = "open"
    elif status in WIN_STATUSES:
        cls = "win"
    elif status in LOSS_STATUSES:
        cls = "loss"
    else:
        cls = "neutral"
    return f'<span class="badge {cls}">{html.escape(status)}</span>'


def _trade_type_badge(trade_type: str) -> str:
    trade_type = str(trade_type or "").upper()
    cls = "buy" if trade_type == "BUY" else "sell" if trade_type == "SELL" else "neutral"
    return f'<span class="badge {cls}">{html.escape(trade_type or "N/A")}</span>'


def _pnl(trade: Dict[str, Any]) -> float:
    for key in ("final_pnl", "current_pnl", "current_pnl_points", "pnl"):
        if trade.get(key) is not None:
            return _f(trade.get(key))
    return 0.0


def _trade_type(trade: Dict[str, Any]) -> str:
    return str(trade.get("type") or trade.get("trade_type") or "").upper()


def summarize_trades(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(trades)
    open_trades = [t for t in trades if str(t.get("status", "")).upper() in OPEN_STATUSES]
    closed = [t for t in trades if str(t.get("status", "")).upper() not in OPEN_STATUSES]
    wins = [t for t in closed if str(t.get("status", "")).upper() in WIN_STATUSES or _pnl(t) > 0]
    losses = [t for t in closed if str(t.get("status", "")).upper() in LOSS_STATUSES or _pnl(t) < 0]
    net = sum(_pnl(t) for t in trades)
    gross_profit = sum(_pnl(t) for t in trades if _pnl(t) > 0)
    gross_loss = abs(sum(_pnl(t) for t in trades if _pnl(t) < 0))
    buy_trades = [t for t in trades if _trade_type(t) == "BUY"]
    sell_trades = [t for t in trades if _trade_type(t) == "SELL"]
    return {
        "total": total,
        "open": len(open_trades),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round((len(wins) / len(closed) * 100) if closed else 0, 2),
        "net_points": round(net, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else 0,
        "buy_count": len(buy_trades),
        "sell_count": len(sell_trades),
        "buy_net": round(sum(_pnl(t) for t in buy_trades), 2),
        "sell_net": round(sum(_pnl(t) for t in sell_trades), 2),
        "avg_confidence": round(sum(_f(t.get("confidence")) for t in trades) / total, 2) if total else 0,
    }


def _render_cards(summary: Dict[str, Any]) -> str:
    cards = [
        ("Total Trades", summary["total"], "📊"),
        ("Open", summary["open"], "🟡"),
        ("Win Rate", f"{summary['win_rate']}%", "✅"),
        ("Net Points", f"{summary['net_points']:+}", "💰"),
        ("Profit Factor", summary["profit_factor"], "⚖️"),
        ("Avg Confidence", f"{summary['avg_confidence']}%", "🎯"),
    ]
    return "\n".join(
        f"""
        <div class="card">
          <div class="card-icon">{icon}</div>
          <div class="card-title">{html.escape(str(title))}</div>
          <div class="card-value">{html.escape(str(value))}</div>
        </div>
        """
        for title, value, icon in cards
    )


def _render_trades_table(trades: List[Dict[str, Any]]) -> str:
    rows = []
    for trade in trades[:80]:
        status = str(trade.get("status", "UNKNOWN"))
        pnl = _pnl(trade)
        pnl_cls = "pos" if pnl > 0 else "neg" if pnl < 0 else "flat"
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(trade.get('id', '')))}</code></td>"
            f"<td>{_trade_type_badge(_trade_type(trade))}</td>"
            f"<td>{_status_badge(status)}</td>"
            f"<td>{html.escape(str(trade.get('entry_price', '')))}</td>"
            f"<td>{html.escape(str(trade.get('current_price', '')))}</td>"
            f"<td>{html.escape(str(trade.get('stop_loss', '')))}</td>"
            f"<td>{html.escape(str(trade.get('tp1', '')))}</td>"
            f"<td>{html.escape(str(trade.get('tp2', '')))}</td>"
            f"<td class='{pnl_cls}'>{pnl:+.2f}</td>"
            f"<td>{html.escape(str(trade.get('confidence', '')))}%</td>"
            f"<td>{html.escape(str(trade.get('trading_mode', 'paper')))}</td>"
            f"<td>{html.escape(str(trade.get('created_at', ''))[:19])}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='12' class='empty'>No trades found</td></tr>")
    return "\n".join(rows)


def _render_reviews(reviews: List[Dict[str, Any]]) -> str:
    if not reviews:
        return "<div class='empty-box'>No AI trade reviews yet.</div>"
    blocks = []
    for item in reviews[:12]:
        review = item.get("review", {}) or {}
        if isinstance(review, str):
            try:
                review = json.loads(review)
            except Exception:  # noqa: BLE001
                review = {"root_cause": review}
        suggestions = review.get("rule_suggestions") or []
        if isinstance(suggestions, list):
            suggestions_html = "".join(f"<li>{html.escape(str(x))}</li>" for x in suggestions[:3])
        else:
            suggestions_html = f"<li>{html.escape(str(suggestions))}</li>"
        blocks.append(
            f"""
            <div class="review">
              <div class="review-head">🔻 <code>{html.escape(str(item.get('trade_id', '')))}</code> · {html.escape(str(review.get('failure_category', 'OTHER')))}</div>
              <div class="review-cause">{html.escape(str(review.get('root_cause', 'No root cause available')))}</div>
              <ul>{suggestions_html}</ul>
            </div>
            """
        )
    return "\n".join(blocks)




def _render_memory_rules(rules: List[Dict[str, Any]]) -> str:
    if not rules:
        return "<div class='empty-box'>No active memory rules yet.</div>"
    blocks = []
    for rule in rules[:16]:
        blocks.append(
            f"""
            <div class="review">
              <div class="review-head">🧠 {html.escape(str(rule.get('category', 'MEMORY')))} · {html.escape(str(rule.get('applies_to', 'BOTH')))} · {html.escape(str(rule.get('confidence', 0)))}%</div>
              <div class="review-cause">{html.escape(str(rule.get('rule_text', '')))}</div>
              <div class="muted">Source trade: <code>{html.escape(str(rule.get('source_trade_id', 'N/A')))}</code></div>
            </div>
            """
        )
    return "\n".join(blocks)

def render_dashboard(trades: List[Dict[str, Any]], reviews: List[Dict[str, Any]] | None = None, memory_rules: List[Dict[str, Any]] | None = None) -> str:
    reviews = reviews or []
    memory_rules = memory_rules or []
    summary = summarize_trades(trades)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cards = _render_cards(summary)
    rows = _render_trades_table(sorted(trades, key=lambda t: str(t.get("created_at", "")), reverse=True))
    reviews_html = _render_reviews(reviews)
    memory_rules_html = _render_memory_rules(memory_rules)
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gold AI Signals Dashboard</title>
<style>
  :root {{ --bg:#0f172a; --panel:#111827; --card:#1f2937; --text:#f8fafc; --muted:#94a3b8; --gold:#facc15; --green:#22c55e; --red:#ef4444; --blue:#38bdf8; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: Arial, Tahoma, sans-serif; background:linear-gradient(135deg,#020617,#111827); color:var(--text); }}
  .wrap {{ max-width:1200px; margin:auto; padding:24px; }}
  .hero {{ padding:24px; background:rgba(250,204,21,.08); border:1px solid rgba(250,204,21,.25); border-radius:18px; margin-bottom:20px; }}
  h1 {{ margin:0 0 8px; color:var(--gold); }}
  .muted {{ color:var(--muted); }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:14px; margin:20px 0; }}
  .card {{ background:rgba(31,41,55,.9); border:1px solid rgba(148,163,184,.18); border-radius:16px; padding:18px; }}
  .card-icon {{ font-size:24px; }} .card-title {{ color:var(--muted); font-size:13px; margin-top:6px; }} .card-value {{ font-size:28px; font-weight:bold; margin-top:8px; }}
  .panel {{ background:rgba(17,24,39,.92); border:1px solid rgba(148,163,184,.18); border-radius:18px; padding:18px; margin-top:20px; overflow:hidden; }}
  .table-wrap {{ overflow:auto; }}
  table {{ width:100%; border-collapse:collapse; min-width:980px; }}
  th, td {{ border-bottom:1px solid rgba(148,163,184,.15); padding:10px; text-align:right; white-space:nowrap; }}
  th {{ color:var(--gold); font-size:13px; background:rgba(250,204,21,.05); }}
  code {{ direction:ltr; unicode-bidi:bidi-override; color:#c4b5fd; }}
  .badge {{ display:inline-block; padding:4px 8px; border-radius:999px; font-size:12px; font-weight:bold; }}
  .buy,.win {{ background:rgba(34,197,94,.16); color:#86efac; }} .sell,.loss {{ background:rgba(239,68,68,.16); color:#fca5a5; }} .open {{ background:rgba(56,189,248,.16); color:#7dd3fc; }} .neutral {{ background:rgba(148,163,184,.16); color:#cbd5e1; }}
  .pos {{ color:#86efac; font-weight:bold; }} .neg {{ color:#fca5a5; font-weight:bold; }} .flat {{ color:#cbd5e1; }}
  .review {{ background:rgba(31,41,55,.72); border:1px solid rgba(148,163,184,.15); border-radius:14px; padding:14px; margin:10px 0; }}
  .review-head {{ color:#fca5a5; font-weight:bold; }} .review-cause {{ margin:8px 0; color:#e2e8f0; }}
  .empty,.empty-box {{ color:var(--muted); text-align:center; padding:20px; }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>🏆 Gold AI Signals Dashboard</h1>
      <div class="muted">Generated at {html.escape(generated)} · Paper Trading / XAU/USD</div>
    </div>
    <div class="grid">{cards}</div>
    <div class="panel">
      <h2>📊 Latest Trades</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>ID</th><th>Type</th><th>Status</th><th>Entry</th><th>Current</th><th>SL</th><th>TP1</th><th>TP2</th><th>PnL</th><th>Conf</th><th>Mode</th><th>Created</th></tr></thead>
        <tbody>{rows}</tbody>
      </table></div>
    </div>
    <div class="panel">
      <h2>🧠 Active Memory Rules</h2>
      {memory_rules_html}
    </div>
    <div class="panel">
      <h2>🧠 AI Trade Reviews</h2>
      {reviews_html}
    </div>
  </div>
</body>
</html>"""


def save_dashboard(html_text: str, path: str | Path = "storage/dashboard.html") -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html_text, encoding="utf-8")
    return target


def format_dashboard_telegram(summary: Dict[str, Any]) -> str:
    return "\n".join(
        [
            "📊 <b>Dashboard Updated</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"الصفقات: {summary.get('total', 0)} | المفتوحة: {summary.get('open', 0)}",
            f"Win Rate: {summary.get('win_rate', 0)}%",
            f"Net Points: {summary.get('net_points', 0):+}",
            f"Profit Factor: {summary.get('profit_factor', 0)}",
            "تم إنشاء dashboard.html كـ Artifact في GitHub Actions.",
            "━━━━━━━━━━━━━━━━━━━━",
        ]
    )
