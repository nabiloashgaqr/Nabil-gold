"""Agreeing on the maths is not enough; both surfaces must see the same rows.

THE SECOND DIVERGENCE
---------------------
``services/performance_stats.py`` made the two dashboards compute a summary
the same way. They still disagreed::

    website  : 85 closed trades
    Telegram : 52 closed trades

Because the maths was never the whole problem. The two surfaces were reading
different samples.

    Telegram : db.get_recent_trades(limit=80)
               -> ORDER BY created_at DESC LIMIT 80
               -> then the summary filters to closed rows
               -> pending, cancelled and open rows had already consumed the
                  window, leaving 52 finished trades

    website  : supabaseGet('trades', { status: in.(closed...), limit: 150 })
               -> the database filters first, so all 150 slots hold rows that
                  can actually have an outcome
               -> 85 finished trades

Filtering AFTER truncation throws away the oldest closed trades, and how many
it throws away depends on how much unfilled noise happens to sit at the top of
the table. The number was therefore unstable as well as wrong.

THE FIX
-------
``DatabaseService.get_recent_closed_trades`` filters in the query, using the
same status list and the same ordering as the web API, and
``scripts/generate_dashboard.py`` uses it with the same default limit of 150.
Open positions are fetched separately so the card can report them without
folding their floating PnL into the realised total.

FAULT INJECTION
---------------
Point ``generate_dashboard`` back at ``get_recent_trades(limit=80)`` and
``test_the_card_and_the_website_see_the_same_count`` fails: the card reports
52 where the website reports 85.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import performance_stats as ps  # noqa: E402
from services.dashboard import summarize_trades  # noqa: E402


# ── a table shaped like the live one ────────────────────────────────────────
#
# 113 rows: closed trades interleaved with the pending and cancelled orders
# that were crowding the Telegram window.

def _row(i, status, pnl, side="BUY"):
    return {
        "id": f"T{i:03d}", "status": status, "final_pnl": pnl, "type": side,
        "symbol": "XAU/USD", "confidence": 84.0,
        "created_at": f"2026-07-{(i % 28) + 1:02d}T10:00:00+00:00",
        "closed_at": f"2026-07-{(i % 28) + 1:02d}T14:00:00+00:00"
        if status in ps.CLOSED_STATUSES else None,
    }


def _table():
    rows = []
    i = 0
    # The newest 28 rows are mostly unfilled noise -- this is what ate the
    # Telegram window.
    for _ in range(20):
        rows.append(_row(i, "CANCELLED", 0.0)); i += 1
    for _ in range(8):
        rows.append(_row(i, "PENDING", 0.0)); i += 1
    rows.append(_row(i, "OPEN", 55.0)); i += 1
    # 84 finished trades underneath.
    for _ in range(9):
        rows.append(_row(i, "TP2_HIT", 320.0)); i += 1
    for _ in range(46):
        rows.append(_row(i, "SL_HIT", 180.0, "SELL")); i += 1
    for _ in range(14):
        rows.append(_row(i, "SL_HIT", -380.0, "SELL")); i += 1
    for _ in range(10):
        rows.append(_row(i, "BE_HIT", 0.0)); i += 1
    for _ in range(5):
        rows.append(_row(i, "THESIS_EXIT", 90.0)); i += 1
    return rows


TABLE = _table()
CLOSED_IN_TABLE = [r for r in TABLE if ps.is_closed(r)]


def _website_sample(limit=150):
    """What the web API asks for: closed rows, filtered in the query."""
    return [r for r in TABLE if ps.is_closed(r)][:limit]


def _old_telegram_sample(limit=80):
    """What the card used to do: newest 80 rows, filtered afterwards."""
    return [r for r in TABLE[:limit] if ps.is_closed(r)]


def _new_telegram_sample(limit=None):
    """What the card ACTUALLY samples, driven through the real code path.

    An earlier draft re-implemented the query here. That made the count tests
    pass even with the old sampling restored, because they were testing the
    test's own copy of the logic. They now run the real
    ``DatabaseService.get_recent_closed_trades`` against a fake table and read
    the limit the real generator would use.
    """
    return _db_sample(limit if limit is not None else _generator_limit())


def _generator_limit() -> int:
    """The default limit scripts/generate_dashboard.py would pass."""
    gen = open(
        os.path.join(ROOT, "scripts", "generate_dashboard.py"), encoding="utf-8"
    ).read()
    if "get_recent_closed_trades" not in gen:
        # The generator is still sampling by created_at; reproduce that so the
        # count assertions below see what the operator would see.
        limit = int(re.search(r'DASHBOARD_TRADE_LIMIT",\s*"(\d+)"', gen).group(1))
        return -limit  # negative marks the legacy path
    return int(re.search(r'DASHBOARD_TRADE_LIMIT",\s*"(\d+)"', gen).group(1))


def _db_sample(limit: int):
    """Run the real database helper over the fake table."""
    if limit < 0:
        # Legacy behaviour: newest rows by created_at, filtered afterwards.
        return [r for r in TABLE[: -limit] if ps.is_closed(r)]

    from services.database import DatabaseService

    class _FakeDB(DatabaseService):
        def __init__(self):  # noqa: D107 - no config, no I/O
            self.use_supabase = False
            self.client = None
            self.local_path = None
            self.logger = __import__("logging").getLogger("fake")

    db = _FakeDB()
    import services.database as dbmod

    original = dbmod.load_trades
    dbmod.load_trades = lambda _path: list(TABLE)
    try:
        return db.get_recent_closed_trades(limit=limit)
    finally:
        dbmod.load_trades = original


# ── the defect ──────────────────────────────────────────────────────────────

def test_the_old_sampling_really_did_lose_trades():
    """Precondition: truncate-then-filter drops finished trades."""
    assert len(_old_telegram_sample()) < len(_website_sample()), (
        "the fixture does not reproduce the reported gap"
    )


def test_the_card_and_the_website_see_the_same_count():
    assert len(_new_telegram_sample()) == len(_website_sample())


def test_the_card_and_the_website_report_the_same_summary():
    a = summarize_trades(_new_telegram_sample())
    b = summarize_trades(_website_sample())
    for key in ("total", "wins", "losses", "breakeven", "win_rate",
                "net_points", "profit_factor"):
        assert a[key] == b[key], f"{key}: card {a[key]} vs website {b[key]}"


def test_no_finished_trade_is_dropped_by_unfilled_noise():
    """Cancelled and pending rows must not consume the window."""
    assert len(_new_telegram_sample()) == len(CLOSED_IN_TABLE)


# ── the query itself ────────────────────────────────────────────────────────

def test_the_database_exposes_a_closed_only_query():
    from services.database import DatabaseService
    assert hasattr(DatabaseService, "get_recent_closed_trades"), (
        "there is no way to ask the database for finished trades, so every "
        "caller has to truncate first and filter afterwards"
    )


def test_the_generator_uses_it():
    source = open(
        os.path.join(ROOT, "scripts", "generate_dashboard.py"), encoding="utf-8"
    ).read()
    assert "get_recent_closed_trades" in source, (
        "the dashboard generator still samples by created_at"
    )
    assert "get_recent_trades(limit=" not in source


def test_the_default_limit_matches_the_web_api():
    """A different window size is a different answer, however small."""
    gen = open(
        os.path.join(ROOT, "scripts", "generate_dashboard.py"), encoding="utf-8"
    ).read()
    py_limit = int(re.search(r'DASHBOARD_TRADE_LIMIT",\s*"(\d+)"', gen).group(1))

    api_path = os.path.join(ROOT, "dashboard", "api", "dashboard.js")
    if not os.path.exists(api_path):
        pytest.skip("web API not present in this checkout")
    api = open(api_path, encoding="utf-8").read()
    js_limit = int(
        re.search(r"parseInt\(req\.query\.limit \|\| '(\d+)'", api).group(1)
    )
    assert py_limit == js_limit, (
        f"card samples {py_limit} trades, website samples {js_limit}"
    )


def test_the_query_orders_by_close_time_like_the_web_api():
    source = open(
        os.path.join(ROOT, "services", "database.py"), encoding="utf-8"
    ).read()
    body = source[source.index("def get_recent_closed_trades"):]
    body = body[: body.index("def get_trades_closed_since")]
    assert '"closed_at"' in body, "must order by close time, not creation time"
    assert ".in_(\"status\", statuses)" in body, "must filter in the query"


def test_open_positions_are_still_reported_but_kept_separate():
    sample = _new_telegram_sample() + [r for r in TABLE if r["status"] == "OPEN"]
    s = summarize_trades(sample)
    assert s["open"] == 1
    assert s["open_floating_points"] == pytest.approx(55.0)
    # The floating 55 must not be inside the realised net.
    assert s["net_points"] == pytest.approx(
        summarize_trades(_new_telegram_sample())["net_points"]
    )


def test_the_counts_still_reconcile():
    s = summarize_trades(_new_telegram_sample())
    assert s["wins"] + s["losses"] + s["breakeven"] == s["total"]
