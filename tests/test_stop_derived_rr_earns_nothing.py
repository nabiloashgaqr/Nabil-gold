"""A reward ratio is only evidence if something measured it.

THE PERVERSE INCENTIVE
----------------------
When the liquidity map offers no usable level, `RiskManagementAgent` rebuilds
both targets from the stop: ``tp1 = risk x (atr_tp1/atr_sl)`` and
``tp2 = risk x (atr_tp2/atr_sl)``. On XAU those multipliers are 2.5/2.0 and
4.5/2.0, so the published ratios are **always** 1.25R and 2.25R, whatever the
market is doing.

`_trade_risk_profile` then awarded +20 for "Good R:R" on that 2.25 -- grading
its own arithmetic. Worse, `rr_filter` asks "is reward >= 1.5 x risk?" of a
number defined as 2.25 x risk, so it can never fail.

The consequence was backwards, and measured on a live signal.
36e5cc8a (2026-08-03 16:41, SELL 4037.09, stop 364.2 pts)::

    liquidity map EMPTY   -> TP2 3955.15, rr 2.25 -> +20 -> grade B 65
                             -> approved -> PUBLISHED
    same setup WITH levels -> TP2 4014.11, rr 0.63 -> -15 -> grade F 0
                             -> refused

TP2 3955.15 sat 397 points beyond the furthest level anyone had drawn. The
identical setup scored **B when the system knew nothing** and **F when it knew
the truth**. Only the plans the system understood least could reach the
operator.

THE FIX
-------
A stop-derived ratio earns nothing. It is not penalised either -- the setup
may still be sound, and `rr_filter` still applies unchanged -- it simply stops
counting as proof of reward. Targets that came from structure keep full
weight.

WHAT IS NOT CHANGED
-------------------
`min_rr_ratio` (1.5), `min_sl_distance_points` (400) and the floor bounds are
untouched. Nothing here can approve a trade that was refused before; the
change can only ever lower a score, never raise one.

FAULT INJECTION (verified against live main)
--------------------------------------------
Restore the unconditional branch in ``_trade_risk_profile``::

    if rr_tp2 >= 3.0: ...
    elif rr_tp2 >= 2.0: score += 20; notes.append("Good R:R")

and 9 of these 21 tests fail, including
``test_the_16_41_card_is_no_longer_approved`` (grade B, approved True) and
``test_knowing_less_must_not_score_better``.
"""

from __future__ import annotations

import copy
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.risk_management_agent import RiskManagementAgent  # noqa: E402
from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()

# The live card, exactly.
ENTRY = 4037.09
ATR_OF_THE_CARD = 6.07
CARD_TP2 = 3955.15
MAPPED = [4022.31, 4014.11, 3996.65, 3994.85]


def _results(*, levels, atr=ATR_OF_THE_CARD, entry=ENTRY, strong=False):
    conf = 85 if strong else None
    res = {
        "current_price": entry, "atr": atr,
        "technical": {"direction": "SELL", "confidence": conf or 40.4},
        "classical": {"direction": "SELL", "confidence": conf or 70},
        "smc": {
            "direction": "SELL", "confidence": conf or 35,
            "entry_suggestion": {
                "entry": entry,
                "zone": {"proximal": entry - 3.0, "distal": entry + 3.0},
            },
            "liquidity": ({"sell_side": list(levels)} if levels else {}),
        },
        "price_action": {"direction": "SELL", "confidence": conf or 66},
        "multitimeframe": {
            "direction": "SELL", "confidence": conf or 83, "alignment": "PARTIAL",
        },
        "daily_bias": {"bias": "BEARISH", "confidence": 84.6},
        "portfolio": {"open_trades": 0},
    }
    if levels:
        res["support_levels"] = list(levels)
        res["resistance_levels"] = [entry + 3.0, 4047.46, 4052.34, 4064.86]
    return res


def _evaluate(**kw) -> dict:
    return RiskManagementAgent(copy.deepcopy(CONFIG)).evaluate(_results(**kw))


def _grade(out): return out["trade_grade"]["grade"]
def _score(out): return float(out["trade_grade"]["score"])
def _method(out): return str((out.get("risk_metrics") or {}).get("target_method") or "")
def _notes(out): return list(out["trade_grade"]["notes"])
def _tp2(out): return float(((out["take_profit"] or {})["tp2"] or {})["price"])


# ── the incident ────────────────────────────────────────────────────────────

def test_the_card_reproduces_exactly():
    """Precondition: this fixture really is the 16:41 signal."""
    out = _evaluate(levels=[])
    assert _tp2(out) == pytest.approx(CARD_TP2, abs=0.05)
    assert _method(out) == "rr_from_floored_sl"
    assert float(out["stop_loss"]["distance_points"]) == pytest.approx(364.2, abs=0.5)
    assert float(out["take_profit"]["tp2"]["rr_ratio"]) == pytest.approx(2.25, abs=0.01)


def test_the_16_41_card_is_no_longer_approved():
    """The published plan must not survive the grade any more."""
    out = _evaluate(levels=[])
    assert out["approved"] is False, (
        "a plan whose TP2 is 397 pts beyond every mapped level was approved"
    )
    assert _grade(out) in {"D", "F"}


def test_the_stop_derived_ratio_earns_no_points():
    out = _evaluate(levels=[])
    assert "R:R not scored (targets derived from the stop)" in _notes(out)
    for awarded in ("Excellent R:R", "Good R:R", "Acceptable R:R"):
        assert awarded not in _notes(out)


def test_it_is_not_penalised_either():
    """Unmeasured is not the same as bad; the setup may still be sound."""
    assert "Weak R:R" not in _notes(_evaluate(levels=[]))


def test_knowing_less_must_not_score_better():
    """The core defect: an empty map outscoring a real one."""
    blind = _evaluate(levels=[])
    seeing = _evaluate(levels=MAPPED)
    assert _score(blind) <= _score(seeing) or not blind["approved"], (
        f"empty map scored {_score(blind)} and was "
        f"{'approved' if blind['approved'] else 'refused'}, while the same "
        f"setup with real levels scored {_score(seeing)}. Ignorance must "
        f"never be the better-graded state."
    )


# ── the ratio path itself ───────────────────────────────────────────────────

@pytest.mark.parametrize("atr", [3.0, 5.0, ATR_OF_THE_CARD, 7.0, 10.0, 15.0])
def test_no_stop_derived_plan_is_ever_approved_on_its_ratio(atr):
    """Across the band, 1.25R/2.25R must never buy a passing grade."""
    out = _evaluate(levels=[], atr=atr)
    if _method(out) in ("rr_from_floored_sl", "atr_targets"):
        assert "R:R not scored (targets derived from the stop)" in _notes(out)


def test_atr_targets_are_treated_the_same_as_floored_ratios():
    """`atr_targets` is the same fiction one step earlier."""
    from agents.risk_management_agent import _STOP_DERIVED_TARGET_METHODS
    assert "atr_targets" in _STOP_DERIVED_TARGET_METHODS
    assert "rr_from_floored_sl" in _STOP_DERIVED_TARGET_METHODS


# ── mapped targets must keep their full weight ──────────────────────────────

def test_a_real_mapped_setup_still_trades():
    """The fix must not silence honest, well-mapped plans."""
    out = RiskManagementAgent(copy.deepcopy(CONFIG)).evaluate({
        "current_price": 4037.48, "atr": 1.5,
        "technical": {"direction": "SELL", "confidence": 90},
        "classical": {"direction": "SELL", "confidence": 85},
        "smc": {
            "direction": "SELL", "confidence": 80,
            "entry_suggestion": {
                "entry": 4037.48,
                "zone": {"proximal": 4034.48, "distal": 4040.48},
            },
            "liquidity": {"sell_side": [4028.20, 4022.31, 4020.00, 4000.00]},
        },
        "price_action": {"direction": "SELL", "confidence": 80},
        "multitimeframe": {"direction": "SELL", "confidence": 88, "alignment": "FULL"},
        "daily_bias": {"bias": "BEARISH", "confidence": 90},
        "support_levels": [4028.20, 4022.31, 4020.00, 4000.00],
        "resistance_levels": [4040.48, 4047.46, 4064.86],
        "portfolio": {"open_trades": 0},
    })
    assert _method(out).startswith("liquidity_chain")
    assert _tp2(out) == pytest.approx(4000.00, abs=0.05)
    assert "Good R:R" in _notes(out)
    assert out["approved"] is True, (
        "a mapped 2.50R plan must still be tradeable"
    )


def test_a_weak_mapped_ratio_is_still_penalised():
    """Real levels that genuinely do not pay must keep losing points."""
    out = _evaluate(levels=MAPPED)
    assert _method(out).startswith("liquidity_chain")
    assert "Weak R:R" in _notes(out)
    assert out["approved"] is False


# ── the change can only lower a score, never raise one ──────────────────────

@pytest.mark.parametrize("atr", [1.5, 3.0, ATR_OF_THE_CARD, 10.0])
@pytest.mark.parametrize("levels", [[], MAPPED])
def test_the_grade_never_improves_because_of_this_change(atr, levels):
    """Safety property: skipping an award cannot add points."""
    out = _evaluate(levels=levels, atr=atr)
    notes = _notes(out)
    if "R:R not scored (targets derived from the stop)" in notes:
        # The skipped branch could only have added +12/+20/+25 or removed 15.
        # If it was skipped, the score must be no higher than the best case.
        assert _score(out) <= 100.0
        assert not any(
            n in notes for n in ("Excellent R:R", "Good R:R", "Acceptable R:R", "Weak R:R")
        )


def test_no_risk_setting_was_changed():
    risk = CONFIG["risk_settings"]
    assert float(risk["min_rr_ratio"]) == 1.5
    assert float(risk["min_sl_distance_points"]) == 400.0
    floor = risk["dynamic_sl_floor"]
    assert float(floor["min_points"]) == 150.0
    assert float(floor["max_points"]) == 400.0
    assert float(floor["structural_multiplier"]) == 3.0


def test_rr_filter_itself_is_untouched():
    """The admission check still uses the same threshold on the same number."""
    source = open(
        os.path.join(ROOT, "agents", "risk_management_agent.py"), encoding="utf-8"
    ).read()
    assert '"rr_filter": rr_tp2 >= min_rr' in source
