"""A refusal message must state the rule that actually refused it.

WHAT THIS PINS
--------------
``post_tp2_reentry.window_hours`` was cut from 3 to 2.5 on 2026-08-03 at the
operator's request. The block message was formatted with ``:.0f``::

    f"(needs >={min_distance_points:.0f} pts within {window_hours:.0f}h)."

``f"{2.5:.0f}"`` is ``"2"``. So the moment the window became fractional, every
refusal announced a 2-hour rule while a 2.5-hour rule was doing the blocking.
The operator would have been told a signal was refused under a bar that did
not exist, and any hand-check of "was this block correct?" would have used the
wrong number.

This is the same class of defect as the activation card reporting the planned
entry instead of the fill: the system did the right thing and then described
it wrongly. A guard whose message cannot be trusted cannot be audited.

WHAT THE FIX IS
---------------
``_trim_zero`` renders one decimal only when it carries information, so 2.5
prints "2.5" and 3.0 still prints "3" rather than the noisier "3.0".

FAULT INJECTION
---------------
Restore ``{window_hours:.0f}h`` in ``_post_tp2_reentry_block`` and
``test_the_message_quotes_the_fractional_window`` fails with the message
claiming "within 2h" while the config says 2.5.

NO RISK SETTING IS READ AS A TARGET HERE
----------------------------------------
These tests assert only that the *rendered text* matches the *configured*
value. They do not assert any particular window, so changing the window again
will not break them -- only lying about it will.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "run_analysis_block_message", os.path.join(ROOT, "scripts", "run_analysis.py")
)
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)

from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()
SYMBOL = "XAU/USD"
NOW = datetime(2026, 8, 3, 15, 2, tzinfo=timezone.utc)

# The real numbers from 2026-08-03.
TP2 = 4022.31
NEW_ENTRY = 4045.99


def _closed_trade(hours_ago: float, *, tp2: float = TP2) -> dict:
    return {
        "id": "TRADE_20260803_112629_291441_79fb5a6e",
        "symbol": SYMBOL, "side": "SELL", "status": "TP2_HIT",
        "tp2": tp2, "entry_price": 4052.85, "close_price": tp2,
        "final_pnl_points": 305.4,
        "closed_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
    }


def _decision(entry: float = NEW_ENTRY) -> dict:
    return {
        "decision": "SELL", "symbol": SYMBOL, "current_price": 4035.53,
        "signal": {"order_type": "SELL_LIMIT", "entry": {"price": entry},
                   "stop_loss": 4077.48, "tp1": 3987.48, "tp2": 3947.48},
    }


def _config(window_hours: float) -> dict:
    cfg = {k: v for k, v in CONFIG.items()}
    block = dict(cfg.get("post_tp2_reentry") or {})
    block["window_hours"] = window_hours
    cfg["post_tp2_reentry"] = block
    return cfg


def _block(window_hours: float, hours_ago: float = 1.4) -> str | None:
    return ra._post_tp2_reentry_block(
        _decision(), [_closed_trade(hours_ago)], _config(window_hours),
        now=NOW, symbol=SYMBOL, entry_price=NEW_ENTRY, direction="SELL",
    )


# ── the formatter ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value, expected",
    [(3.0, "3"), (2.5, "2.5"), (2.0, "2"), (1.5, "1.5"), (0.5, "0.5"), (12.0, "12")],
)
def test_trim_zero_keeps_a_decimal_only_when_it_matters(value, expected):
    assert ra._trim_zero(value) == expected


# ── the message ─────────────────────────────────────────────────────────────

def test_the_message_quotes_the_fractional_window():
    """A 2.5-hour rule must not announce itself as a 2-hour rule."""
    reason = _block(2.5)
    assert reason is not None
    assert "within 2.5h" in reason, (
        f"message rounds the window away: {reason!r}"
    )
    assert "within 2h" not in reason, (
        "the message states a window the rule is not using"
    )


def test_a_whole_window_is_not_printed_with_a_trailing_zero():
    reason = _block(3.0)
    assert reason is not None
    assert "within 3h" in reason and "3.0h" not in reason


@pytest.mark.parametrize("window", [1.0, 1.5, 2.0, 2.5, 3.0, 4.0])
def test_the_message_always_matches_the_configured_window(window):
    """Whatever the window is set to, the text must name that same value."""
    reason = _block(window, hours_ago=0.2)
    assert reason is not None
    assert f"within {ra._trim_zero(window)}h" in reason


def test_the_message_still_reports_distance_and_age():
    """The rest of the sentence must stay auditable."""
    reason = _block(2.5)
    assert "237 pts above the TP2 4022.31" in reason, reason
    assert "1.4h ago" in reason, reason
    assert "250" in reason, reason


# ── the boundary the operator actually asked to move ────────────────────────

@pytest.mark.parametrize(
    "hours_ago, blocked_at_3h, blocked_at_2h30",
    [
        (1.4, True, True),    # the live case: unchanged
        (2.0, True, True),
        (2.45, True, True),
        (2.55, True, False),  # the new gap
        (2.90, True, False),
        (3.10, False, False),
    ],
)
def test_only_the_last_half_hour_changes(hours_ago, blocked_at_3h, blocked_at_2h30):
    """Cutting 3h -> 2.5h must free exactly the 2.5-3.0h band and nothing else."""
    assert (_block(3.0, hours_ago) is not None) is blocked_at_3h
    assert (_block(2.5, hours_ago) is not None) is blocked_at_2h30


def test_the_live_2026_08_03_signal_is_still_refused():
    """The operator's current message must not be affected by the change.

    TP2 4022.31 was taken 1.4h ago and the entry is 237 pts above it. That is
    inside both windows, so shortening the window must not release it -- the
    distance rule is what refuses it, not the clock.
    """
    reason = _block(2.5, 1.4)
    assert reason is not None
    assert "237 pts" in reason


def test_distance_still_governs_independently_of_the_window():
    """A far-enough entry is allowed even well inside the window."""
    far = ra._post_tp2_reentry_block(
        _decision(entry=TP2 + 30.0),  # 300 pts above TP2
        [_closed_trade(0.1)], _config(2.5),
        now=NOW, symbol=SYMBOL, entry_price=TP2 + 30.0, direction="SELL",
    )
    assert far is None


def test_no_other_setting_moved():
    """Only window_hours changed; the distance and the risk floor did not."""
    block = CONFIG.get("post_tp2_reentry") or {}
    risk = CONFIG.get("risk_settings") or {}
    assert float(block["min_distance_points"]) == 250.0
    assert block.get("enabled") is True
    assert float(risk["min_sl_distance_points"]) == 400.0
    assert float(risk["min_rr_ratio"]) == 1.5
