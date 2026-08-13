"""Dashboard continuity (operator directive 2026-08-09).

The dashboard must read as ONE unbroken record — paper history + live
execution — as if no cut or migration ever happened. Therefore:
- rows from both books merge, deduped by id;
- NO paper/demo split marker anywhere (no 🧪, no "DEMO" in titles/cards).
These tests must fail if a split label or a lost history ever returns.
"""
from services.dashboard import (
    format_dashboard_telegram, merge_trade_rows, render_dashboard,
    summarize_trades)


def _row(tid, status="TP2_HIT", pnl=100.0):
    return {
        "id": tid, "symbol": "XAU/USD", "type": "BUY", "status": status,
        "entry_price": 4300.0, "current_price": 4310.0, "stop_loss": 4290.0,
        "tp1": 4310.0, "tp2": 4330.0, "pnl_points": pnl, "confidence": 70,
        "created_at": "2026-08-08T10:00:00Z",
        "closed_at": "2026-08-08T11:00:00Z", "close_reason": "TP2",
    }


def test_merge_keeps_both_books_and_dedupes():
    paper = [_row("P1"), _row("P2")]
    demo = [_row("D1"), _row("P2")]          # duplicate id across books
    merged = merge_trade_rows(paper, demo)
    ids = [r["id"] for r in merged]
    assert ids.count("P2") == 1               # deduped
    assert {"P1", "P2", "D1"} <= set(ids)     # nothing lost


def test_merge_empty_books_safe():
    assert merge_trade_rows([], None, [_row("X")]) == [_row("X")]


def test_dashboard_has_no_split_markers():
    html_text = render_dashboard([_row("P1"), _row("D1")])
    assert "DEMO" not in html_text
    assert "🧪" not in html_text
    assert "Gold AI Signals Dashboard" in html_text
    assert "Paper Trading" not in html_text


def test_telegram_card_has_no_split_markers():
    text = format_dashboard_telegram(
        summarize_trades([_row("P1"), _row("D1")]))
    assert "DEMO" not in text
    assert "🧪" not in text
    assert "Dashboard Updated" in text
