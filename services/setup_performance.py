"""How has *this* pattern performed lately, in *these* conditions?

A discretionary analyst carries one advantage the system has never had: a
memory that is selective rather than statistical. They do not recall an
aggregate win rate -- they recall that this particular setup, in this session,
has failed the last three times, and they size down or stand aside.

The system already stores everything needed to answer that question. Every
trade row carries `setup_type`, `session_label` and `volatility_regime`, and
the learning service consumes them nightly to retune agent weights. But
nothing asks the question at the moment a signal is about to go out, so a
pattern can fail repeatedly in one session and still be taken at full
confidence the next morning.

This module answers it in a way that can only ever be conservative: it lowers
confidence on a demonstrably poor pattern and never raises it on a good one.
A memory that inflates conviction is how overfitting reaches production.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_WIN_STATUSES = {"TP2_HIT"}
_LOSS_STATUSES = {"SL_HIT"}
_CLOSED_STATUSES = _WIN_STATUSES | _LOSS_STATUSES | {"BE_HIT", "MANUAL_CLOSE", "EXPIRED", "TP1_HIT"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _outcome(trade: Dict[str, Any]) -> str | None:
    """Classify a closed trade by realised points, not by status label alone.

    A trailed SL_HIT can be profitable, so the label on its own misreports the
    result -- which would poison the very memory this module provides.
    """
    status = str(trade.get("status") or "").upper()
    if status not in _CLOSED_STATUSES:
        return None
    pnl = _f(trade.get("final_pnl"), _f(trade.get("current_pnl_points")))
    if pnl > 0:
        return "WIN"
    if pnl < 0:
        return "LOSS"
    if status in _WIN_STATUSES:
        return "WIN"
    if status in _LOSS_STATUSES:
        return "LOSS"
    return "FLAT"


class SetupPerformanceService:
    """Scores a pending signal against the recent history of its own pattern."""

    def __init__(self, database: Any, config: Dict[str, Any] | None = None) -> None:
        self.database = database
        self.config = config or {}
        cfg = (self.config.get("setup_performance") or {}) if isinstance(self.config, dict) else {}
        self.enabled = bool(cfg.get("enabled", True))
        self.lookback_trades = int(cfg.get("lookback_trades", 60) or 60)
        self.min_samples = int(cfg.get("min_samples", 4) or 4)
        self.poor_win_rate = float(cfg.get("poor_win_rate_pct", 40.0) or 40.0)
        self.max_penalty = float(cfg.get("max_confidence_penalty", 12.0) or 12.0)
        self.veto_win_rate = float(cfg.get("veto_win_rate_pct", 0.0) or 0.0)
        self.veto_min_samples = int(cfg.get("veto_min_samples", 6) or 6)

    # -- lookup ----------------------------------------------------------

    def review(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """Return a verdict for this signal's pattern. Never raises."""
        base = {
            "enabled": self.enabled,
            "matched": 0,
            "win_rate_pct": None,
            "confidence_penalty": 0.0,
            "veto": False,
            "reason": None,
        }
        if not self.enabled:
            return base

        setup_type = str(decision.get("setup_type") or "").upper()
        if not setup_type:
            setup = decision.get("setup_context") or {}
            setup_type = str((setup or {}).get("setup_type") or "").upper()
        if not setup_type:
            base["reason"] = "signal carries no setup type to look up"
            return base

        session = str(
            (decision.get("session_info") or {}).get("current_session")
            or (decision.get("session_info") or {}).get("session")
            or ""
        ).strip()

        try:
            trades = self.database.get_recent_trades(limit=self.lookback_trades)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Setup performance lookup failed: %s", exc)
            base["reason"] = "history unavailable"
            return base

        matches = self._matching(trades, setup_type=setup_type, session=session)
        # Fall back to the pattern across all sessions rather than reporting
        # nothing: a thin session sample is common and still informative.
        scope = "session"
        if len(matches) < self.min_samples and session:
            matches = self._matching(trades, setup_type=setup_type, session="")
            scope = "all sessions"

        outcomes = [o for o in (_outcome(t) for t in matches) if o]
        decided = [o for o in outcomes if o in {"WIN", "LOSS"}]
        base["matched"] = len(decided)
        base["scope"] = scope
        base["setup_type"] = setup_type

        if len(decided) < self.min_samples:
            base["reason"] = (
                f"only {len(decided)} closed {setup_type} trades on record "
                f"(need {self.min_samples})"
            )
            return base

        wins = len([o for o in decided if o == "WIN"])
        win_rate = round((wins / len(decided)) * 100.0, 1)
        base["win_rate_pct"] = win_rate
        base["wins"] = wins
        base["losses"] = len(decided) - wins

        if win_rate >= self.poor_win_rate:
            base["reason"] = (
                f"{setup_type} is {win_rate:.0f}% over its last {len(decided)} "
                f"closed trades ({scope})"
            )
            return base

        # Scale the penalty by how far below the bar the pattern sits, so a
        # marginal record costs less than a consistently losing one.
        shortfall = (self.poor_win_rate - win_rate) / max(self.poor_win_rate, 1.0)
        base["confidence_penalty"] = round(min(self.max_penalty, self.max_penalty * shortfall), 1)
        base["reason"] = (
            f"{setup_type} has won {win_rate:.0f}% of its last {len(decided)} "
            f"closed trades ({scope}), below the {self.poor_win_rate:.0f}% bar"
        )

        if (
            self.veto_win_rate > 0
            and win_rate <= self.veto_win_rate
            and len(decided) >= self.veto_min_samples
        ):
            base["veto"] = True
            base["reason"] = (
                f"{setup_type} has won {win_rate:.0f}% of its last {len(decided)} "
                f"closed trades ({scope}) — below the {self.veto_win_rate:.0f}% veto line"
            )

        return base

    # -- internals -------------------------------------------------------

    def _matching(self, trades: List[Dict[str, Any]], *, setup_type: str, session: str) -> List[Dict[str, Any]]:
        out = []
        for trade in trades or []:
            if str(trade.get("setup_type") or "").upper() != setup_type:
                continue
            if session and str(trade.get("session_label") or "").strip() != session:
                continue
            out.append(trade)
        return out
