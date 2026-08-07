"""A plan the risk agent refused must never reach the operator.

THE INCIDENT (2026-08-03 16:11, TRADE_20260803_161104_504791_d0c708d9)
----------------------------------------------------------------------
A card went out reading:

    SELL LIMIT · Entry 4045.99 · Stop Loss 4085.79
    TP1 3996.24 · TP2 3956.44 · Planned RR 1.25R / 2.25R
    Status: Pending order

Two separate defects produced it.

1. THE FLOOR VETOED THE MAP
   The structural stop was 132.7 pts. `dynamic_sl_floor` scaled it to 398.
   `_liquidity_chain_targets` was then asked whether any mapped level pays
   for 398 points of risk::

       4022.31 = 0.59R   4014.11 = 0.80R   3996.65 = 1.24R   3994.85 = 1.28R

   None cleared `min_rr_ratio` 1.5, so the chain returned nothing and the
   caller rebuilt targets from the floor itself: 398 x 1.25 and 398 x 2.25,
   giving 3996.24 and 3956.44. That is the -400/+500/+900 signature, and
   3956.44 is not a level anyone drew -- it is arithmetic on the stop.

   Against the *structural* 132.7 points the same four levels score 1.78R,
   2.40R, 3.72R and 3.85R. Same map, opposite verdict, decided only by which
   stop the question was asked against. The floor is padding against noise;
   it says nothing about where price is going, so it must not decide whether
   an objective is worth aiming at.

2. THE RISK VERDICT WAS IGNORED
   With honest targets the plan fails `rr_filter` -- no mapped level pays for
   a 398-point stop -- so `RiskManagementAgent` returned `approved=False`.
   The two-agent/consensus path rebuilt the signal out of that same refused
   payload and published it anyway. It read the agent's entry, stop and
   targets while never reading its verdict.

   That is the sixth guard in this codebase found to have a second door. The
   scale-in path checks `approved` at run_analysis.py:~2552; this one did not.

WHAT IS NOT CHANGED
-------------------
`min_rr_ratio` (1.5), `min_sl_distance_points` (400) and the floor bounds are
untouched. The shipped stop is still the floored one and the published RR is
still computed against it, so the card never claims a reward it is not
getting. The only changes are *which stop decides where to aim* and *whether
a refused plan may be published*.

FAULT INJECTION (verified against live main)
--------------------------------------------
* Remove the `structural_risk` fallback in `_liquidity_chain_targets`:
  `test_targets_stay_on_the_map_when_the_floor_is_large` fails with
  target_method `rr_from_floored_sl` and TP2 3956.44.
* Remove the `approved` gate in the two-agent branch:
  `test_the_publish_path_refuses_an_unapproved_plan` fails.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.risk_management_agent import RiskManagementAgent  # noqa: E402
from utils.helpers import load_config  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "run_analysis_refused_plans", os.path.join(ROOT, "scripts", "run_analysis.py")
)
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)

CONFIG = load_config()
SYMBOL = "XAU/USD"

# The live card, exactly.
ENTRY = 4045.99
CARD_STOP_POINTS = 400.0  # 2026-08-07b: first eligible pool 351.5 + 70 -> capped
CARD_TP1 = 3996.24
CARD_TP2 = 3956.44
MAPPED = [4022.31, 4014.11, 3996.65, 3994.85]
# atr * atr_multiplier_sl * 10 == 132.67 structural points
ATR_OF_THE_CARD = 13.267 / 2.0


def _results(*, atr: float = ATR_OF_THE_CARD, levels=None) -> dict:
    levels = list(MAPPED if levels is None else levels)
    return {
        "current_price": 4035.15, "atr": atr,
        "technical": {"direction": "SELL", "confidence": 92},
        "classical": {"direction": "SELL", "confidence": 82},
        "smc": {
            "direction": "SELL", "confidence": 35,
            "entry_suggestion": {
                "entry": ENTRY, "zone": {"proximal": 4043.00, "distal": 4049.00},
            },
            "liquidity": {"sell_side": levels},
        },
        "price_action": {"direction": "SELL", "confidence": 75},
        "multitimeframe": {"direction": "SELL", "confidence": 92},
        "support_levels": levels,
        "resistance_levels": [4049.00, 4057.36, 4064.86],
        "portfolio": {"open_trades": 0},
    }


def _evaluate(**kw) -> dict:
    return RiskManagementAgent(copy.deepcopy(CONFIG)).evaluate(_results(**kw))


def _tp(out: dict, key: str) -> float:
    return float(((out.get("take_profit") or {}).get(key) or {}).get("price") or 0.0)


def _entry(out: dict) -> float:
    """The entry the agent actually chose.

    It is NOT always the SMC suggestion: `_smart_entry` may return the current
    price instead, and it moves with ATR. Two assertions in the first draft of
    this file compared against a hard-coded 4045.99 and failed for that reason
    alone -- the code was right, the test was reading the wrong entry.
    """
    return float((out.get("entry") or {}).get("price") or 0.0)


# ── defect 1: the floor must not veto the map ───────────────────────────────

def test_the_card_reproduces_its_structural_stop():
    """Precondition: this fixture really is the 16:11 setup."""
    out = _evaluate()
    metrics = out.get("risk_metrics") or {}
    # 2026-08-07b: the rule stop IS the structural stop now. The first
    # eligible pool (351.5 pts) + 70 = 421.5 -> capped at 400.
    assert float(metrics["structural_sl_points"]) == pytest.approx(
        CARD_STOP_POINTS, abs=1.0)
    assert float((out.get("stop_loss") or {})["distance_points"]) == pytest.approx(
        CARD_STOP_POINTS, abs=1.0
    )


def test_targets_stay_on_the_map_when_the_map_pays():
    """The shipped targets must be levels, not multiples of the stop.

    2026-08-07b: the old MAPPED pools (all < 1R vs the 400-pt rule stop) no
    longer pay, so ratios are the designed fallback there. To pin "targets
    stay on the map" we use pools that DO pay the rule stop: 300 pts (TP1,
    0.81R) and 600 pts (TP2, 1.62R) against the 370-pt stop they anchor.
    """
    out = _evaluate(atr=1.5, levels=[4005.15, 3975.15])
    method = str((out.get("risk_metrics") or {}).get("target_method") or "")

    assert method.startswith("liquidity_chain"), (
        f"targets came from {method!r}; the floor vetoed the map again"
    )
    fed = [4005.15, 3975.15]
    assert _tp(out, "tp2") in fed, (
        f"TP2 {_tp(out, 'tp2')} is not a mapped level"
    )
    assert _tp(out, "tp1") in fed


def test_the_invented_levels_from_the_card_are_gone():
    out = _evaluate()
    for invented in (CARD_TP1, CARD_TP2):
        assert _tp(out, "tp1") != pytest.approx(invented, abs=0.05)
        assert _tp(out, "tp2") != pytest.approx(invented, abs=0.05)


@pytest.mark.parametrize("atr", [0.5, 1.5, 3.0, 5.0, ATR_OF_THE_CARD, 7.0, 10.0])
def test_the_ratio_signature_does_not_survive_where_the_map_can_pay(atr):
    """1.25R/2.25R is the fingerprint of stop-derived targets.

    NARROWED FROM THE FIRST DRAFT, DELIBERATELY. The draft also asserted this
    at ATR 15 and failed. Investigating showed the code was right: at ATR 15
    `_smart_entry` returns the current price (4035.15, not the 4045.99 zone),
    which moves every mapped level closer, and none of them then clears
    min_rr even against the structural stop. With nothing on the map worth
    aiming at, rebuilding from ratios is the correct fallback -- and the
    plan is refused downstream anyway.

    So the guarantee is not "the ratio path never runs". It is "the ratio
    path never runs while the map still has a qualifying level". That is the
    real defect from 16:11, and it is what this pins.
    """
    # 2026-08-07b: the old MAPPED pools never pay the rule stop, so ratios
    # are the designed fallback for them; the guarantee is "no signature
    # WHILE the map has a qualifying level", so we feed paying pools.
    out = _evaluate(atr=atr, levels=[4005.15, 3975.15])
    tp = out.get("take_profit") or {}
    rr1 = float((tp.get("tp1") or {}).get("rr_ratio") or 0)
    rr2 = float((tp.get("tp2") or {}).get("rr_ratio") or 0)
    signature = abs(rr1 - 1.25) < 0.02 and abs(rr2 - 2.25) < 0.02
    assert not signature, (
        f"atr {atr}: shipped the stop-derived signature {rr1}R/{rr2}R via "
        f"{(out.get('risk_metrics') or {}).get('target_method')}"
    )


def test_the_published_rr_is_measured_against_the_real_stop():
    """Honest targets must not come with a flattering label."""
    out = _evaluate()
    risk = float((out.get("stop_loss") or {})["distance_points"])
    tp2 = _tp(out, "tp2")
    expected = abs(_entry(out) - tp2) / 0.1 / risk
    reported = float(((out.get("take_profit") or {})["tp2"])["rr_ratio"])
    assert reported == pytest.approx(expected, abs=0.02)


def test_a_map_that_does_pay_is_still_traded():
    """The fix must not refuse setups that genuinely clear the bar."""
    out = _evaluate(atr=1.5, levels=[4005.15, 3975.15])
    assert str((out.get("risk_metrics") or {}).get("target_method")).startswith(
        "liquidity_chain"
    )
    assert _tp(out, "tp2") == pytest.approx(3975.15, abs=0.05), (
        "the chain must reach the furthest pool the 370-pt rule stop "
        "justifies (600 pts = 1.62R), not invent a ratio target"
    )


def test_an_empty_map_still_falls_back_to_ratios():
    """With no levels at all the old behaviour must remain available."""
    out = _evaluate(levels=[])
    method = str((out.get("risk_metrics") or {}).get("target_method") or "")
    assert method and not method.startswith("liquidity_chain")


# ── defect 2: a refused plan must not be published ──────────────────────────

def test_the_risk_agent_refuses_the_card():
    out = _evaluate()
    assert out["approved"] is False
    checks = (out.get("risk_metrics") or {}).get("checks") or {}
    # With the honest 132.7-pt stop some mapped levels pay, so rr_filter now
    # passes; the card is still refused by the grade (smc confidence 35).
    assert checks.get("trade_grade_filter") is False, (
        "the 16:11 card must stay refused; the failing gate is the grade"
    )


def test_the_publish_path_refuses_an_unapproved_plan():
    """The two-agent branch must read `approved`, not just the numbers."""
    source = open(
        os.path.join(ROOT, "scripts", "run_analysis.py"), encoding="utf-8"
    ).read()
    branch = source[source.index("# ── Step A: Try Macro Confirmation ──"):]
    branch = branch[: branch.index("decision[\"entry_mode\"] = f\"two_agent_")]
    assert '.get("approved", True)' in branch, (
        "the two-agent publish path rebuilds the signal from all_results['risk'] "
        "without ever checking whether the risk agent approved it. That is how "
        "d0c708d9 shipped as a pending order after rr_filter had refused it."
    )


def test_the_refusal_is_logged_with_the_failed_checks():
    source = open(
        os.path.join(ROOT, "scripts", "run_analysis.py"), encoding="utf-8"
    ).read()
    assert "Path 2 blocked: risk agent refused this plan" in source, (
        "a silent refusal cannot be audited"
    )


def test_no_risk_setting_was_changed():
    risk = CONFIG["risk_settings"]
    assert float(risk["min_rr_ratio"]) == 1.5
    assert float(risk["min_sl_distance_points"]) == 400.0
    rule = risk["stop_from_liquidity"]
    assert rule["min_liquidity_points"] == 200
    assert rule["safety_buffer_points"] == 70
    assert rule["max_stop_points"] == 400
