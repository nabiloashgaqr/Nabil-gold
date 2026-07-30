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
    trade = {
        "id": "T", "type": "SELL", "status": "OPEN", "symbol": SYMBOL,
        "entry_price": 4046.02, "stop_loss": 4086.02, "tp1": 3996.0,
        "tp2": 3970.0, "created_at": "2026-07-30T06:31:00+00:00",
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
    """Postgres rejects an unlisted status outright, so the CHECK must allow it."""
    schema = open(os.path.join(ROOT, "supabase_schema_unified.sql"), encoding="utf-8").read()
    assert "'THESIS_EXIT'" in schema
    migration = os.path.join(ROOT, "THESIS_EXIT_STATUS_MIGRATION.sql")
    assert os.path.exists(migration), "the DB constraint change needs a migration file"
    body = open(migration, encoding="utf-8").read()
    assert "THESIS_EXIT" in body and "trades_status_check" in body
