"""A clock rollover is not a reason to cancel a live pending order.

Background
----------
On 2026-07-29 a SELL LIMIT at 4019.38 was cancelled 42 minutes after it was
placed, with price 103 points away and nothing wrong with it:

    Distance to activation : 103 pts
    Waiting                : 0.7h
    TP1 Progress           : 0%
    Cancellation reason    : session changed:
                             London + New York Afternoon -> New York Evening

Every genuine staleness limit was comfortably satisfied -- 0.7h against a 6h
cap, 103 points against a 250-point excursion cap, 0% against a 60% path cap.
The order died because the wall clock crossed a session boundary.

Worse, the Telegram message said:

    "Pending order was cancelled because the market moved away from the
     map without filling it."

which is a fixed PLAN_STALE string. The market had not moved away. The
operator was told a false reason for a cancellation that should not have
happened.

Price then traded back to 4015 -- four points from the entry that no longer
existed.

The session check is removed. The three measured limits remain: an expired
plan, six hours of waiting, a 250-point excursion, or 60% of the target path
covered without a fill. Those describe a map the market has actually left
behind. A clock rollover does not.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.open_trades_manager import OpenTradesManager

CONFIG = {
    "symbol": "XAU/USD",
    "pending_freshness": {
        "enabled": True,
        "aging_after_hours": 2,
        "stale_after_hours": 6,
        "stale_after_excursion_points": 250,
        "stale_after_target_progress_pct": 60,
    },
}


def _pending(created_session: str, *, created_hours_ago: float = 0.7) -> dict:
    created = datetime.now(timezone.utc) - timedelta(hours=created_hours_ago)
    return {
        "id": "TRADE_20260729_152108_440161_a4911dee",
        "symbol": "XAU/USD",
        "type": "SELL",
        "status": "PENDING",
        "order_type": "SELL_LIMIT",
        "order_kind": "LIMIT",
        "entry_price": 4019.38,
        "stop_loss": 4059.38,
        "initial_stop_loss": 4059.38,
        "tp1": 3969.38,
        "tp2": 3929.38,
        "entry_time": created.isoformat(),
        "created_at": created.isoformat(),
        "updates_sent": [],
        "signal_snapshot": {
            "setup_context": {"pending_plan_role": "PRIMARY",
                              "selection_role": "PRIMARY"},
            "session_info": {"current_session": created_session},
            "session_plan": {
                "plan_expires_at":
                    (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),
            },
            # The manager reads its pending bookkeeping from
            # signal_snapshot.pending_runtime, not from a top-level key.
            "pending_runtime": {
                "creation_price": 4006.78,
                "created_session_label": created_session,
            },
        },
    }


def _evaluate(trade: dict, price: float, session: str) -> dict:
    manager = OpenTradesManager({**CONFIG, "session": {"current_session": session}})
    return manager.evaluate_trade(
        trade, price, now=datetime.now(timezone.utc),
        candle_high=price + 1.0, candle_low=price - 1.0,
    )


# ── The cancellation that should never have happened ───────────────────────

def test_session_rollover_does_not_cancel_a_healthy_pending() -> None:
    """The 2026-07-29 order: 0.7h old, 103 pts away, 0% progress.

    Failure injection: restoring the ``session changed`` branch makes this
    fail with status CANCELLED.
    """
    trade = _pending("London + New York Afternoon")
    result = _evaluate(trade, 4009.12, "New York Evening")

    status = str((result.get("updates") or {}).get("status") or trade["status"])
    assert status != "CANCELLED", (
        "a pending order 103 points away after 42 minutes must survive a "
        f"session rollover (reasons: {(result.get('updates') or {}).get('reasons')})"
    )


def test_session_rollover_reports_no_staleness_reason() -> None:
    """Nothing about the order is stale, so nothing should say it is."""
    trade = _pending("London + New York Afternoon")
    result = _evaluate(trade, 4009.12, "New York Evening")
    snapshot = (result.get("updates") or {}).get("signal_snapshot") or {}
    runtime = snapshot.get("pending_runtime") or {}

    assert not runtime.get("cancelled_as_stale"), (
        f"order marked stale on a clock rollover: {runtime.get('stale_cancel_reason')}"
    )


# ── Guards: the real limits must still cancel ──────────────────────────────

def test_long_wait_still_cancels() -> None:
    """Six hours without a fill is a genuinely stale map."""
    trade = _pending("New York Evening", created_hours_ago=7.0)
    result = _evaluate(trade, 4009.12, "New York Evening")

    updates = result.get("updates") or {}
    assert str(updates.get("status") or "").upper() == "CANCELLED"
    reasons = " ".join(str(x) for x in (updates.get("reasons") or []))
    assert "waiting too long" in reasons


def test_large_excursion_still_cancels() -> None:
    """A 250-point move away without a fill is the operator's own limit."""
    trade = _pending("New York Evening")
    trade["signal_snapshot"]["pending_runtime"]["max_excursion_points"] = 300.0
    result = _evaluate(trade, 3989.0, "New York Evening")

    updates = result.get("updates") or {}
    status = str(updates.get("status") or "").upper()
    reasons = " ".join(str(x) for x in (updates.get("reasons") or []))
    assert status == "CANCELLED" or "moved" in reasons, (
        f"a 300-point excursion must still retire the order (got {status})"
    )


def test_expired_plan_still_cancels() -> None:
    """An expired day map takes its orders with it."""
    trade = _pending("New York Evening")
    trade["signal_snapshot"]["session_plan"]["plan_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).isoformat()
    result = _evaluate(trade, 4009.12, "New York Evening")

    updates = result.get("updates") or {}
    assert str(updates.get("status") or "").upper() == "CANCELLED"
    reasons = " ".join(str(x) for x in (updates.get("reasons") or []))
    assert "expired" in reasons


def test_pending_still_fills_when_price_arrives() -> None:
    """Surviving the rollover must not break ordinary activation."""
    trade = _pending("London + New York Afternoon")
    result = _evaluate(trade, 4019.50, "New York Evening")

    events = result.get("events") or []
    status = str((result.get("updates") or {}).get("status") or "")
    assert "ORDER_FILLED" in events or status.upper() == "OPEN", (
        f"price reached the limit; the order must activate (events={events})"
    )
