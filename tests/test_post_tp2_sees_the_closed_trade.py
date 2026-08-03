"""The post-TP2 guard must be able to SEE the trade it guards against.

2026-08-03, the second failure of the same rule.

    13:38  SELL 79fb5a6e takes TP2 at 4022.31   (opened 11:26)
    14:10  a new SELL LIMIT is published at 4037.48 -- 152 pts above it

The rule was configured (250 pts / 3 hours), the code was deployed, the
adaptive bypass had already been closed, and the signal still went out.

WHY
---
``_post_tp2_reentry_reason`` gathered its candidates from
``get_recent_trades(limit=50)``, which orders by ``created_at``. That answers
"what was written recently", not "what closed recently".

79fb5a6e was CREATED at 11:26. By 14:10 the table had accumulated newer rows
-- 6dd160dd, 03ed828a, 2f72579f, every ladder leg, every cancelled pending,
and the other symbol shares the table. Once more than fifty rows exist with a
later ``created_at``, the trade that actually took TP2 is no longer in the
list the guard is handed.

A guard that cannot see the trade cannot block against it. Reproduced: with
sixty newer rows the guard returns ALLOWED, and the TP2 row is absent from
``limit=50``.

THE FIX
-------
``DatabaseService.get_trades_closed_since`` asks the database for trades that
CLOSED inside the window, ordered by closing time. That is the question this
rule has always been asking. The widened row scan stays as a fallback for
schemas with no ``closed_at`` column, so an older deployment degrades instead
of raising.

THE LESSON
----------
This is the fourth time a guard in this project was real, tested, and unable
to act -- the entry-zone floor, the SMC selection role, the adaptive bypass,
and now a lookup window. The first three were doors around the rule. This one
is subtler: the rule ran, on data that could not contain the answer.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "run_analysis_tp2_visibility", os.path.join(ROOT, "scripts", "run_analysis.py")
)
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)

from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()
SYMBOL = "XAU/USD"
TP2_TAKEN = 4022.31
NEW_ENTRY = 4037.48

_NOW = datetime.now(timezone.utc)


def _tp2_trade(closed_minutes_ago: float = 32.0,
               created_hours_ago: float = 2.75) -> dict:
    return {
        "id": "TRADE_20260803_112629_291441_79fb5a6e",
        "symbol": SYMBOL, "type": "SELL", "status": "TP2_HIT", "result": "WIN",
        "entry_price": 4052.85, "tp1": 4037.58, "tp2": TP2_TAKEN,
        "created_at": (_NOW - timedelta(hours=created_hours_ago)).isoformat(),
        "closed_at": (_NOW - timedelta(minutes=closed_minutes_ago)).isoformat(),
        "final_pnl": 305.4, "signal_snapshot": {"setup_context": {}},
    }


def _noise(count: int) -> list[dict]:
    """Rows created AFTER the TP2 trade: pendings, cancels, ladder legs."""
    return [
        {
            "id": f"noise-{i}", "symbol": SYMBOL, "type": "SELL",
            "status": "CANCELLED", "result": "CANCELLED", "entry_price": 4040.0,
            "created_at": (_NOW - timedelta(minutes=30 - i * 0.4)).isoformat(),
        }
        for i in range(count)
    ]


class _Database:
    """Mimics Supabase: created_at ordering, hard row limit."""

    def __init__(self, rows, *, supports_closed_at: bool = True):
        self._rows = rows
        self._supports = supports_closed_at
        self.closed_since_calls: list[str] = []

    def get_open_trades(self):
        return []

    def get_recent_trades(self, limit: int = 50):
        ordered = sorted(self._rows, key=lambda r: str(r.get("created_at") or ""),
                         reverse=True)
        return ordered[:limit]

    def __getattr__(self, name):
        if name == "get_trades_closed_since" and not self._supports:
            raise AttributeError(name)
        raise AttributeError(name)

    def get_trades_closed_since(self, since_iso, *, symbol=None, limit=200):
        if not self._supports:
            raise AttributeError("get_trades_closed_since")
        self.closed_since_calls.append(since_iso)
        return [
            r for r in self._rows
            if str(r.get("closed_at") or "") >= since_iso
            and (not symbol or str(r.get("symbol") or "") == symbol)
        ]


def _decision(entry: float = NEW_ENTRY) -> dict:
    return {
        "decision": "SELL", "symbol": SYMBOL, "current_price": 4029.74,
        "signal": {
            "order_type": "SELL_LIMIT", "entry": {"price": entry},
            "stop_loss": 4077.48, "tp1": 3987.48, "tp2": 3947.48,
        },
        "setup_context": {"selection_role": "PRIMARY"},
    }


# ── the incident ────────────────────────────────────────────────────────────

def test_the_trade_really_does_fall_out_of_the_created_at_window() -> None:
    """Establish the premise before asserting the fix."""
    rows = _noise(60) + [_tp2_trade()]
    visible = _Database(rows).get_recent_trades(limit=50)
    assert not any(r["id"].endswith("79fb5a6e") for r in visible), (
        "with sixty newer rows the closed trade is outside limit=50 -- this "
        "is why the guard could not act"
    )


def test_the_guard_blocks_despite_the_crowded_table() -> None:
    database = _Database(_noise(60) + [_tp2_trade()])
    reason = ra._post_tp2_reentry_reason(_decision(), database, CONFIG)

    assert reason is not None, "the rule must not depend on row ordering luck"
    assert "152 pts above the TP2 4022.31" in reason


def test_the_closed_since_query_is_actually_used() -> None:
    database = _Database(_noise(60) + [_tp2_trade()])
    ra._post_tp2_reentry_reason(_decision(), database, CONFIG)
    assert database.closed_since_calls, (
        "the guard must ask the database by closing time, not hope the row "
        "survives a created_at truncation"
    )


def test_the_window_asked_for_matches_the_configured_hours() -> None:
    database = _Database([_tp2_trade()])
    ra._post_tp2_reentry_reason(_decision(), database, CONFIG)
    asked = datetime.fromisoformat(database.closed_since_calls[0])
    hours = float(CONFIG["post_tp2_reentry"]["window_hours"])
    delta_hours = (_NOW - asked).total_seconds() / 3600.0
    assert abs(delta_hours - hours) < 0.1, (
        f"asked for {delta_hours:.2f}h of history against a {hours}h rule"
    )


# ── it must still work without the new query ────────────────────────────────

def test_a_legacy_schema_still_blocks_through_the_row_scan() -> None:
    """No closed_at column: the widened scan has to carry it."""
    database = _Database(_noise(60) + [_tp2_trade()], supports_closed_at=False)
    reason = ra._post_tp2_reentry_reason(_decision(), database, CONFIG)
    assert reason is not None


def test_a_failing_closed_since_query_never_breaks_the_cycle() -> None:
    class _Broken(_Database):
        def get_trades_closed_since(self, *a, **k):
            raise RuntimeError("supabase timeout")

    database = _Broken([_tp2_trade()])
    # The row scan still sees it here, so the block stands; the point is that
    # nothing raises.
    ra._post_tp2_reentry_reason(_decision(), database, CONFIG)


# ── the guard must stay narrow ──────────────────────────────────────────────

def test_a_trade_closed_before_the_window_is_ignored() -> None:
    old = _tp2_trade(closed_minutes_ago=4 * 60)
    database = _Database([old])
    assert ra._post_tp2_reentry_reason(_decision(), database, CONFIG) is None


def test_a_far_enough_re_entry_is_still_allowed() -> None:
    database = _Database(_noise(60) + [_tp2_trade()])
    far = _decision(entry=round(TP2_TAKEN + 27.7, 2))   # 277 pts above
    assert ra._post_tp2_reentry_reason(far, database, CONFIG) is None


def test_the_opposite_direction_is_still_allowed() -> None:
    database = _Database(_noise(60) + [_tp2_trade()])
    buy = dict(_decision(), decision="BUY")
    assert ra._post_tp2_reentry_reason(buy, database, CONFIG) is None


def test_no_history_means_no_block() -> None:
    assert ra._post_tp2_reentry_reason(_decision(), _Database([]), CONFIG) is None


# ── the database helper ─────────────────────────────────────────────────────

def test_the_helper_filters_by_closing_time_locally() -> None:
    from services.database import DatabaseService

    service = DatabaseService.__new__(DatabaseService)
    service.use_supabase = False
    service.client = None
    service.local_path = os.path.join(ROOT, "storage", "does_not_exist.json")

    since = (_NOW - timedelta(hours=3)).isoformat()
    assert service.get_trades_closed_since(since) == []


def test_no_risk_setting_was_changed() -> None:
    risk = CONFIG["risk_settings"]
    assert float(risk["min_sl_distance_points"]) == 400.0
    assert float(risk["min_rr_ratio"]) == 1.5
    post_tp2 = CONFIG["post_tp2_reentry"]
    assert float(post_tp2["min_distance_points"]) == 250.0
    assert float(post_tp2["window_hours"]) == 3.0
