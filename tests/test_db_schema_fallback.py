"""Tests for resilient Supabase column handling in DatabaseService.

Regression for the production log:
  "Could not find the 'exit_warning' column of 'trades' in the schema cache" (PGRST204)

The old fallback collapsed the update to a tiny legacy payload, silently dropping
critical fields (stop_loss, result, sl_moved_to_entry, ...). The new behavior
drops ONLY the genuinely-missing column(s) and retries, preserving everything else.
"""

from __future__ import annotations

from typing import Any, Dict, List

from services.database import DatabaseService


class _PGRST204(Exception):
    """Mimic a PostgREST schema-cache missing-column error."""


def _err(col: str) -> _PGRST204:
    return _PGRST204(
        f"{{'message': \"Could not find the '{col}' column of 'trades' in the schema cache\", "
        f"'code': 'PGRST204', 'hint': None, 'details': None}}"
    )


class _FakeQuery:
    """Records update payloads and raises for columns not in the live schema."""

    def __init__(self, table: "_FakeTable", op: str, payload: Dict[str, Any]):
        self.table = table
        self.op = op
        self.payload = payload

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        missing = [c for c in self.payload if c not in self.table.live_columns]
        if missing:
            raise _err(missing[0])
        self.table.last_payload = dict(self.payload)
        self.table.calls.append(dict(self.payload))
        return type("Resp", (), {"data": [self.payload]})()


class _FakeTable:
    def __init__(self, live_columns: set):
        self.live_columns = live_columns
        self.last_payload: Dict[str, Any] | None = None
        self.calls: List[Dict[str, Any]] = []

    def update(self, payload):
        return _FakeQuery(self, "update", payload)

    def insert(self, payload):
        return _FakeQuery(self, "insert", payload)


class _FakeClient:
    def __init__(self, live_columns: set):
        self._table = _FakeTable(live_columns)

    def table(self, _name):
        return self._table


def _make_db(live_columns: set) -> DatabaseService:
    db = DatabaseService({"database": {"provider": "supabase"}})
    db.client = _FakeClient(live_columns)  # type: ignore[assignment]
    db.use_supabase = True
    return db


def test_missing_column_name_parsing():
    db = _make_db(set())
    assert db._missing_column_name(_err("exit_warning")) == "exit_warning"
    assert db._missing_column_name(Exception('column "foo" does not exist')) == "foo"
    assert db._missing_column_name(Exception("totally unrelated")) is None


def test_update_drops_only_missing_column_keeps_critical_fields():
    # Live schema lacks 'exit_warning' and 'management_phase' but HAS stop_loss etc.
    live = {"id", "status", "stop_loss", "sl_moved_to_entry", "result",
            "current_price", "current_pnl", "last_updated"}
    db = _make_db(live)
    updates = {
        "status": "TP1_HIT",
        "stop_loss": 2344.0,            # critical — must persist (breakeven move)
        "sl_moved_to_entry": True,      # critical — must persist
        "result": None,
        "current_price": 2351.0,
        "current_pnl": 7.0,
        "exit_warning": False,          # missing column -> should be dropped
        "management_phase": "RUNNING",  # missing column -> should be dropped
        "last_updated": "2026-06-23T12:39:27Z",
    }
    db.update_trade("TRADE_X", updates)

    stored = db.client._table.last_payload  # type: ignore[attr-defined]
    assert stored is not None
    # Critical fields survived.
    assert stored["stop_loss"] == 2344.0
    assert stored["sl_moved_to_entry"] is True
    assert stored["status"] == "TP1_HIT"
    # Only the genuinely-missing columns were dropped.
    assert "exit_warning" not in stored
    assert "management_phase" not in stored


def test_update_succeeds_directly_when_schema_complete():
    live = {"id", "status", "stop_loss", "sl_moved_to_entry", "result",
            "current_price", "current_pnl", "exit_warning", "management_phase", "last_updated"}
    db = _make_db(live)
    updates = {"status": "TP1_HIT", "exit_warning": True, "management_phase": "RUNNING", "stop_loss": 2344.0}
    db.update_trade("TRADE_Y", updates)
    stored = db.client._table.last_payload  # type: ignore[attr-defined]
    assert stored == updates
    # Exactly one successful call (no retries needed).
    assert len(db.client._table.calls) == 1  # type: ignore[attr-defined]
