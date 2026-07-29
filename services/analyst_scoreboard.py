"""Score the system against the manual analyst in captured points.

Why this exists
---------------
``AnalystDistillationService`` measures whether the system *saw the same
chart* as the analyst: direction overlap, setup type, POI type, entry
proximity. That is a real signal and it stays.

It is not a measure of who was right. On 2026-07-29 the system's SMC read of
the chart was defensible -- it found the sweep, the order block and the
bearish structure -- and it still published a BUY that lost 198 points while
the analyst sold 4040 into 4009 for +310. An overlap-based scoreboard scores
that day as a partial success. A points-based one scores it as a 512-point
defeat, which is what it was.

Scoring rules, applied identically to both sides:

  - Marked in points, using the instrument's own point scale.
  - An analyst plan is scored from his stated entry, invalidation and
    targets against the day's actual high and low: reached target, or
    stopped, or never triggered.
  - A system trade is scored from its real fill and close. Open trades are
    marked to the live price rather than skipped.
  - Not trading a winning day scores zero, not "no result". Standing still
    through a 310-point move is an outcome and the scoreboard says so.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from utils.instruments import price_to_points

logger = logging.getLogger(__name__)


class AnalystScoreboardService:
    """Head-to-head comparison in points captured."""

    OPEN_STATUSES = {"OPEN", "PARTIAL", "TP1_HIT"}
    TRADED_DECISIONS = {"TRADE", "TAKEN", "EXECUTED"}

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        cfg = (self.config.get("analyst_scoreboard") or {}) if isinstance(self.config, dict) else {}
        self.enabled = bool(cfg.get("enabled", True))
        self.symbol = str(self.config.get("symbol", "XAU/USD"))
        # A gap inside this band is noise, not a verdict.
        self.tie_band_points = self._f(cfg.get("tie_band_points", 0.0), 0.0)

    # ── public API ─────────────────────────────────────────────────────────

    def score_day(
        self,
        *,
        labels: List[Dict[str, Any]],
        trades: List[Dict[str, Any]],
        market_high: float,
        market_low: float,
        symbol: str | None = None,
        current_price: float | None = None,
    ) -> Dict[str, Any]:
        """Return the head-to-head result for one symbol over one day."""
        symbol = str(symbol or self.symbol)

        analyst = self._score_analyst(labels, market_high, market_low, symbol)
        system = self._score_system(trades, symbol, current_price)

        gap = round(system["points"] - analyst["points"], 1)
        if abs(gap) <= self.tie_band_points:
            verdict = "TIE"
        elif gap > 0:
            verdict = "SYSTEM_AHEAD"
        else:
            verdict = "ANALYST_AHEAD"

        return {
            "symbol": symbol,
            "analyst_points": analyst["points"],
            "analyst_traded": analyst["traded"],
            "analyst_detail": analyst["detail"],
            "system_points": system["points"],
            "system_traded": system["traded"],
            "system_detail": system["detail"],
            "gap_points": gap,
            "verdict": verdict,
            "summary": self._summary(analyst, system, gap, verdict),
        }

    def build_report_lines(self, board: Dict[str, Any]) -> List[str]:
        """Compact lines for the daily report / Telegram."""
        verdict_label = {
            "SYSTEM_AHEAD": "System ahead",
            "ANALYST_AHEAD": "Analyst ahead",
            "TIE": "Level",
        }.get(str(board.get("verdict")), "Unknown")
        lines = [
            f"Head-to-head ({board.get('symbol')}): {verdict_label} "
            f"by {abs(self._f(board.get('gap_points'), 0.0)):.0f} pts",
            f"Analyst {self._f(board.get('analyst_points'), 0.0):+.0f} pts | "
            f"System {self._f(board.get('system_points'), 0.0):+.0f} pts",
        ]
        if not board.get("system_traded"):
            lines.append("System placed no order on this symbol today.")
        return lines

    # ── analyst side ───────────────────────────────────────────────────────

    def _score_analyst(
        self, labels: List[Dict[str, Any]], high: float, low: float, symbol: str
    ) -> Dict[str, Any]:
        total = 0.0
        traded = False
        detail: List[Dict[str, Any]] = []

        for label in labels or []:
            if not isinstance(label, dict):
                continue
            if str(label.get("symbol") or self.symbol) != symbol:
                continue
            decision = str(label.get("trade_decision") or "TRADE").upper()
            if decision not in self.TRADED_DECISIONS:
                continue

            side = str(label.get("bias") or label.get("direction") or "").upper()
            if side not in {"BUY", "SELL"}:
                continue
            entry = self._f(label.get("intended_entry"), 0.0)
            if entry <= 0:
                continue

            stop = self._f(label.get("invalidation"), 0.0)
            target = self._f(label.get("tp2"), 0.0) or self._f(label.get("tp1"), 0.0)
            outcome, exit_price = self._resolve_plan_outcome(
                side=side, entry=entry, stop=stop, target=target, high=high, low=low
            )
            if outcome == "NOT_TRIGGERED":
                detail.append({"label_id": label.get("id"), "outcome": outcome,
                               "points": 0.0})
                continue

            traded = True
            points = self._directional_points(side, entry, exit_price, symbol)
            total += points
            detail.append({
                "label_id": label.get("id"), "side": side, "entry": entry,
                "exit": exit_price, "outcome": outcome, "points": points,
            })

        return {"points": round(total, 1), "traded": traded, "detail": detail}

    def _resolve_plan_outcome(
        self, *, side: str, entry: float, stop: float, target: float,
        high: float, low: float,
    ) -> tuple[str, float]:
        """Decide whether a drawn plan triggered, hit its target, or stopped.

        ``high`` and ``low`` must be the range that occurred *after* the plan
        was published, not the whole session's range. Sequencing is not
        recoverable from a single high/low pair, and using the full day's
        extremes silently rewrites history: on 2026-07-29 the analyst's stop
        sat at 4047.5, which is the sweep high his thesis was built on. That
        sweep happened before he entered -- it is why he entered -- so scoring
        him against the day high stopped him out on his own setup and turned
        a +314 call into -75. Callers pass post-entry extremes.

        Deliberately conservative for the analyst: if the post-entry range
        still covers both his stop and his target, the stop is assumed to
        have come first. The scoreboard must never flatter the side it is
        measuring against.
        """
        if high <= 0 or low <= 0:
            return "NOT_TRIGGERED", entry

        triggered = low <= entry <= high
        if not triggered:
            return "NOT_TRIGGERED", entry

        if side == "SELL":
            stopped = stop > 0 and high >= stop
            reached = target > 0 and low <= target
        else:
            stopped = stop > 0 and low <= stop
            reached = target > 0 and high >= target

        if stopped:
            return "STOPPED", stop
        if reached:
            return "TARGET", target
        return "OPEN_AT_CLOSE", (low if side == "SELL" else high)

    # ── system side ────────────────────────────────────────────────────────

    def _score_system(
        self, trades: List[Dict[str, Any]], symbol: str, current_price: float | None
    ) -> Dict[str, Any]:
        total = 0.0
        traded = False
        detail: List[Dict[str, Any]] = []

        for trade in trades or []:
            if not isinstance(trade, dict):
                continue
            if str(trade.get("symbol") or "") != symbol:
                continue
            side = str(trade.get("type") or trade.get("side") or "").upper()
            if side not in {"BUY", "SELL"}:
                continue
            entry = self._f(trade.get("entry_price"), 0.0)
            if entry <= 0:
                continue

            status = str(trade.get("status") or "").upper()
            if status == "PENDING":
                # Never filled: no points either way, but it is not a trade.
                continue

            exit_price = self._f(trade.get("close_price"), 0.0)
            if exit_price <= 0:
                # Still running: mark to market rather than drop it, so an
                # open loser cannot hide from the scoreboard.
                exit_price = self._f(current_price, 0.0)
                if exit_price <= 0:
                    continue

            traded = True
            points = self._directional_points(side, entry, exit_price, symbol)
            total += points
            detail.append({
                "trade_id": trade.get("id"), "side": side, "entry": entry,
                "exit": exit_price, "status": status, "points": points,
            })

        return {"points": round(total, 1), "traded": traded, "detail": detail}

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _directional_points(side: str, entry: float, exit_price: float, symbol: str) -> float:
        raw = (exit_price - entry) if side == "BUY" else (entry - exit_price)
        return round(price_to_points(raw, symbol=symbol), 1)

    def _summary(
        self, analyst: Dict[str, Any], system: Dict[str, Any],
        gap: float, verdict: str,
    ) -> str:
        if not system["traded"] and analyst["traded"]:
            return (
                f"Analyst captured {analyst['points']:+.0f} pts; the system "
                f"placed no order and captured 0. Gap {gap:+.0f} pts."
            )
        if not analyst["traded"] and system["traded"]:
            return (
                f"No analyst plan triggered; the system captured "
                f"{system['points']:+.0f} pts. Gap {gap:+.0f} pts."
            )
        if verdict == "TIE":
            return "Analyst and system finished level on captured points."
        return (
            f"Analyst {analyst['points']:+.0f} pts vs system "
            f"{system['points']:+.0f} pts. Gap {gap:+.0f} pts."
        )

    @staticmethod
    def _f(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
