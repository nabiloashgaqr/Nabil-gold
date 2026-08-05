"""An automatic thesis close must not be recorded as a manual one.

The thesis check writes the close itself, after judging that the idea the
trade was opened on no longer holds. Storing that as MANUAL_CLOSE made every
automatic exit look like an operator decision -- in the database, in the
weekly report, in the learning service and on the Telegram card.

MANUAL_CLOSE keeps its meaning: a human closed the trade
(scripts/run_close_trade_now.py). THESIS_EXIT is the automatic one.

The second half of this file is the part that matters most. A new closing
status is only half-added if some report still filters on the old list: the
trades do not appear as errors, they simply vanish from the statistics. Each
classifier is therefore checked directly.

Fault injection: revert `new_status` to "MANUAL_CLOSE" in
open_trades_manager and the first test fails; drop THESIS_EXIT from any one
classifier set and its own test fails.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager

SYMBOL = "XAU/USD"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config() -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _bullish_reclaim() -> list[dict]:
    return [
        {"time": "2026-07-30T06:30:00+00:00", "open": 4047.0, "high": 4048.2,
         "low": 4045.5, "close": 4046.5},
        {"time": "2026-07-30T06:45:00+00:00", "open": 4046.6, "high": 4050.2,
         "low": 4046.4, "close": 4049.94},
    ]


def _closed_by_thesis() -> dict:
    manager = OpenTradesManager(_config())
    # Relative age, never a frozen timestamp: a fixed created_at silently
    # becomes older than expire_after_hours and the trade starts reporting
    # EXPIRED instead of the behaviour under test.
    opened = datetime.now(timezone.utc) - timedelta(minutes=30)
    trade = {
        "id": "T", "type": "SELL", "status": "OPEN", "symbol": SYMBOL,
        "entry_price": 4046.02, "stop_loss": 4086.02, "tp1": 3996.0,
        "tp2": 3970.0,
        "created_at": opened.isoformat(), "entry_time": opened.isoformat(),
        "updates_sent": [],
    }
    # No agent book -> the legacy full exit path, which is the one that
    # produced the mislabelled status.
    return manager.evaluate_trade(
        trade, 4049.94, candle_high=4050.2, candle_low=4046.4,
        recent_candles=_bullish_reclaim(),
    )


# ── the rename itself ──────────────────────────────────────────────────────

def test_automatic_thesis_close_is_not_called_manual() -> None:
    result = _closed_by_thesis()
    assert result["new_status"] == "THESIS_EXIT"
    assert "THESIS_EXIT" in result["events"]
    assert "MANUAL_CLOSE" not in result["events"], (
        "an automatic close must not be reported as a manual one"
    )


def test_the_close_is_still_recorded_as_a_finished_trade() -> None:
    result = _closed_by_thesis()
    updates = result["updates"]
    assert updates["status"] == "THESIS_EXIT"
    assert updates.get("close_price")
    assert updates.get("closed_at")
    assert updates.get("final_pnl") is not None


def test_telegram_titles_the_card_from_the_event() -> None:
    from services.telegram_bot import TelegramService

    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    captured: dict = {}
    service.send_message = lambda text, urgent=False, **_k: captured.setdefault("text", text) or True  # type: ignore[assignment]

    trade = {"id": "T1", "symbol": SYMBOL, "type": "SELL",
             "status": "THESIS_EXIT", "entry_price": 4046.02}
    evaluation = {
        "old_status": "OPEN", "new_status": "THESIS_EXIT",
        "events": ["THESIS_EXIT"], "pnl_points": -39.2,
        "updates": {"close_price": 4049.94, "final_pnl": -39.2,
                    "reasons": ["Automatic thesis exit: bullish continuation "
                                "reclaimed the breakdown"]},
    }
    service.send_trade_events(trade, ["THESIS_EXIT"], 4049.94, -39.2, evaluation)
    text = captured["text"]
    assert "Thesis Exit" in text
    assert "Manual Close" not in text
    assert "Exit reason:" in text


def test_a_real_manual_close_is_still_manual() -> None:
    """run_close_trade_now.py is a human decision and must stay labelled so."""
    import scripts.run_close_trade_now as rcn

    source = open(rcn.__file__, encoding="utf-8").read()
    assert '"MANUAL_CLOSE"' in source
    assert "THESIS_EXIT" not in source


# ── the silent-drop guard ──────────────────────────────────────────────────
#
# Every set below decides whether a finished trade is counted at all. A new
# closing status missing from any of them does not raise: the trade quietly
# disappears from that report.

def test_manager_treats_thesis_exit_as_closed() -> None:
    assert "THESIS_EXIT" in OpenTradesManager.CLOSED_STATUSES


def test_manager_notifies_on_thesis_exit() -> None:
    assert "THESIS_EXIT" in OpenTradesManager.NOTIFIABLE_EVENTS


def test_run_analysis_outcome_classifier_knows_thesis_exit() -> None:
    from scripts.run_analysis import _BREAKEVEN_STATUSES, _trade_outcome

    assert "THESIS_EXIT" in _BREAKEVEN_STATUSES
    # A closed trade must never be mistaken for an open one.
    assert _trade_outcome({"status": "THESIS_EXIT"}) != "OPEN"
    assert _trade_outcome({"status": "THESIS_EXIT", "final_pnl": -39.2}) == "LOSS"
    assert _trade_outcome({"status": "THESIS_EXIT", "final_pnl": 67.4}) == "WIN"


def test_setup_performance_counts_thesis_exit() -> None:
    from services.setup_performance import _CLOSED_STATUSES

    assert "THESIS_EXIT" in _CLOSED_STATUSES


def test_exit_replay_treats_thesis_exit_as_a_closing_event() -> None:
    from services.exit_replay import ExitReplayHarness

    assert "THESIS_EXIT" in ExitReplayHarness.CLOSING_EVENTS


def test_execution_metrics_and_reports_include_thesis_exit() -> None:
    for path in ("services/execution_metrics.py", "services/weekly_report.py",
                 "services/database.py", "services/learning_service.py"):
        body = open(os.path.join(ROOT, path), encoding="utf-8").read()
        assert "THESIS_EXIT" in body, f"{path} still filters the old status list only"


def test_database_schema_accepts_the_new_status() -> None:
    """Postgres rejects an unlisted status outright, so the CHECK must allow it.

    The constraint now lives only in the unified schema (the one-off
    THESIS_EXIT_STATUS_MIGRATION.sql was consolidated and removed on
    2026-08-05), so assert against that single source of truth.
    """
    schema = open(os.path.join(ROOT, "supabase_schema_unified.sql"), encoding="utf-8").read()
    assert "'THESIS_EXIT'" in schema
    assert "CHECK (status IN (" in schema


def test_no_status_list_anywhere_forgets_thesis_exit() -> None:
    """A closing status missing from one list does not raise -- it hides rows.

    The dashboard proved this in production: `OUTCOME_STATUSES` in three
    JavaScript files still listed only MANUAL_CLOSE, so after the rename the
    renamed trades were filtered out of the UI and looked deleted. The rows
    were never touched; the filter simply stopped matching them.

    Any file that enumerates closing statuses must include THESIS_EXIT. The
    exceptions below are deliberate and each is justified.
    """
    import pathlib

    allowed_without = {
        # A genuine human close. Must NOT adopt the automatic name.
        "scripts/run_close_trade_now.py",
        # Proof scripts replaying recorded history, where the rows really
        # were written as MANUAL_CLOSE at the time.
        "scripts/prove_exit_without_entry.py",
        "scripts/prove_exit_then_replan_same_zone.py",
        "scripts/prove_agent_vote_before_after.py",
        # Tests covering the manual-close path itself.
        "tests/test_manual_close_trade.py",
        "tests/test_enrich_trade_close_now.py",
        "tests/test_duplicate_filter.py",
        "tests/test_integration.py",
        "tests/test_signal_formatting.py",
    }
    root = pathlib.Path(ROOT)
    offenders = []
    for pattern in ("**/*.py", "**/*.js", "**/*.sql"):
        for path in root.glob(pattern):
            parts = set(path.parts)
            if parts & {"node_modules", ".git", "__pycache__", ".venv"}:
                continue
            rel = path.relative_to(root).as_posix()
            if rel in allowed_without:
                continue
            try:
                body = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if "MANUAL_CLOSE" in body and "THESIS_EXIT" not in body:
                offenders.append(rel)

    assert not offenders, (
        "these files still enumerate MANUAL_CLOSE without THESIS_EXIT, so "
        f"automatic exits will silently vanish from them: {offenders}"
    )
