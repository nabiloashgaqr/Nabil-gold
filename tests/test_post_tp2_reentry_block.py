"""After a SELL takes TP2, a new SELL must stand well clear of that level.

WHY
---
TP2 is where a move ENDS, because it is where the liquidity that fuelled it
was consumed. Selling again at that same level is selling into the bounce.

2026-07-31 is the case that motivated the rule:

    13:50  d917b1d5 SELL closes at TP2 4029.17  (+456 pts)
    14:11  b4f85832 published as SELL LIMIT 4031.77
           -- 26 points above that TP2, twenty-one minutes later
    15:21  price 4044.28 -> the new sell is 116 points offside

An exit-cooldown already existed (``post_exit_revalidation``, 30 minutes
after a win) and it did not fire, for a reason worth recording: it measures
distance from the previous trade's ENTRY (4074.78), so b4f85832 looked 430
points away -- outside the 200-point duplicate zone -- and was never
examined. Measuring from the level the trade CLOSED at is the whole point of
this guard.

THE RULE (as specified by the user)
-----------------------------------
    * Both directions, mirrored. The safe re-entry is always AWAY from the
      exhausted level, back toward where the move began:

          SELL -> new entry at least ``min_distance_points`` (250) ABOVE TP2
          BUY  -> new entry at least ``min_distance_points`` (250) BELOW TP2

      An entry on the far side of TP2 -- already beyond it -- is not a repeat
      of the exhausted move and is left alone.
    * Direction must match the closed trade. A BUY after a SELL's TP2 is a
      reversal, not a repeat, and is never blocked.
    * The block lapses after ``window_hours`` (2).
    * It is overridden early by a genuinely new thesis, using the same
      evidence ``_post_exit_revalidation_review`` already accepts: a
      different POI, a fresh sweep after the exit, or a state progression
      with a stronger trigger. A trend that keeps producing new structure is
      not silenced; a lazy re-entry at an exhausted level is.

NOT A RISK CHANGE
-----------------
This refuses a signal. It never resizes, re-prices or re-stops one, and no
risk threshold is read or written.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "run_analysis_post_tp2", os.path.join(ROOT, "scripts", "run_analysis.py")
)
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)

from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()
SYMBOL = "XAU/USD"
TP2 = 4029.17

# The setup the closed trade carried, so "new thesis" can be judged against it.
OLD_SETUP = {
    "state_key": "K1", "setup_type": "LIQUIDITY_REVERSAL", "poi_type": "swept_level",
    "setup_state": "ENTRY_TRIGGERED", "thesis_dominance_score": 60.0,
    "trigger_score": 50.0, "displacement_score": 40.0,
}

CLOSED_ON_TP2 = {
    "id": "TRADE_20260731_060253_794033_d917b1d5",
    "symbol": SYMBOL, "type": "SELL", "status": "TP2_HIT", "result": "WIN",
    "entry_price": 4074.78, "tp1": 4051.98, "tp2": TP2,
    "closed_at": "2026-07-31T13:50:00+00:00",
    "final_pnl": 456.1,
    "signal_snapshot": {"setup_context": OLD_SETUP},
}

# Same shape as the old setup: nothing here counts as a new thesis.
SAME_THESIS = {
    "state_key": "K1", "setup_type": "LIQUIDITY_REVERSAL", "poi_type": "swept_level",
    "setup_state": "ENTRY_ARMED", "thesis_dominance_score": 47.6,
    "trigger_score": 50.0, "displacement_score": 40.0,
}


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 7, 31, hour, minute, tzinfo=timezone.utc)


def _block(entry: float, when: datetime, *, direction: str = "SELL",
           setup: dict | None = None, closed=None, config=None) -> str | None:
    decision = {
        "decision": direction, "symbol": SYMBOL, "current_price": entry,
        "signal": {"entry": {"price": entry}},
        "setup_context": SAME_THESIS if setup is None else setup,
    }
    trades = [CLOSED_ON_TP2] if closed is None else closed
    return ra._post_tp2_reentry_block(
        decision, trades, config or CONFIG,
        now=when, symbol=SYMBOL, entry_price=entry, direction=direction,
    )


# ── the incident ────────────────────────────────────────────────────────────

def test_the_signal_that_motivated_the_rule_is_blocked() -> None:
    reason = _block(4031.77, _at(14, 11))
    assert reason is not None
    assert "26 pts above the TP2 4029.17" in reason
    assert "0.3h ago" in reason
    assert "250" in reason, "the requirement must be stated, not just the failure"


def test_the_old_cooldown_could_not_have_caught_it() -> None:
    """Documents why a new guard was needed rather than a tweak."""
    zone = float(CONFIG["duplicate_signal_filter"]["price_zone_points"])
    from_prev_entry = abs(4031.77 - CLOSED_ON_TP2["entry_price"]) * 10
    from_tp2 = abs(4031.77 - TP2) * 10

    assert from_prev_entry > zone, (
        "measured from the previous ENTRY the signal is outside the duplicate "
        "zone, which is why the existing cooldown skipped it"
    )
    assert from_tp2 < zone, "measured from TP2 it is obviously the same level"


# ── the user's chosen numbers ───────────────────────────────────────────────

def test_the_configured_distance_and_window() -> None:
    cfg = CONFIG["post_tp2_reentry"]
    assert cfg["enabled"] is True
    assert float(cfg["min_distance_points"]) == 250.0
    assert float(cfg["window_hours"]) == 2.0


def test_exactly_at_the_distance_is_allowed() -> None:
    assert _block(round(TP2 + 25.0, 2), _at(14, 30)) is None


def test_one_point_short_is_blocked() -> None:
    assert _block(round(TP2 + 25.0, 2) - 0.01, _at(14, 30)) is not None


def test_a_comfortable_rally_back_is_allowed() -> None:
    """279 pts above TP2 -- the a5520ee6 case, allowed at a 250-pt bar."""
    assert _block(4057.07, _at(15, 21)) is None


def test_the_block_lapses_after_the_window() -> None:
    assert _block(4031.77, _at(14, 11)) is not None      # 0.3h
    assert _block(4031.77, _at(15, 45)) is not None      # 1.9h
    assert _block(4031.77, _at(16, 0)) is None           # 2.2h


# ── the early override ──────────────────────────────────────────────────────

def test_a_genuinely_new_poi_lifts_the_block() -> None:
    fresh = {
        "state_key": "K2", "setup_type": "ORDER_BLOCK_PULLBACK",
        "poi_type": "order_block", "setup_state": "ENTRY_ARMED",
        "thesis_dominance_score": 70.0, "trigger_score": 62.0,
        "displacement_score": 55.0,
    }
    assert _block(4031.77, _at(14, 11), setup=fresh) is None


def test_a_repackaged_same_thesis_does_not_lift_it() -> None:
    """Only real new evidence counts, not a relabelled candidate."""
    cosmetic = {**SAME_THESIS, "quality_grade": "A"}
    assert _block(4031.77, _at(14, 11), setup=cosmetic) is not None


# ── scope ───────────────────────────────────────────────────────────────────

def test_a_buy_after_a_sell_tp2_is_never_blocked() -> None:
    """Direction must match: a BUY after a SELL's TP2 is a reversal."""
    assert _block(4031.77, _at(14, 11), direction="BUY") is None


# ── the mirrored BUY side ───────────────────────────────────────────────────
#
# A BUY's TP2 sits ABOVE its entry, so the exhausted level is above and the
# safe re-entry is BELOW it -- the exact mirror of the SELL case. The prices
# below are the manual analyst's 2026-07-30 chart, whose extended target was
# 4132.389.

BUY_TP2 = 4132.389

BUY_CLOSED_ON_TP2 = {
    "id": "buy-prev", "symbol": SYMBOL, "type": "BUY", "status": "TP2_HIT",
    "result": "WIN", "entry_price": 4074.055, "tp1": 4093.0, "tp2": BUY_TP2,
    "closed_at": "2026-07-31T13:50:00+00:00", "final_pnl": 583.3,
    "signal_snapshot": {"setup_context": OLD_SETUP},
}


def _buy_block(entry: float, when: datetime, *, setup: dict | None = None,
               config=None) -> str | None:
    decision = {
        "decision": "BUY", "symbol": SYMBOL, "current_price": entry,
        "signal": {"entry": {"price": entry}},
        "setup_context": SAME_THESIS if setup is None else setup,
    }
    return ra._post_tp2_reentry_block(
        decision, [BUY_CLOSED_ON_TP2], config or CONFIG,
        now=when, symbol=SYMBOL, entry_price=entry, direction="BUY",
    )


def test_a_buy_too_close_below_its_own_tp2_is_blocked() -> None:
    reason = _buy_block(4130.00, _at(14, 11))
    assert reason is not None
    assert "24 pts below the TP2 4132.39" in reason, (
        "the message must say BELOW for a BUY; saying 'above' would describe "
        "the wrong side of the market"
    )


def test_the_buy_bar_is_the_same_distance_mirrored() -> None:
    """Exactly 250 points below TP2 is the boundary, and it passes."""
    exact = round(BUY_TP2 - 25.0, 3)          # 250 pts in price terms
    assert _buy_block(exact, _at(14, 11)) is None
    # One cent nearer is genuinely inside the bar.
    assert _buy_block(round(exact + 0.01, 3), _at(14, 11)) is not None


def test_a_buy_well_below_its_tp2_is_allowed() -> None:
    assert _buy_block(4100.00, _at(14, 11)) is None


def test_a_buy_beyond_its_own_tp2_is_not_a_repeat() -> None:
    """Price already past the exhausted level is a different situation."""
    assert _buy_block(4140.00, _at(14, 11)) is None


def test_the_buy_block_also_lapses_after_the_window() -> None:
    assert _buy_block(4130.00, _at(15, 45)) is not None   # 1.9h
    assert _buy_block(4130.00, _at(16, 0)) is None        # 2.2h


def test_a_new_thesis_lifts_the_buy_block_too() -> None:
    fresh = {
        "state_key": "K2", "setup_type": "ORDER_BLOCK_PULLBACK",
        "poi_type": "order_block", "setup_state": "ENTRY_ARMED",
        "thesis_dominance_score": 70.0, "trigger_score": 62.0,
        "displacement_score": 55.0,
    }
    assert _buy_block(4130.00, _at(14, 11), setup=fresh) is None


def test_a_sell_after_a_buy_tp2_is_never_blocked() -> None:
    decision = {
        "decision": "SELL", "symbol": SYMBOL, "current_price": 4130.00,
        "signal": {"entry": {"price": 4130.00}}, "setup_context": SAME_THESIS,
    }
    assert ra._post_tp2_reentry_block(
        decision, [BUY_CLOSED_ON_TP2], CONFIG,
        now=_at(14, 11), symbol=SYMBOL, entry_price=4130.00, direction="SELL",
    ) is None


def test_a_sell_entry_beyond_its_own_tp2_is_not_a_repeat() -> None:
    """The SELL mirror of the far-side case."""
    assert _block(4020.00, _at(14, 11)) is None


def test_only_a_tp2_close_arms_the_rule() -> None:
    """A stop-out or breakeven exit is a different situation entirely."""
    for status, result in (("SL_HIT", "LOSS"), ("BE_HIT", "BREAKEVEN"),
                           ("THESIS_EXIT", "WIN"), ("TP1_HIT", "OPEN")):
        other = {**CLOSED_ON_TP2, "status": status, "result": result}
        assert _block(4031.77, _at(14, 11), closed=[other]) is None, (
            f"{status} must not arm a post-TP2 block"
        )


def test_an_open_trade_does_not_arm_the_rule() -> None:
    still_open = {**CLOSED_ON_TP2, "status": "OPEN", "result": None}
    assert _block(4031.77, _at(14, 11), closed=[still_open]) is None


def test_a_trade_without_a_tp2_is_skipped() -> None:
    no_tp2 = {k: v for k, v in CLOSED_ON_TP2.items() if k != "tp2"}
    no_tp2["signal_snapshot"] = {"setup_context": OLD_SETUP}
    assert _block(4031.77, _at(14, 11), closed=[no_tp2]) is None


def test_the_rule_can_be_disabled() -> None:
    off = {**CONFIG, "post_tp2_reentry": {
        **CONFIG["post_tp2_reentry"], "enabled": False,
    }}
    assert _block(4031.77, _at(14, 11), config=off) is None


def test_the_numbers_are_configurable() -> None:
    tighter = {**CONFIG, "post_tp2_reentry": {
        "enabled": True, "min_distance_points": 150, "window_hours": 1,
    }}
    # 200 pts above TP2 clears a 150-pt bar but not the default 250.
    entry = round(TP2 + 20.0, 2)
    assert _block(entry, _at(14, 11), config=tighter) is None
    assert _block(entry, _at(14, 11)) is not None
    # ...and the 1h window lapses sooner than the default 2h.
    assert _block(4031.77, _at(15, 30), config=tighter) is None


# ── risk untouched ──────────────────────────────────────────────────────────

def test_no_risk_setting_was_changed() -> None:
    risk = CONFIG["risk_settings"]
    assert float(risk["min_sl_distance_points"]) == 400.0
    assert float(risk["min_rr_ratio"]) == 1.5
    assert float(risk["max_rr_ratio"]) == 4.0
    assert int(risk["max_open_trades"]) == 3
    assert float(CONFIG["duplicate_signal_filter"]["price_zone_points"]) == 200.0


def test_the_guard_only_refuses_it_never_edits_the_signal() -> None:
    import inspect
    source = inspect.getsource(ra._post_tp2_reentry_block)
    for forbidden in ("decision[", "signal[", "entry_price =", "stop_loss"):
        assert forbidden not in source, (
            f"this guard must only return a reason; found {forbidden!r}"
        )


def test_fault_injection_measuring_from_the_entry_misses_the_signal() -> None:
    """Rebuild the old distance basis and show it lets b4f85832 through."""
    zone = float(CONFIG["duplicate_signal_filter"]["price_zone_points"])
    entry_basis = abs(4031.77 - CLOSED_ON_TP2["entry_price"]) * 10
    tp2_basis = abs(4031.77 - TP2) * 10

    assert entry_basis > zone and tp2_basis < zone, (
        "the same signal is invisible from one reference point and obvious "
        "from the other -- that is the whole bug"
    )
    assert _block(4031.77, _at(14, 11)) is not None
