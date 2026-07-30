"""The trailing rule quoted in a message must be the rule that was applied.

The Telegram card carried the literal string "150-point gap / 40-point step"
in two places. A trade running `continuation_profile` trails at 170/45, so a
single message said:

    Trailing rule: 150-point gap / 40-point step        <- hardcoded
    Management: Trail gap 170 pts / step 45 pts · check 5m   <- real config

...contradicting itself, and contradicting the arithmetic behind the very stop
it was reporting. The reversal trail (60 pts) would have made it wrong a third
way.

The manager now publishes the distance and step it actually used, and the card
reads them.

Fault injection: restore either hardcoded string and the matching test fails.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.telegram_bot import TelegramService

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADE = {"id": "T", "symbol": "XAU/USD", "type": "BUY",
         "status": "OPEN", "entry_price": 4062.05}


def _config() -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _card(updates: dict, config: dict | None = None) -> str:
    cfg = config or _config()
    service = TelegramService({**cfg, "telegram": {"bot_token": None, "chat_id": None}})
    captured: dict = {}
    service.send_message = lambda text, urgent=False, **_k: captured.setdefault("t", text) or True  # type: ignore[assignment]
    service.send_trade_events(
        TRADE, ["TRAILING_SL_UPDATED"], 4073.82, 117.7,
        {"old_status": "OPEN", "new_status": "OPEN",
         "events": ["TRAILING_SL_UPDATED"], "pnl_points": 117.7, "updates": updates},
    )
    return re.sub("<[^>]+>", "", captured["t"])


# ── the reported bug ───────────────────────────────────────────────────────

def test_a_continuation_profile_trade_reports_its_own_gap() -> None:
    text = _card({"stop_loss": 4070.96,
                  "trailing_distance_points": 170.0, "trailing_step_points": 45.0})
    assert "170-point gap / 45-point step" in text
    assert "150-point gap" not in text, "the hardcoded default is back"


def test_the_two_trailing_lines_agree_with_each_other() -> None:
    """Both the rule line and the note are generated from the same source."""
    text = _card({"stop_loss": 4070.96,
                  "trailing_distance_points": 170.0, "trailing_step_points": 45.0})
    quoted = re.findall(r"(\d+)-point gap / (\d+)-point step", text)
    assert len(quoted) >= 2, "expected the rule line and the note"
    assert len(set(quoted)) == 1, f"the message contradicts itself: {quoted}"


def test_the_message_matches_the_profile_in_config() -> None:
    """Guards against the card and config.json drifting apart again."""
    profile = (_config()["trade_management"].get("profiles") or {}).get("continuation_profile") or {}
    gap = float(profile.get("trailing_distance_points"))
    step = float(profile.get("trailing_step_points"))
    text = _card({"stop_loss": 4070.96,
                  "trailing_distance_points": gap, "trailing_step_points": step})
    assert f"{gap:.0f}-point gap / {step:.0f}-point step" in text


# ── the tightened reversal trail ───────────────────────────────────────────

def test_a_tightened_trail_is_reported_and_explained() -> None:
    text = _card({"stop_loss": 4077.00, "trailing_distance_points": 60.0,
                  "trailing_step_points": 45.0, "reversal_trail_active": True})
    assert "60-point gap" in text
    assert "agent book turned against" in text


def test_an_ordinary_trail_is_not_labelled_as_tightened() -> None:
    text = _card({"stop_loss": 4070.96,
                  "trailing_distance_points": 170.0, "trailing_step_points": 45.0})
    assert "tightened" not in text


# ── graceful fallbacks ─────────────────────────────────────────────────────

def test_it_falls_back_to_config_when_the_manager_says_nothing() -> None:
    """Older rows carry no trailing fields; the root config is the next truth."""
    mgmt = _config()["trade_management"]
    gap = float(mgmt.get("trailing_distance_points"))
    step = float(mgmt.get("trailing_step_points"))
    text = _card({"stop_loss": 4070.96})
    assert f"{gap:.0f}-point gap / {step:.0f}-point step" in text


def test_unparseable_values_do_not_break_the_message() -> None:
    text = _card({"stop_loss": 4070.96,
                  "trailing_distance_points": "nonsense", "trailing_step_points": None})
    assert "point gap" in text
    assert "New SL" in text


# ── the source of truth ────────────────────────────────────────────────────

def test_the_manager_publishes_the_distance_it_used() -> None:
    from datetime import datetime, timezone
    from agents.open_trades_manager import OpenTradesManager

    manager = OpenTradesManager(_config())
    trade = {
        "id": "T", "type": "BUY", "status": "OPEN", "symbol": "XAU/USD",
        "entry_price": 4062.05, "stop_loss": 4062.05, "initial_stop_loss": 4043.57,
        "tp1": 4075.79, "tp2": 4093.31, "partial_close": True,
        "sl_moved_to_entry": True, "management_phase": "POST_TP1_TRAILING",
        "updates_sent": ["ORDER_FILLED", "TP1_HIT", "MOVE_SL_TO_BE"],
        "entry_time": "2026-07-30T09:20:00+00:00",
        "created_at": "2026-07-30T09:05:48+00:00",
        "max_favorable_excursion": 191.0, "current_pnl_points": 191.0,
    }
    result = manager.evaluate_trade(
        trade, 4081.14, now=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        candle_high=4081.5, candle_low=4080.0,
        recent_candles=[
            {"time": "a", "open": 4082.0, "high": 4083.0, "low": 4079.0, "close": 4081.0},
            {"time": "b", "open": 4081.0, "high": 4081.5, "low": 4080.0, "close": 4081.14},
        ],
    )
    updates = result["updates"]
    assert "trailing_distance_points" in updates
    assert "trailing_step_points" in updates
    assert float(updates["trailing_distance_points"]) > 0
