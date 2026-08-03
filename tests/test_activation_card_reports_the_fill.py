"""The activation card must report the price the order actually filled at.

2026-08-03, trade TRADE_20260803_141059_572842_2f72579f:

    • Entry: 4037.48
    • Current Price: 4031.76
    • Distance to activation: 0 pts
    • Activation: Pending order was converted to MARKET and is now live

Three lines that cannot all be true. The order was a SELL LIMIT at 4037.48;
price never reached it, which is exactly why the near-miss logic converted it
to a market order. It filled at 4031.76 -- 57 points away.

The card showed the PLANNED limit as if it were the fill, and then claimed
the distance to activation was zero. Read together they describe an order
that filled at its limit. Nothing in the message revealed the 57 points of
slippage that moved the whole trade.

WHY
---
`send_trade_events` reads `trade["entry_price"]`, and `trade` is the row as
it was BEFORE this cycle's write. The send happens at
open_trades_manager.py:543 and the database update at :566 -- deliberately,
so a Supabase failure cannot swallow the notification. On the fill cycle that
ordering means the row still holds the plan while `updates` holds the truth.

Every later card reads the same field after the row is written, so the two
agree from then on. Only the activation card was ever wrong, and only about
the one number that mattered most.

THE FIX
-------
Prefer `updates["entry_price"]`, and when it differs from the plan, say so
and quote the distance. "Distance to activation" is suppressed once the order
has filled: it is zero by construction on that cycle and means nothing.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.telegram_bot import TelegramService  # noqa: E402
from utils.helpers import load_config  # noqa: E402

SYMBOL = "XAU/USD"
PLANNED_ENTRY = 4037.48
ACTUAL_FILL = 4031.76
STOP = 4077.48


def _service():
    service = TelegramService.__new__(TelegramService)
    service.config = load_config()
    service.enabled = True
    service._sent = []
    service.send_message = lambda msg, **kw: (service._sent.append(msg), True)[1]
    return service


def _card(*, events, updates, trade_overrides=None, evaluation_overrides=None) -> str:
    service = _service()
    trade = {
        "id": "TRADE_20260803_141059_572842_2f72579f",
        "symbol": SYMBOL, "type": "SELL", "status": "PENDING",
        "entry_price": PLANNED_ENTRY, "stop_loss": STOP,
        "tp1": 3987.48, "tp2": 3947.48,
    }
    trade.update(trade_overrides or {})
    evaluation = {
        "old_status": "PENDING", "new_status": "OPEN", "pnl_points": 0.0,
        "events": events, "pending_distance_points": 0.0, "hours_open": 0.0,
        "progress_to_tp1": 0.0, "updates": updates,
    }
    evaluation.update(evaluation_overrides or {})
    service.send_trade_events(trade, events, ACTUAL_FILL, 0.0, evaluation)
    return re.sub("<[^>]+>", "", service._sent[0] if service._sent else "")


_CONVERSION_UPDATES = {
    "status": "OPEN", "entry_price": ACTUAL_FILL, "stop_loss": 4071.76,
    "activation_reason": "Near-miss market conversion",
}


# ── the incident ────────────────────────────────────────────────────────────

def test_the_card_shows_the_fill_not_the_plan() -> None:
    card = _card(events=["ORDER_FILLED"], updates=_CONVERSION_UPDATES)
    assert f"Entry: {ACTUAL_FILL}" in card, (
        "the order filled at 4031.76; showing 4037.48 describes a fill that "
        "never happened"
    )


def test_the_card_names_the_planned_price_and_the_gap() -> None:
    card = _card(events=["ORDER_FILLED"], updates=_CONVERSION_UPDATES)
    assert f"planned {PLANNED_ENTRY}" in card
    assert "57 pts away" in card, (
        "the slippage is the whole story of this fill and must be stated, "
        "not left for the reader to subtract"
    )


def test_distance_to_activation_is_dropped_once_filled() -> None:
    card = _card(events=["ORDER_FILLED"], updates=_CONVERSION_UPDATES)
    assert "Distance to activation" not in card, (
        "on the fill cycle it is 0 by construction; printed beside a "
        "conversion 57 pts away it reads as 'price reached the entry'"
    )


# ── the honest cases must not be spoiled ────────────────────────────────────

def test_an_exact_fill_prints_no_slippage_note() -> None:
    """A limit that filled at its price says nothing extra."""
    card = _card(
        events=["ORDER_FILLED"],
        updates={"status": "OPEN", "entry_price": PLANNED_ENTRY},
    )
    assert f"Entry: {PLANNED_ENTRY}" in card
    assert "planned" not in card
    assert "pts away" not in card


def test_a_still_pending_order_keeps_its_distance_line() -> None:
    """The line is useful while the order is genuinely waiting."""
    card = _card(
        events=["NEWS_HOLD"],
        updates={"status": "PENDING"},
        evaluation_overrides={"new_status": "PENDING", "pending_distance_points": 77.0},
    )
    assert "Distance to activation" in card
    assert "77 pts" in card


def test_a_card_without_an_entry_update_falls_back_to_the_row() -> None:
    """Later cards read the written row and must be unaffected."""
    card = _card(
        events=["TP1_HIT"],
        updates={"status": "TP1_HIT"},
        trade_overrides={"status": "OPEN", "entry_price": ACTUAL_FILL},
        evaluation_overrides={"old_status": "OPEN", "new_status": "TP1_HIT"},
    )
    assert f"Entry: {ACTUAL_FILL}" in card
    assert "planned" not in card


def test_a_buy_conversion_is_described_the_same_way() -> None:
    card = _card(
        events=["ORDER_FILLED"],
        updates={"status": "OPEN", "entry_price": 4006.24},
        trade_overrides={"type": "BUY", "entry_price": 4000.0},
    )
    assert "Entry: 4006.24" in card
    assert "planned 4000.0" in card
    assert "62 pts away" in card


def test_a_sub_cent_difference_is_not_reported_as_slippage() -> None:
    card = _card(
        events=["ORDER_FILLED"],
        updates={"status": "OPEN", "entry_price": PLANNED_ENTRY + 0.001},
    )
    assert "pts away" not in card


# ── fault injection ─────────────────────────────────────────────────────────

def test_fault_injection_reading_the_row_shows_the_stale_plan() -> None:
    """Reproduce the pre-fix expression and show it prints the wrong price.

    The send happens before the database write, so the row still holds the
    planned entry at this moment. That ordering is deliberate -- a failed
    write must not swallow the notification -- which is why the card has to
    read `updates`, not the row.
    """
    trade_row = {"entry_price": PLANNED_ENTRY}
    updates = {"entry_price": ACTUAL_FILL}

    old_value = trade_row.get("entry_price")
    new_value = updates.get("entry_price", trade_row.get("entry_price"))

    assert old_value == PLANNED_ENTRY, "the row is stale on the fill cycle"
    assert new_value == ACTUAL_FILL
    assert old_value != new_value

    card = _card(events=["ORDER_FILLED"], updates=_CONVERSION_UPDATES)
    assert f"Entry: {PLANNED_ENTRY}\n" not in card
