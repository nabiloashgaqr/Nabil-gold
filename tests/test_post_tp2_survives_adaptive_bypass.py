"""Adaptive execution may waive the duplicate filter, never the post-TP2 block.

2026-08-03, the rule's first live test -- and it failed.

    13:38  SELL 79fb5a6e takes TP2 at 4022.31  (+305.4 pts)
    13:41  a new SELL LIMIT is published at 4037.48

That is 152 points above the target just taken, three minutes later: inside
the 250-point distance bar and well inside the 3-hour window. The guard was
configured, correct, and never ran.

WHY IT DID NOT RUN
------------------
``_post_tp2_reentry_block`` lives inside ``duplicate_signal_reason``, and the
main signal path calls that function like this:

    duplicate_reason = None if adaptive_action in {
        "PROMOTE_TO_MARKET", "REPLACE_WITH_CONTINUATION"
    } else duplicate_signal_reason(decision, database, config)

Skipping the DUPLICATE check for an adaptive replacement is deliberate: the
whole point of REPLACE_WITH_CONTINUATION is to put out an order that
resembles the stale one it replaces, so a duplicate filter would refuse it by
design. But the skip took the entire function with it, and the post-TP2 guard
was a passenger.

Two rules with opposite intents were riding in one vehicle:

  * "do not publish the same order twice"     -- waivable by a stronger thesis
  * "do not re-enter at an exhausted target"  -- not waivable by anything

An exhausted level is exhausted whatever route the signal took to reach
execution. ``_post_tp2_reentry_reason`` now evaluates it separately, after
the duplicate check and regardless of ``adaptive_action``.

This is the third time a guard in this project was real, tested, and bypassed
on the one path that mattered -- the entry-zone floor and the SMC selection
role were the first two. The pattern is always the same: the rule is written
once and the code has more than one door.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "run_analysis_adaptive_bypass", os.path.join(ROOT, "scripts", "run_analysis.py")
)
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)

from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()
SYMBOL = "XAU/USD"

TP2_TAKEN = 4022.31        # the target 79fb5a6e closed on
NEW_ENTRY = 4037.48        # the SELL LIMIT published three minutes later

_SETUP = {
    "state_key": "K1", "setup_type": "STRUCTURE_CONTINUATION", "poi_type": "fvg",
    "setup_state": "ENTRY_TRIGGERED", "thesis_dominance_score": 70.5,
    "trigger_score": 55.0,
}


def _closed_trade(minutes_ago: float = 3.0) -> dict:
    return {
        "id": "TRADE_20260803_112629_291441_79fb5a6e",
        "symbol": SYMBOL, "type": "SELL", "status": "TP2_HIT", "result": "WIN",
        "entry_price": 4052.85, "tp1": 4037.58, "tp2": TP2_TAKEN,
        "closed_at": (
            datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        ).isoformat(),
        "final_pnl": 305.4,
        "signal_snapshot": {"setup_context": _SETUP},
    }


class _Database:
    """Minimal stand-in: the guard only reads trade history."""

    def __init__(self, recent=None, open_trades=None):
        self._recent = recent if recent is not None else [_closed_trade()]
        self._open = open_trades or []

    def get_open_trades(self):
        return self._open

    def get_recent_trades(self, limit: int = 50):
        return self._recent


def _decision(entry: float = NEW_ENTRY, direction: str = "SELL") -> dict:
    return {
        "decision": direction, "symbol": SYMBOL, "current_price": 4022.52,
        "signal": {
            "order_type": f"{direction}_LIMIT", "entry": {"price": entry},
            "stop_loss": 4077.48, "tp1": 3987.48, "tp2": 3947.48,
        },
        "setup_context": {**_SETUP, "setup_state": "ENTRY_ARMED",
                          "selection_role": "PRIMARY"},
    }


def _window_text() -> str:
    """The configured window as the message renders it (2.5 -> "2.5", 3.0 -> "3")."""
    hours = float((CONFIG.get("post_tp2_reentry") or {}).get("window_hours", 3))
    text = f"{hours:.1f}"
    return text[:-2] if text.endswith(".0") else text


# ── the incident ────────────────────────────────────────────────────────────

def test_the_published_signal_is_now_refused() -> None:
    reason = ra._post_tp2_reentry_reason(_decision(), _Database(), CONFIG)

    assert reason is not None, "152 pts above a target taken 3 minutes ago"
    assert "152 pts above the TP2 4022.31" in reason
    # UPDATED 2026-08-03: the window was cut from 3h to 2.5h at the operator's
    # request. This assertion is not weakened -- it still requires the message
    # to name the exact configured window, it just reads it from config rather
    # than hard-coding "3h". A message that announced the wrong window would
    # still fail here, which is the point of the assertion.
    assert "250" in reason and f"{_window_text()}h" in reason, (
        "the message must state the bar it failed, so the block is auditable"
    )


def test_the_distance_and_window_are_the_configured_ones() -> None:
    cfg = CONFIG["post_tp2_reentry"]
    assert float(cfg["min_distance_points"]) == 250.0
    # 3.0 -> 2.5 on 2026-08-03 at the operator's request. See
    # config.json post_tp2_reentry.description_window_hours.
    assert float(cfg["window_hours"]) == 2.5
    assert (NEW_ENTRY - TP2_TAKEN) * 10 < 250.0


# ── the bypass itself ───────────────────────────────────────────────────────

def test_the_guard_is_evaluated_outside_the_duplicate_filter() -> None:
    """The fix: a call that does not depend on adaptive_action."""
    source = open(os.path.join(ROOT, "scripts", "run_analysis.py"),
                  encoding="utf-8").read()
    assert "_post_tp2_reentry_reason(decision, database, config)" in source, (
        "the block must have its own call site; living only inside "
        "duplicate_signal_reason is what let the adaptive path skip it"
    )


def test_fault_injection_the_adaptive_skip_removed_the_guard() -> None:
    """Rebuild the pre-fix expression and show the guard never executes."""
    calls: list[str] = []

    def duplicate_signal_reason_stub(*_args, **_kwargs):
        calls.append("duplicate_filter_ran")
        return "would have blocked"

    for action in ("ALLOW_NEW", "PROMOTE_TO_MARKET", "REPLACE_WITH_CONTINUATION"):
        calls.clear()
        # The old line, verbatim.
        _ = None if action in {"PROMOTE_TO_MARKET", "REPLACE_WITH_CONTINUATION"} \
            else duplicate_signal_reason_stub()
        if action == "ALLOW_NEW":
            assert calls == ["duplicate_filter_ran"]
        else:
            assert calls == [], (
                f"{action} skipped the whole function, and the post-TP2 guard "
                "was inside it"
            )

    # The shipped path blocks regardless of the adaptive verdict.
    assert ra._post_tp2_reentry_reason(_decision(), _Database(), CONFIG) is not None


# ── the guard must stay narrow ──────────────────────────────────────────────

def test_a_re_entry_beyond_the_distance_is_allowed() -> None:
    far = _decision(entry=round(TP2_TAKEN + 27.7, 2))   # 277 pts above
    assert ra._post_tp2_reentry_reason(far, _Database(), CONFIG) is None


def test_the_opposite_direction_is_allowed() -> None:
    assert ra._post_tp2_reentry_reason(
        _decision(direction="BUY"), _Database(), CONFIG
    ) is None


def test_the_block_lapses_after_the_window() -> None:
    stale = _Database(recent=[_closed_trade(minutes_ago=3 * 60 + 10)])
    assert ra._post_tp2_reentry_reason(_decision(), stale, CONFIG) is None


def test_a_different_symbol_is_not_affected() -> None:
    other = dict(_decision(), symbol="WTI/USD")
    assert ra._post_tp2_reentry_reason(other, _Database(), CONFIG) is None


def test_no_history_means_no_block() -> None:
    assert ra._post_tp2_reentry_reason(
        _decision(), _Database(recent=[], open_trades=[]), CONFIG
    ) is None


def test_a_database_failure_never_blocks_the_cycle() -> None:
    """A guard that crashes the run is worse than the fault it prevents."""
    class _Broken:
        def get_open_trades(self):
            raise RuntimeError("supabase down")

        def get_recent_trades(self, limit: int = 50):
            raise RuntimeError("supabase down")

    assert ra._post_tp2_reentry_reason(_decision(), _Broken(), CONFIG) is None


def test_a_wait_decision_is_ignored() -> None:
    assert ra._post_tp2_reentry_reason(
        dict(_decision(), decision="WAIT"), _Database(), CONFIG
    ) is None


# ── risk untouched ──────────────────────────────────────────────────────────

def test_no_risk_setting_was_changed() -> None:
    risk = CONFIG["risk_settings"]
    assert float(risk["min_sl_distance_points"]) == 400.0
    assert float(risk["min_rr_ratio"]) == 1.5
    assert int(risk["max_open_trades"]) == 3
    vote = CONFIG["trade_management"]["thesis_exit"]["agent_vote"]
    assert int(vote["min_opponents_to_exit"]) == 3
    assert str(vote["silent_action"]) == "HOLD"
