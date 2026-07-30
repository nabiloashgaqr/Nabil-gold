"""Replay candles through the real trade manager to measure exit behaviour.

Why this is not part of services/backtesting.py
-----------------------------------------------
The existing backtester models exactly two exits::

    if stop_loss and low <= stop_loss:  -> SL_HIT
    if tp2 and high >= tp2:             -> TP2_HIT

There is no trailing stop, no breakeven, and no partial close anywhere in it.
That is fine for what it was built for -- judging whether a *signal* would
have worked -- but it means the only measurement tool in the repo is blind to
every mechanism that decides how much of a winning move is actually kept.

Measuring an exit change against a model that cannot express exits would
produce numbers that never move, and any conclusion drawn from them would be
an artefact of the harness. So this replays the production code instead:
``OpenTradesManager.evaluate_trade`` is called candle by candle, exactly as
the live update loop calls it, and the resulting stop movements, events and
final points are recorded.

This module measures. It changes no behaviour and is not imported by the
live analysis path.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from agents.open_trades_manager import OpenTradesManager
from utils.instruments import price_to_points

logger = logging.getLogger(__name__)


class ExitReplayHarness:
    """Drive a trade through a candle series using the real manager."""

    CLOSING_EVENTS = {"TP2_HIT", "SL_HIT", "TRAILING_SL_HIT", "BE_HIT",
                      "THESIS_EXIT", "MANUAL_CLOSE", "EXPIRED"}

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}

    # ── single replay ──────────────────────────────────────────────────────

    def replay(
        self,
        trade: Dict[str, Any],
        candles: List[Dict[str, Any]],
        config: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Run one trade through one candle series.

        The caller's ``trade`` and ``config`` are never mutated: a measurement
        that alters its subject is not a measurement.
        """
        active_config = deepcopy(config if config is not None else self.config)
        symbol = str(trade.get("symbol") or active_config.get("symbol", "XAU/USD"))
        entry = self._f(trade.get("entry_price"), 0.0)
        side = str(trade.get("type") or trade.get("side") or "").upper()

        blank = {
            "filled": False, "points": 0.0, "exit_price": 0.0,
            "events": [], "stop_history": [], "trailing_moves": 0,
            "breakeven_engaged": False, "final_stop": self._f(trade.get("stop_loss"), 0.0),
            "candles_held": 0, "closing_event": None,
        }
        if not candles or entry <= 0 or side not in {"BUY", "SELL"}:
            return blank

        manager = OpenTradesManager(active_config)
        state = deepcopy(trade)
        state.setdefault("updates_sent", [])

        events_seen: List[str] = []
        stop_history: List[Dict[str, Any]] = []
        trailing_moves = 0
        breakeven_engaged = False
        closing_event: str | None = None
        exit_price = 0.0
        held = 0

        start = self._parse_time(state.get("entry_time")) or datetime.now(timezone.utc)

        for index, candle in enumerate(candles):
            high = self._f(candle.get("high"), 0.0)
            low = self._f(candle.get("low"), 0.0)
            close = self._f(candle.get("close"), (high + low) / 2 if high and low else 0.0)
            if high <= 0 or low <= 0:
                continue
            held += 1
            now = self._parse_time(candle.get("time")) or (start + timedelta(minutes=5 * (index + 1)))

            try:
                evaluation = manager.evaluate_trade(
                    state, close, now=now, candle_high=high, candle_low=low,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Exit replay aborted at candle %s: %s", index, exc)
                break

            previous_stop = self._f(state.get("stop_loss"), 0.0)
            updates = evaluation.get("updates") or {}
            events = list(evaluation.get("events") or [])
            events_seen.extend(events)

            if "MOVE_SL_TO_BE" in events:
                breakeven_engaged = True
            if "TRAILING_SL_UPDATED" in events:
                trailing_moves += 1

            # Apply the manager's own updates, exactly as the live loop does.
            for key, value in updates.items():
                state[key] = value
            if updates.get("status"):
                state["status"] = updates["status"]
            state["updates_sent"] = list(
                dict.fromkeys(list(state.get("updates_sent") or []) + events)
            )

            new_stop = self._f(state.get("stop_loss"), previous_stop)
            if abs(new_stop - previous_stop) > 1e-9:
                stop_history.append({
                    "candle": index, "from": round(previous_stop, 2),
                    "to": round(new_stop, 2),
                    "events": [e for e in events
                               if e in {"MOVE_SL_TO_BE", "TRAILING_SL_UPDATED"}],
                })

            closing = [e for e in events if e in self.CLOSING_EVENTS]
            if closing:
                closing_event = closing[0]
                exit_price = self._f(
                    updates.get("close_price"), self._exit_for_event(closing_event, state, close)
                )
                break

        if exit_price <= 0:
            exit_price = self._f(candles[-1].get("close"), 0.0)

        raw = (exit_price - entry) if side == "BUY" else (entry - exit_price)
        return {
            "filled": True,
            "points": round(price_to_points(raw, symbol=symbol), 1),
            "exit_price": round(exit_price, 2),
            "events": events_seen,
            "closing_event": closing_event,
            "stop_history": stop_history,
            "trailing_moves": trailing_moves,
            "breakeven_engaged": breakeven_engaged,
            "final_stop": round(self._f(state.get("stop_loss"), 0.0), 2),
            "candles_held": held,
        }

    # ── comparison ─────────────────────────────────────────────────────────

    def compare(
        self,
        trade: Dict[str, Any],
        candles: List[Dict[str, Any]],
        variants: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Replay the same trade and candles under several configurations.

        Returns the points each variant captured and which won. The verdict is
        arithmetic: no variant is preferred for being newer or more elaborate.
        """
        results: Dict[str, Any] = {}
        for name, variant_config in (variants or {}).items():
            results[name] = self.replay(trade, candles, config=variant_config)

        if not results:
            return {"variants": {}, "best": None, "tie": True, "spread_points": 0.0}

        scores = {name: self._f(data.get("points"), 0.0) for name, data in results.items()}
        best_points = max(scores.values())
        worst_points = min(scores.values())
        winners = [name for name, points in scores.items() if points == best_points]

        return {
            "variants": results,
            "scores": scores,
            "best": sorted(winners)[0],
            "tie": len(winners) == len(scores),
            "spread_points": round(best_points - worst_points, 1),
        }

    def format_lines(self, comparison: Dict[str, Any]) -> List[str]:
        """Human-readable summary of a comparison."""
        scores = comparison.get("scores") or {}
        if not scores:
            return ["No variants were replayed."]
        lines = [
            f"{name}: {points:+.0f} pts"
            for name, points in sorted(scores.items(), key=lambda kv: -kv[1])
        ]
        if comparison.get("tie"):
            lines.append("Result: tie — no variant captured more points.")
        else:
            lines.append(
                f"Best: {comparison.get('best')} "
                f"(+{comparison.get('spread_points', 0):.0f} pts over the worst)"
            )
        return lines

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _exit_for_event(event: str, state: Dict[str, Any], fallback: float) -> float:
        mapping = {
            "TP2_HIT": state.get("tp2"),
            "SL_HIT": state.get("stop_loss"),
            "TRAILING_SL_HIT": state.get("stop_loss"),
            "BE_HIT": state.get("entry_price"),
        }
        try:
            value = float(mapping.get(event) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        return value if value > 0 else fallback

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _f(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
