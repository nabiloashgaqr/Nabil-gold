"""One definition of "how did we do", shared by every surface.

WHY THIS MODULE EXISTS
----------------------
The Telegram dashboard card and the web dashboard each computed their own
performance summary, in different languages, over different sets of trades.
They disagreed, and the operator saw it:

    Telegram card          web API (dashboard/api/dashboard.js)
    ─────────────          ────────────────────────────────────
    Trades      80         closedTrades only
    W 34 / L 9  (= 43)     wins/losses from pnl sign only
    Net +5111.9            net over closed trades only

Three separate divergences produced that gap.

1. TRADE SET
   ``services.dashboard.summarize_trades`` summed over EVERY row it was
   handed, including PENDING and CANCELLED orders that were never filled and
   OPEN trades whose profit is still floating. The web API filtered to
   finished outcomes first. So "Trades: 80" counted rows that can never have
   a result, which is why W + L = 43 did not add up to it: 37 rows were in
   neither column because they had no outcome to be in.

2. NET AND PROFIT FACTOR
   Following from (1), the card's net mixed realised profit with unrealised
   floating PnL. Those are different quantities and must not be added.

3. THE DEFINITION OF A WIN
   The card counted a trade as a win when ``status == "TP2_HIT"`` OR
   ``pnl > 0``. The web used ``pnl > 0`` alone. The status test is the weaker
   one: a TP2_HIT row whose recorded PnL is zero or negative would still be
   counted as a win, which flatters the record.

   ``pnl > 0`` is also the more truthful test in the other direction. SL_HIT
   is not always a loss — once the stop has been trailed into profit, a
   stop-out is a winning trade — and the sign of the realised PnL captures
   that correctly while the status does not.

WHAT THIS MODULE DOES
---------------------
Defines the summary once, over CLOSED trades only, with a win being
``pnl > 0``. Open and pending rows are reported separately, with their
floating PnL kept in its own field so nobody can accidentally add it to the
realised figure.

Operator decision, 2026-08-03: closed-only, ``pnl > 0``, one shared source.

This module holds no I/O and no configuration. It is pure arithmetic over
rows, so both the Python card and any other consumer can rely on it, and the
JavaScript API can be checked against it.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

#: Rows that are still working. Their PnL floats and is not banked.
OPEN_STATUSES = {"OPEN", "PARTIAL", "TP1_HIT"}

#: Resting orders that were never filled. They have no outcome at all.
PENDING_STATUSES = {"PENDING"}

#: Orders that expired or were cancelled before filling. Also no outcome.
UNFILLED_STATUSES = {"CANCELLED", "CANCELED", "REJECTED"}

#: Finished trades. Mirrors OUTCOME_STATUSES in dashboard/api/dashboard.js;
#: the two lists must stay in step, which test_dashboard_numbers_agree pins.
CLOSED_STATUSES = {
    "TP2_HIT", "SL_HIT", "BE_HIT", "EXPIRED",
    "THESIS_EXIT", "MANUAL_CLOSE", "CLOSED",
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default  # reject NaN


def _status(trade: Dict[str, Any]) -> str:
    return str(trade.get("status") or "").upper()


def pnl_of(trade: Dict[str, Any]) -> float:
    """Realised or floating PnL in points, whichever the row carries.

    Mirrors the key order the web API uses so a row cannot resolve to two
    different numbers depending on which surface reads it.
    """
    for key in ("final_pnl", "current_pnl_points", "current_pnl", "pnl"):
        if trade.get(key) is not None:
            return _f(trade.get(key))
    return 0.0


def _side(trade: Dict[str, Any]) -> str:
    return str(
        trade.get("type") or trade.get("side") or trade.get("trade_type") or ""
    ).upper()


def is_closed(trade: Dict[str, Any]) -> bool:
    status = _status(trade)
    if status in CLOSED_STATUSES:
        return True
    # An unknown status is only treated as closed when it is definitely not
    # one of the live or unfilled states, so a new status string added to the
    # database cannot silently vanish from the totals.
    return status not in (OPEN_STATUSES | PENDING_STATUSES | UNFILLED_STATUSES)


def summarize(trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Performance over CLOSED trades, with live rows reported separately."""
    rows: List[Dict[str, Any]] = [t for t in trades if isinstance(t, dict)]

    closed = [t for t in rows if is_closed(t)]
    live = [t for t in rows if _status(t) in OPEN_STATUSES]
    pending = [t for t in rows if _status(t) in PENDING_STATUSES]
    unfilled = [t for t in rows if _status(t) in UNFILLED_STATUSES]

    wins = [t for t in closed if pnl_of(t) > 0]
    losses = [t for t in closed if pnl_of(t) < 0]
    breakeven = [t for t in closed if pnl_of(t) == 0]

    net = sum(pnl_of(t) for t in closed)
    gross_profit = sum(pnl_of(t) for t in wins)
    gross_loss = abs(sum(pnl_of(t) for t in losses))

    decisive = len(wins) + len(losses)
    buys = [t for t in closed if _side(t) == "BUY"]
    sells = [t for t in closed if _side(t) == "SELL"]

    confidences = [_f(t.get("confidence")) for t in closed if t.get("confidence") is not None]

    return {
        # Scope, stated explicitly so a reader never has to guess.
        "closed": len(closed),
        "open": len(live),
        "pending": len(pending),
        "unfilled": len(unfilled),
        "total_rows": len(rows),

        # Outcomes. wins + losses + breakeven == closed, always.
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": round(len(wins) / decisive * 100, 2) if decisive else 0.0,

        # Realised only. Floating PnL is kept apart on purpose.
        "net_points": round(net, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": (
            round(gross_profit / gross_loss, 2) if gross_loss > 0
            else (None if gross_profit > 0 else 0.0)
        ),
        "open_floating_points": round(sum(pnl_of(t) for t in live), 2),

        "buy_count": len(buys),
        "sell_count": len(sells),
        "buy_net": round(sum(pnl_of(t) for t in buys), 2),
        "sell_net": round(sum(pnl_of(t) for t in sells), 2),
        "avg_confidence": (
            round(sum(confidences) / len(confidences), 2) if confidences else 0.0
        ),
    }
