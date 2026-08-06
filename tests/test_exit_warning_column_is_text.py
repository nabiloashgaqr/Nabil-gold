"""Regression: trades.exit_warning must be TEXT, never BOOLEAN.

Live evidence (analyze run #8966, 2026-08-06 18:33 UTC):

    PATCH /rest/v1/trades?id=eq.TRADE_..._d4351dad -> HTTP/2 400
    {'message': 'invalid input syntax for type boolean: "NEAR_STOP_LOSS"',
     'code': '22P02'}

agents/open_trades_manager.py::_exit_warning() writes rich labels
('NEAR_STOP_LOSS' | 'ADVERSE_MOVE_DEEP' | NULL). When the column was
BOOLEAN, every near-stop update 400'd atomically and collapsed into the
legacy payload, silently dropping exit_warning + management_phase +
trailing telemetry. These tests fail if the boolean column (or a boolean
writer) is ever reintroduced.
"""

from __future__ import annotations

import re
from pathlib import Path
import sys
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

SCHEMA_SQL = ROOT / "supabase_schema_unified.sql"


# ---------------------------------------------------------------------------
# Guard 1: the schema itself
# ---------------------------------------------------------------------------

def _exit_warning_declarations() -> List[str]:
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    # Every line that declares the column (CREATE TABLE column or ADD COLUMN).
    decls = [
        line.strip()
        for line in text.splitlines()
        if "exit_warning" in line and ("ADD COLUMN" in line or re.match(r"^\s*exit_warning\b", line))
    ]
    assert decls, f"exit_warning disappeared from {SCHEMA_SQL.name}"
    return decls


def test_schema_declares_exit_warning_as_text():
    for decl in _exit_warning_declarations():
        assert re.search(r"exit_warning\s+TEXT", decl, re.IGNORECASE), (
            f"exit_warning must be TEXT (it stores labels like 'NEAR_STOP_LOSS'), got: {decl!r}"
        )


def test_schema_does_not_declare_exit_warning_as_boolean():
    """Fault injection: reintroducing BOOLEAN reproduces the 22P02 outage."""
    for decl in _exit_warning_declarations():
        assert not re.search(r"exit_warning\s+BOOLEAN", decl, re.IGNORECASE), (
            f"exit_warning declared BOOLEAN again -- 'NEAR_STOP_LOSS' cannot be "
            f"stored in a boolean and every near-stop update will 400: {decl!r}"
        )


# ---------------------------------------------------------------------------
# Guard 2: the writer never emits booleans
# ---------------------------------------------------------------------------

def _exit_warning(*args: Any) -> Any:
    from agents.open_trades_manager import OpenTradesManager

    # _exit_warning does not touch self; call it unbound with None.
    return OpenTradesManager._exit_warning(None, *args)  # type: ignore[arg-type]


def test_exit_warning_near_stop_label():
    # BUY entry 4300, SL 4200 (risk 100). Price 4226 -> 26 pts above the stop
    # (> 25% of risk? no: 26 > 25, so pick 4224: distance 24 <= 25).
    result = _exit_warning("BUY", 4300.0, 4200.0, 4400.0, 4224.0, -76.0)
    assert result == "NEAR_STOP_LOSS"
    assert not isinstance(result, bool)


def test_exit_warning_deep_adverse_label():
    # BUY entry 4300, SL 4200. Price 4230: pnl -70 < -0.65*100, and the price
    # sits 30 pts above the stop (> 0.25 * risk), so the deep-move branch fires.
    result = _exit_warning("BUY", 4300.0, 4200.0, 4400.0, 4230.0, -70.0)
    assert result == "ADVERSE_MOVE_DEEP"
    assert not isinstance(result, bool)


def test_exit_warning_healthy_trade_is_none():
    result = _exit_warning("BUY", 4300.0, 4200.0, 4400.0, 4350.0, 50.0)
    assert result is None


def test_exit_warning_sell_side_uses_absolute_distances():
    # SELL entry 4300, SL 4400. Price 4376 -> 24 pts below the stop -> near.
    assert _exit_warning("SELL", 4300.0, 4400.0, 4200.0, 4376.0, -76.0) == "NEAR_STOP_LOSS"


# ---------------------------------------------------------------------------
# Guard 3: characterize the failure mode so a future boolean column is visible
# ---------------------------------------------------------------------------

class _TypeMismatch(Exception):
    pass


class _BooleanColumnQuery:
    """Mimics Postgres rejecting a string written into a BOOLEAN column."""

    def __init__(self, table: "_BooleanColumnTable", payload: Dict[str, Any]):
        self.table = table
        self.payload = payload

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        value = self.payload.get("exit_warning")
        if isinstance(value, str):
            raise _TypeMismatch(
                "{'message': 'invalid input syntax for type boolean: "
                "\"" + str(value) + "\", 'code': '22P02', 'hint': None, 'details': None}"
            )
        self.table.last_payload = dict(self.payload)
        self.table.calls.append(dict(self.payload))
        return type("Resp", (), {"data": [self.payload]})()


class _BooleanColumnTable:
    def __init__(self):
        self.last_payload: Dict[str, Any] | None = None
        self.calls: List[Dict[str, Any]] = []

    def update(self, payload):
        return _BooleanColumnQuery(self, payload)


class _BooleanColumnClient:
    def __init__(self):
        self._table = _BooleanColumnTable()

    def table(self, _name):
        return self._table


def test_boolean_column_silently_drops_the_label_via_legacy_fallback():
    """Characterizes the 2026-08-06 outage: with a boolean column the label
    never persists and the row survives only via the legacy payload. This is
    WHY the schema test above must stay green."""
    from services.database import DatabaseService

    db = DatabaseService({"database": {"provider": "supabase"}})
    db.client = _BooleanColumnClient()  # type: ignore[assignment]
    db.use_supabase = True

    updates = {
        "stop_loss": 4250.0,
        "sl_moved_to_entry": True,
        "exit_warning": "NEAR_STOP_LOSS",
        "management_phase": "DEFENSIVE",
    }
    db._update_trade_supabase("TRADE_TEST", updates)

    persisted = db.client._table.last_payload  # type: ignore[attr-defined]
    assert persisted is not None, "row update failed outright"
    assert persisted.get("stop_loss") == 4250.0, "critical management field must survive"
    # The label cannot live in a boolean column -- the legacy fallback drops it.
    assert persisted.get("exit_warning") in (None, False), (
        "label unexpectedly persisted despite boolean column"
    )
