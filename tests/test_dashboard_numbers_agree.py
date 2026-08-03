"""The Telegram card and the web dashboard must report the same numbers.

THE INCIDENT
------------
The operator received this card::

    Trades: 80 | Open: 1
    Win Rate: 79.07% · W: 34 / L: 9
    Net: +5111.9 pts · PF: 3.65

and reported it did not match the website. Three separate divergences:

1. TRADE SET.  ``summarize_trades`` summed over every row handed to it,
   including PENDING and CANCELLED orders that were never filled. That is why
   ``W + L = 43`` does not reconcile with ``Trades: 80`` -- 37 rows sat in
   neither column because they had no outcome. The web API filtered to
   finished statuses first.

2. NET AND PROFIT FACTOR.  Following from (1), realised profit was summed
   together with the floating PnL of open positions. Those are different
   quantities.

3. THE DEFINITION OF A WIN.  The card used ``status == "TP2_HIT" OR
   pnl > 0``; the web used ``pnl > 0``. The status test flatters the record:
   a TP2_HIT row with zero or negative recorded PnL still counted as a win.

OPERATOR DECISION (2026-08-03)
------------------------------
Closed trades only; a win is ``pnl > 0``; one shared source of truth.
``services/performance_stats.py`` now owns the definition and
``summarize_trades`` delegates to it.

WHY ``pnl > 0`` IS THE RIGHT TEST
---------------------------------
SL_HIT is not always a loss. Once the stop has been trailed into profit a
stop-out is a winning trade, and the sign of the realised PnL captures that
while the status does not. It is stricter in the other direction too: a
TP2_HIT that somehow recorded no profit is not counted as a win.

FAULT INJECTION
---------------
Restore the old body of ``summarize_trades`` (summing over all rows, win =
status or pnl) and ``test_the_reported_card_reconciles`` and
``test_pending_and_cancelled_are_not_trades`` fail: the totals stop adding up
exactly as they did on the live card.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import performance_stats as ps  # noqa: E402
from services.dashboard import (  # noqa: E402
    format_dashboard_telegram,
    summarize_trades,
)


def _t(status, pnl, side="BUY", conf=84.0):
    return {"status": status, "final_pnl": pnl, "type": side, "confidence": conf}


def _sample():
    """A set shaped like the live table: outcomes, plus unfilled noise."""
    rows = []
    rows += [_t("TP2_HIT", 320.0) for _ in range(9)]
    rows += [_t("SL_HIT", 180.0, "SELL") for _ in range(20)]   # trailed to profit
    rows += [_t("SL_HIT", -380.0, "SELL") for _ in range(9)]
    rows += [_t("BE_HIT", 0.0) for _ in range(10)]
    rows += [_t("THESIS_EXIT", 90.0) for _ in range(5)]
    rows += [_t("CANCELLED", 0.0) for _ in range(27)]          # never filled
    rows += [_t("PENDING", 0.0) for _ in range(6)]             # never filled
    rows += [_t("OPEN", 55.0)]                                  # floating
    return rows


# ── the incident ────────────────────────────────────────────────────────────

def test_the_reported_card_reconciles():
    """W + L + BE must equal the trade count the card prints."""
    s = summarize_trades(_sample())
    assert s["wins"] + s["losses"] + s["breakeven"] == s["total"], (
        f"card shows {s['total']} trades but only "
        f"{s['wins'] + s['losses'] + s['breakeven']} have an outcome"
    )


def test_pending_and_cancelled_are_not_trades():
    """33 unfilled orders must not inflate the headline count."""
    s = summarize_trades(_sample())
    assert s["total"] == 53, s          # 9 + 20 + 9 + 10 + 5
    assert s["open"] == 1
    assert s["pending"] == 6


def test_open_floating_pnl_is_not_added_to_net():
    """Realised and unrealised must never be summed together."""
    s = summarize_trades(_sample())
    closed_only = sum(
        ps.pnl_of(t) for t in _sample() if ps.is_closed(t)
    )
    assert s["net_points"] == pytest.approx(round(closed_only, 2))
    assert s["open_floating_points"] == pytest.approx(55.0)
    assert s["net_points"] != pytest.approx(closed_only + 55.0)


def test_a_win_is_decided_by_pnl_not_status():
    """A TP2_HIT with no profit is not a win; an SL_HIT in profit is."""
    rows = [_t("TP2_HIT", 0.0), _t("SL_HIT", 210.0)]
    s = summarize_trades(rows)
    assert s["wins"] == 1, "the profitable stop-out must count as a win"
    assert s["breakeven"] == 1, "the zero-PnL TP2 must not count as a win"


def test_win_rate_excludes_breakeven():
    s = summarize_trades([_t("TP2_HIT", 100.0), _t("SL_HIT", -50.0), _t("BE_HIT", 0.0)])
    assert s["win_rate"] == pytest.approx(50.0)


# ── the card text itself ────────────────────────────────────────────────────

def test_the_card_names_its_scope():
    """"Trades: 80" was the ambiguity. The label must say what is counted."""
    text = format_dashboard_telegram(summarize_trades(_sample()))
    assert "Closed:" in text, text
    assert not re.search(r"^Trades:", text, re.M), (
        "an unqualified 'Trades:' label is what made the card unreadable"
    )


def test_the_card_reports_floating_separately_when_present():
    text = format_dashboard_telegram(summarize_trades(_sample()))
    assert "Open floating" in text and "not in Net" in text, text


def test_the_card_shows_breakeven_so_the_columns_add_up():
    text = format_dashboard_telegram(summarize_trades(_sample()))
    assert "BE:" in text, text


# ── the two implementations must not drift apart again ──────────────────────

def test_the_python_and_javascript_status_lists_match():
    """CLOSED_STATUSES here must mirror OUTCOME_STATUSES in the web API."""
    api = os.path.join(ROOT, "dashboard", "api", "dashboard.js")
    if not os.path.exists(api):
        pytest.skip("web API not present in this checkout")
    source = open(api, encoding="utf-8").read()
    match = re.search(r"const OUTCOME_STATUSES\s*=\s*\[([^\]]*)\]", source)
    assert match, "OUTCOME_STATUSES not found in the web API"
    js = {s.strip().strip("'\"").upper() for s in match.group(1).split(",") if s.strip()}
    assert js == ps.CLOSED_STATUSES, (
        f"the two dashboards disagree on what 'closed' means.\n"
        f"  only in JS     : {sorted(js - ps.CLOSED_STATUSES)}\n"
        f"  only in Python : {sorted(ps.CLOSED_STATUSES - js)}"
    )


def test_the_javascript_win_test_is_pnl_only():
    """Both sides must decide a win the same way."""
    api = os.path.join(ROOT, "dashboard", "api", "dashboard.js")
    if not os.path.exists(api):
        pytest.skip("web API not present in this checkout")
    source = open(api, encoding="utf-8").read()
    assert "const wins = closedTrades.filter(t => Number(t.pnl) > 0)" in source, (
        "the web API no longer decides a win by pnl sign; the Python side does"
    )


def test_summarize_trades_delegates_to_the_shared_module():
    """Source guard against a second implementation reappearing."""
    source = open(
        os.path.join(ROOT, "services", "dashboard.py"), encoding="utf-8"
    ).read()
    assert "performance_stats.summarize(trades)" in source, (
        "services/dashboard.py has its own summary maths again"
    )


# ── edge cases the old code got wrong or would crash on ─────────────────────

def test_an_empty_table_does_not_divide_by_zero():
    s = summarize_trades([])
    assert s["total"] == 0 and s["win_rate"] == 0.0 and s["net_points"] == 0.0


def test_all_wins_reports_an_infinite_profit_factor_safely():
    text = format_dashboard_telegram(summarize_trades([_t("TP2_HIT", 100.0)]))
    assert "∞" in text or "99.9" in text


def test_an_unknown_status_is_counted_not_dropped():
    """A new status string must not silently vanish from the totals."""
    s = summarize_trades([_t("SOME_NEW_STATUS", 120.0)])
    assert s["total"] == 1 and s["wins"] == 1


def test_missing_pnl_is_treated_as_zero_not_an_error():
    s = summarize_trades([{"status": "BE_HIT", "type": "BUY"}])
    assert s["total"] == 1 and s["breakeven"] == 1


def test_confidence_averages_over_closed_only():
    rows = [_t("TP2_HIT", 100.0, conf=90.0), _t("PENDING", 0.0, conf=10.0)]
    assert summarize_trades(rows)["avg_confidence"] == pytest.approx(90.0)
