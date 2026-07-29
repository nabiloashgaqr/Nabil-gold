"""The decision trail must record refusals, not only deliveries.

Every fault in this system was found by reading a workflow log by hand, days
after it mattered. Nine block paths existed and none of them left a durable
record, so the obvious questions had no answer: which filter stopped the most
signals this week? Did first targets keep shipping at 0.03R? How often did an
order go out against qualified dissent?

A trades table cannot answer any of them, because the interesting cases are
precisely the ones that never became trades.
"""

from __future__ import annotations

import inspect
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.run_analysis as ra
from services.database import DatabaseService
from services.execution_metrics import build_execution_metrics, format_execution_metrics

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


@pytest.fixture()
def database(tmp_path: Path) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None,
                                       "local_fallback_file": str(tmp_path / "trades.json")}})
    db.local_path = tmp_path / "trades.json"
    db.decision_audit_path = tmp_path / "decision_audit.json"
    return db


# --- storage ------------------------------------------------------------

def test_a_refusal_is_persisted(database: DatabaseService) -> None:
    database.save_decision_audit({
        "symbol": "XAU/USD", "stage": "cross-path distance", "outcome": "BLOCKED",
        "side": "BUY", "reason": "only 10 pts from an existing BUY",
    })

    rows = database.get_recent_decision_audits()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "BLOCKED"
    assert rows[0]["stage"] == "cross-path distance"
    assert rows[0]["id"].startswith("AUDIT_")


def test_deliveries_are_recorded_too(database: DatabaseService) -> None:
    """Without them there is no denominator for a delivery rate."""
    database.save_decision_audit({"symbol": "XAU/USD", "stage": "delivered",
                                  "outcome": "SENT", "tp1_rr": 1.8})
    database.save_decision_audit({"symbol": "XAU/USD", "stage": "dynamic risk",
                                  "outcome": "BLOCKED"})

    outcomes = {row["outcome"] for row in database.get_recent_decision_audits()}
    assert outcomes == {"SENT", "BLOCKED"}


def test_rows_can_be_filtered_by_symbol(database: DatabaseService) -> None:
    database.save_decision_audit({"symbol": "XAU/USD", "stage": "delivered", "outcome": "SENT"})
    database.save_decision_audit({"symbol": "XAG/USD", "stage": "delivered", "outcome": "SENT"})

    assert len(database.get_recent_decision_audits(symbol="XAU/USD")) == 1


def test_empty_entries_are_ignored(database: DatabaseService) -> None:
    assert database.save_decision_audit({}) == ""


# --- wiring -------------------------------------------------------------

def test_every_block_path_records_through_one_helper() -> None:
    """Nine call sites, one place that persists them."""
    source = inspect.getsource(ra._notify_blocked_directional_signal)
    assert "_record_decision_audit(" in source

    record = source.find("_record_decision_audit(")
    gate = source.find("if not (send_hourly_now")
    assert record < gate, (
        "the audit is written after the notification gate, so quiet refusals "
        "-- the majority -- would never be recorded"
    )


def test_audit_failure_cannot_break_a_cycle() -> None:
    """Recording is best-effort; a broken store must not stop a refusal."""
    class _Broken:
        def save_decision_audit(self, _entry):
            raise RuntimeError("supabase down")

    ra._record_decision_audit(
        _Broken(), {"decision": "BUY", "symbol": "XAU/USD"}, CONFIG,
        stage="test", outcome="BLOCKED", reason="x",
    )  # must not raise


def test_recorded_row_carries_the_r_multiples() -> None:
    """tp1_rr is the number that hid the worst fault for two sessions."""
    captured = {}

    class _Capture:
        def save_decision_audit(self, entry):
            captured.update(entry)
            return "AUDIT_X"

    decision = {
        "decision": "BUY", "symbol": "XAU/USD", "current_price": 4028.32,
        "signal": {"entry": {"price": 4028.32}, "stop_loss": 4013.32,
                   "tp1": 4028.85, "tp2": 4082.34},
    }
    ra._record_decision_audit(_Capture(), decision, CONFIG,
                              stage="final validation", outcome="BLOCKED", reason="tp1 too close")

    assert captured["tp1_rr"] == 0.04
    assert captured["tp2_rr"] == 3.6
    assert captured["outcome"] == "BLOCKED"


# --- metrics ------------------------------------------------------------

def test_metrics_expose_the_faults_that_shipped(database: DatabaseService) -> None:
    """Feed the trail the real week and check the numbers name the problems."""
    database.save_decision_audit({"symbol": "XAU/USD", "stage": "delivered",
                                  "outcome": "SENT", "tp1_rr": 0.04, "oppose_count": 3})
    database.save_decision_audit({"symbol": "XAU/USD", "stage": "delivered",
                                  "outcome": "SENT", "tp1_rr": 0.06, "oppose_count": 0})
    for _ in range(4):
        database.save_decision_audit({"symbol": "XAU/USD", "stage": "cross-path distance",
                                      "outcome": "BLOCKED"})

    metrics = build_execution_metrics(database, days=7)

    assert metrics["signals_sent"] == 2
    assert metrics["signals_blocked"] == 4
    assert metrics["median_tp1_rr"] == 0.05
    assert metrics["weak_tp1_count"] == 2
    assert metrics["opposed_signal_rate_pct"] == 50.0
    assert metrics["blocks_by_stage"]["cross-path distance"] == 4


def test_metrics_survive_an_empty_window(database: DatabaseService) -> None:
    metrics = build_execution_metrics(database, days=7)
    assert metrics["decisions_recorded"] == 0
    assert "No decisions recorded" in " ".join(format_execution_metrics(metrics))


def test_metrics_survive_a_broken_store() -> None:
    class _Broken:
        def get_recent_decision_audits(self, **_kwargs):
            raise RuntimeError("down")

        def get_recent_trades(self, **_kwargs):
            raise RuntimeError("down")

    metrics = build_execution_metrics(_Broken(), days=7)
    assert metrics["decisions_recorded"] == 0


def test_weak_first_targets_are_flagged_in_the_rendered_report(database: DatabaseService) -> None:
    database.save_decision_audit({"symbol": "XAU/USD", "stage": "delivered",
                                  "outcome": "SENT", "tp1_rr": 0.04})

    text = " ".join(format_execution_metrics(build_execution_metrics(database, days=7)))
    assert "below 0.80R" in text


def test_healthy_week_reads_clean(database: DatabaseService) -> None:
    for _ in range(5):
        database.save_decision_audit({"symbol": "XAU/USD", "stage": "delivered",
                                      "outcome": "SENT", "tp1_rr": 1.8, "oppose_count": 0})

    text = " ".join(format_execution_metrics(build_execution_metrics(database, days=7)))
    assert "below 0.80R" not in text
    assert "Sent against dissent" not in text
