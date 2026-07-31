"""The thesis exit must ask the agents before it closes a trade.

Both cases below are real, taken from the live book on 2026-07-29/30. They
produced a BYTE-IDENTICAL candle trigger, and opposite correct answers:

    a4911dee  SELL 4019.38  exit 4028.02  -86.4 pts
              agents: 2 qualified against the SELL, 0 defending
              price then ran to 4079.33, through the 4059.38 stop
              -> exiting was RIGHT; it saved ~314 points

    5f383b5c  SELL 4046.02  exit 4049.94  -39.2 pts
              agents: Classical 71, SMC 90, Multi-Timeframe 83 all SELL
              the planner republished 4045.64-4049.88 as an A+ SELL map
              price fell to 4039.28
              -> exiting was WRONG; holding was worth about +67

No threshold on the candle rule can separate them, because the trigger is
identical. Only the agent book can. These tests pin that behaviour.

Fault injection: delete the `verdict == "DEFEND"` branch in
`_thesis_exit_review` and `test_agents_defending_the_trade_veto_the_exit`
fails; delete the `partial_close` guard and the escalation test fails with
three scale-outs instead of one.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager

SYMBOL = "XAU/USD"


def _config() -> dict:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _book(**agents) -> dict:
    """Build an agent_details map: name=(direction, confidence)."""
    return {
        name: {"label": name, "direction": d, "confidence": c, "signals": []}
        for name, (d, c) in agents.items()
    }


def _bullish_reclaim_candles() -> list[dict]:
    """The exact shape that fires _continuation_trigger_against_trade."""
    return [
        {"time": "2026-07-30T06:30:00+00:00", "open": 4047.0, "high": 4048.2,
         "low": 4045.5, "close": 4046.5},
        {"time": "2026-07-30T06:45:00+00:00", "open": 4046.6, "high": 4050.2,
         "low": 4046.4, "close": 4049.94},
    ]


def _review(manager, *, book, partial_close=False, entry=4046.02):
    return manager._thesis_exit_review(
        {"id": "T", "type": "SELL", "entry_price": entry, "symbol": SYMBOL},
        trade_type="SELL", symbol=SYMBOL, current_price=4049.94,
        recent_candles=_bullish_reclaim_candles(), hours_open=0.5,
        pnl_points=-39.2, max_favorable_excursion=0.0, tp1=3996.0,
        entry=entry, partial_close=partial_close, agent_details=book,
    )


# ── the candle trigger is identical in both live cases ─────────────────────

def test_both_live_trades_produced_the_same_candle_trigger() -> None:
    manager = OpenTradesManager(_config())
    a = manager._continuation_trigger_against_trade("SELL", [
        {"high": 4026.0, "close": 4024.0, "open": 4021.0, "low": 4020.0},
        {"high": 4028.5, "close": 4028.02, "open": 4025.0, "low": 4024.5},
    ], SYMBOL)
    b = manager._continuation_trigger_against_trade("SELL", _bullish_reclaim_candles(), SYMBOL)
    assert a == b, "the two live trades must be indistinguishable to the candle rule"
    assert a is not None


# ── 5f383b5c: agents defend -> do not exit ─────────────────────────────────

def test_agents_defending_the_trade_veto_the_exit() -> None:
    manager = OpenTradesManager(_config())
    verdict = _review(manager, book=_book(
        technical=("BUY", 92.0),
        classical=("SELL", 71.0),
        smc=("SELL", 90.0),
        price_action=("WAIT", 29.0),
        multitimeframe=("SELL", 83.0),
    ))
    assert verdict["exit_now"] is False
    assert verdict["scale_out"] is False
    assert verdict["kind"] == "OPPOSITE_CONTINUATION_VETOED_BY_AGENTS"
    assert verdict["agent_vote"]["verdict"] == "DEFEND"
    assert set(verdict["agent_vote"]["defenders"]) == {"classical", "smc", "multitimeframe"}


# ── a4911dee: agents confirm -> full exit ──────────────────────────────────

def test_agents_confirming_the_flip_still_exit_in_full() -> None:
    manager = OpenTradesManager(_config())
    verdict = _review(manager, book=_book(
        technical=("WAIT", 39.6),
        classical=("WAIT", 29.0),
        smc=("BUY", 82.0),
        price_action=("BUY", 79.0),
        multitimeframe=("WAIT", 48.0),
    ))
    assert verdict["exit_now"] is True
    assert verdict["kind"] == "OPPOSITE_CONTINUATION"
    assert verdict["agent_vote"]["verdict"] == "CONFIRM"
    assert "confirmed by 2 qualified agents" in verdict["reason"]


# ── silent book -> scale out, per the operator's choice (أ) ────────────────

def test_silent_agent_book_scales_out_instead_of_closing() -> None:
    manager = OpenTradesManager(_config())
    verdict = _review(manager, book=_book(
        technical=("BUY", 92.0),
        classical=("WAIT", 40.0),
        smc=("WAIT", 30.0),
        price_action=("WAIT", 29.0),
        multitimeframe=("WAIT", 48.0),
    ))
    assert verdict["exit_now"] is False
    assert verdict["scale_out"] is True
    assert verdict["scale_fraction"] == 0.5
    assert verdict["agent_vote"]["verdict"] == "SILENT"


# ── escalation: the same candle must not scale twice ───────────────────────

def test_repeated_candle_does_not_scale_the_position_again() -> None:
    manager = OpenTradesManager(_config())
    silent = _book(
        technical=("BUY", 92.0), classical=("WAIT", 40.0), smc=("WAIT", 30.0),
        price_action=("WAIT", 29.0), multitimeframe=("WAIT", 48.0),
    )
    first = _review(manager, book=silent, partial_close=False)
    assert first["scale_out"] is True

    # The candle keeps printing the same shape on the next cycles.
    for _ in range(3):
        again = _review(manager, book=silent, partial_close=True)
        assert again["scale_out"] is False, "a repeated candle is not new evidence"
        assert again["exit_now"] is False
        assert again["kind"] == "OPPOSITE_CONTINUATION_ALREADY_SCALED"


def test_agents_turning_against_a_scaled_trade_do_escalate() -> None:
    """Escalation is driven by a CHANGE in the book, not by the candle."""
    manager = OpenTradesManager(_config())
    verdict = _review(manager, partial_close=True, book=_book(
        technical=("BUY", 92.0),
        classical=("BUY", 74.0),
        smc=("WAIT", 30.0),
        price_action=("WAIT", 29.0),
        multitimeframe=("WAIT", 48.0),
    ))
    assert verdict["exit_now"] is True
    assert verdict["agent_vote"]["verdict"] == "CONFIRM"


# ── an absent book must not change legacy behaviour ────────────────────────

def test_missing_agent_book_keeps_the_legacy_full_exit() -> None:
    manager = OpenTradesManager(_config())
    for absent in (None, {}):
        verdict = _review(manager, book=absent)
        assert verdict["exit_now"] is True, "no book means no new evidence, so behave as before"
        assert verdict["kind"] == "OPPOSITE_CONTINUATION"


# ── real partial accounting (أ-1, option 1) ────────────────────────────────

def test_scale_out_books_the_closed_half_at_its_own_price() -> None:
    manager = OpenTradesManager(_config())
    # The age must be RELATIVE. A frozen created_at turns any test into a
    # time bomb: this one passed for a day, then crossed the 24h
    # expire_after_hours window and started reporting EXPIRED instead of the
    # scale-out it was written to check -- taking the whole CI barrier, and
    # therefore live analysis, down with it.
    opened = datetime.now(timezone.utc) - timedelta(minutes=30)
    trade = {
        "id": "T", "type": "SELL", "status": "OPEN", "symbol": SYMBOL,
        "entry_price": 4046.02, "stop_loss": 4086.02, "tp1": 3996.0,
        "tp2": 3970.0,
        "created_at": opened.isoformat(), "entry_time": opened.isoformat(),
        "updates_sent": [],
    }
    silent = _book(
        technical=("BUY", 92.0), classical=("WAIT", 40.0), smc=("WAIT", 30.0),
        price_action=("WAIT", 29.0), multitimeframe=("WAIT", 48.0),
    )
    res = manager.evaluate_trade(
        trade, 4049.94, candle_high=4050.2, candle_low=4046.4,
        recent_candles=_bullish_reclaim_candles(), agent_details=silent,
    )
    assert "THESIS_SCALE_OUT" in res["events"]
    updates = res["updates"]
    assert updates["closed_fraction"] == 0.5
    # -39.2 pts on the half that was closed
    assert updates["realized_pnl_points"] == -19.6
    assert updates["scale_out_price"] == 4049.94
    # the remaining half is protected at entry
    assert updates["sl_moved_to_entry"] is True
    assert updates["stop_loss"] == 4046.02


def test_final_pnl_is_composite_after_a_scale_out() -> None:
    """Half booked at -39.2, the rest closed at +100 -> +30.4, not +100."""
    manager = OpenTradesManager(_config())
    opened = datetime.now(timezone.utc) - timedelta(minutes=30)
    trade = {
        "id": "T", "type": "SELL", "status": "PARTIAL", "symbol": SYMBOL,
        "entry_price": 4046.02, "stop_loss": 4046.02, "tp1": 3996.0,
        "tp2": 4036.02,
        "created_at": opened.isoformat(), "entry_time": opened.isoformat(),
        "updates_sent": ["THESIS_SCALE_OUT"], "partial_close": True,
        "sl_moved_to_entry": True,
        "closed_fraction": 0.5, "realized_pnl_points": -19.6,
    }
    # TP2 at 4036.02 = +100 pts on the remaining half.
    res = manager.evaluate_trade(
        trade, 4036.02, candle_high=4037.0, candle_low=4035.5,
        recent_candles=None,
    )
    assert res["new_status"] == "TP2_HIT"
    # -19.6 already realized + 100 * 0.5 remaining = +30.4
    assert res["updates"]["final_pnl"] == 30.4
    assert res["updates"]["closed_fraction"] == 1.0


# ── the message must state the real reduction ──────────────────────────────

def test_scale_out_message_states_the_actual_closed_fraction() -> None:
    """A 'scale-out' notice that names no size describes a reduction that,
    until this change, never happened."""
    from services.telegram_bot import TelegramService

    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    captured: dict = {}

    def _fake_send(text: str, urgent: bool = False, **_k) -> bool:
        captured["text"] = text
        return True

    service.send_message = _fake_send  # type: ignore[assignment]

    trade = {
        "id": "TRADE_20260730_063100_271505_5f383b5c", "symbol": SYMBOL,
        "type": "SELL", "entry_price": 4046.02, "status": "PARTIAL",
    }
    evaluation = {
        "old_status": "OPEN", "new_status": "PARTIAL",
        "events": ["THESIS_SCALE_OUT", "MOVE_SL_TO_BE"],
        "pnl_points": -39.2,
        "updates": {
            "reasons": ["Automatic thesis scale-out: bullish continuation "
                        "reclaimed the breakdown, unconfirmed by the agent book"],
            "closed_fraction": 0.5,
            "realized_pnl_points": -19.6,
            "scale_out_price": 4049.94,
        },
    }
    service.send_trade_events(
        trade, evaluation["events"], 4049.94, -39.2, evaluation
    )
    text = captured["text"]
    assert "50% of the position" in text
    assert "4049.94" in text
    assert "-19.6 pts" in text
    assert "still open" in text
