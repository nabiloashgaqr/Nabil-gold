"""The target-geometry diagnostic must measure, never decide.

WHY THIS EXISTS
---------------
2026-07-31, TRADE_20260731_141102_627592_b4f85832 shipped with:

    Stop Loss 4071.77   (400 pts)
    TP1       3981.77   (500 pts)
    TP2       3941.77   (900 pts)
    Target liquidity 4021.07  -- 107 pts away, absent from the order

Those distances are `risk_management_agent` widening the stop to the flat
`min_sl_distance_points` (400) and then deriving BOTH targets from that same
stop:

    tp1 = floor x (atr_multiplier_tp1 / atr_multiplier_sl) = 400 x 1.25
    tp2 = floor x (atr_multiplier_tp2 / atr_multiplier_sl) = 400 x 2.25

which reproduces 3981.77 / 3941.77 and the card's "1.25R / 2.25R" exactly.
config.json warns about this signature in
`session_planner.description_zone_width_vs_sl_floor`.

Changing any risk number on that basis alone would be a guess, and the user
has refused risk changes. So the script measures and reports; it does not
tune anything, and it refuses to draw a verdict from a sample too small to
carry one.

These tests pin that contract: the detector must recognise the real card,
must not fire on a map-derived plan, and the script must remain read-only.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "analyze_target_geometry", os.path.join(ROOT, "scripts", "analyze_target_geometry.py")
)
atg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(atg)

from utils.helpers import load_config  # noqa: E402

CONFIG = load_config()
SYMBOL = "XAU/USD"

# The card, verbatim.
# A TP2_HIT row must carry evidence that it filled -- entry_time and a
# realized result. The original fixture had neither, which is a state the
# database cannot produce: a position cannot reach TP2 without first being
# opened. Once excursion statistics began requiring proof of a fill (see
# test_target_geometry_excludes_unfilled.py) that contradiction surfaced as a
# failure, and the fixture was corrected rather than the rule relaxed.
THE_CARD = {
    "id": "TRADE_20260731_141102_627592_b4f85832",
    "symbol": SYMBOL, "status": "TP2_HIT",
    "entry_price": 4031.77, "initial_stop_loss": 4071.77, "stop_loss": 4071.77,
    "tp1": 3981.77, "tp2": 3941.77,
    "entry_time": "2026-07-31T14:14:00+00:00",
    "close_price": 3941.77, "final_pnl": 900.0,
    "max_favorable_excursion": 1050.0,
    "signal_snapshot": {"session_plan": {"primary_poi": {"target_price": 4021.07}}},
}

# An earlier trade whose targets came from the map.
MAP_DERIVED = {
    "id": "TRADE_20260731_060253_794033_d917b1d5",
    "symbol": SYMBOL, "status": "TP2_HIT",
    "entry_price": 4074.78, "initial_stop_loss": 4089.78, "stop_loss": 4089.78,
    "tp1": 4051.98, "tp2": 4029.17,
    "entry_time": "2026-07-31T06:02:53+00:00",
    "close_price": 4029.17, "final_pnl": 342.1,
    "max_favorable_excursion": 520.0,
    "signal_snapshot": {"session_plan": {"primary_poi": {"target_price": 4029.17}}},
}


def _run(trades) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        atg.analyse(trades, CONFIG, SYMBOL)
    return buffer.getvalue()


def test_the_card_is_recognised_as_stop_derived() -> None:
    assert atg._matches_floored_signature(
        THE_CARD["entry_price"], THE_CARD["initial_stop_loss"],
        THE_CARD["tp1"], THE_CARD["tp2"], SYMBOL, CONFIG,
    ) is True, (
        "400 / 500 / 900 is floor x1.00 / x1.25 / x2.25 -- the exact shape the "
        "risk agent produces when the stop is floored"
    )


def test_a_map_derived_plan_is_not_flagged() -> None:
    assert atg._matches_floored_signature(
        MAP_DERIVED["entry_price"], MAP_DERIVED["initial_stop_loss"],
        MAP_DERIVED["tp1"], MAP_DERIVED["tp2"], SYMBOL, CONFIG,
    ) is False, (
        "150 / 228 / 456 does not match the ratios; flagging it would make "
        "the measurement meaningless"
    )


def test_the_ratios_are_read_from_config_not_hardcoded() -> None:
    """If the multipliers change, the detector must follow them."""
    tweaked = {
        **CONFIG,
        "risk_settings": {
            **CONFIG["risk_settings"],
            "atr_multiplier_tp1": 3.0,   # ratio becomes 1.5
            "atr_multiplier_tp2": 6.0,   # ratio becomes 3.0
        },
    }
    # Under the new ratios the original card no longer matches...
    assert atg._matches_floored_signature(
        4031.77, 4071.77, 3981.77, 3941.77, SYMBOL, tweaked
    ) is False
    # ...but a plan built with them does.
    assert atg._matches_floored_signature(
        4031.77, 4071.77, 4031.77 - 60.0, 4031.77 - 120.0, SYMBOL, tweaked
    ) is True


def test_the_mapped_objective_is_extracted() -> None:
    assert atg._mapped_objective(THE_CARD) == 4021.07
    assert atg._mapped_objective({"signal_snapshot": {}}) == 0.0


def test_a_small_sample_refuses_to_give_a_verdict() -> None:
    output = _run([THE_CARD, MAP_DERIVED])
    assert "NO VERDICT" in output
    assert f"need {atg.MIN_SAMPLE_FOR_VERDICT}" in output
    assert "READING" not in output, (
        "two trades cannot support a conclusion; printing one would invite a "
        "risk change on noise"
    )


def test_a_large_sample_reports_the_uniformity() -> None:
    trades = [dict(THE_CARD, id=f"t{i}") for i in range(atg.MIN_SAMPLE_FOR_VERDICT + 5)]
    output = _run(trades)

    assert "READING" in output
    assert "100.0%" in output
    assert "stop-derived targets" in output


def test_points_left_on_the_table_are_counted() -> None:
    """MFE beyond TP2 is the measurable cost of an arithmetic target."""
    output = _run([THE_CARD])
    # MFE 1050 vs TP2 900 -> 150 pts beyond
    assert "Ran past TP2" in output
    assert "points left behind" in output


def test_open_and_pending_trades_are_never_measured() -> None:
    """An unfinished trade has no outcome to compare a target against."""
    source = open(os.path.join(ROOT, "scripts", "analyze_target_geometry.py"),
                  encoding="utf-8").read()
    assert '"PENDING", "OPEN", "PARTIAL", "TP1_HIT"' in source


def test_the_script_is_read_only() -> None:
    """It informs a decision; it must not take one."""
    source = open(os.path.join(ROOT, "scripts", "analyze_target_geometry.py"),
                  encoding="utf-8").read()
    # Persistence and messaging only. `sys.path.insert` and reading a file are
    # not writes, and an earlier version of this list flagged them -- the test
    # was wrong, not the script, so it is narrowed rather than deleted.
    for forbidden in (
        "update_trade", "save_trade", "delete(",
        "send_message", "send_signal", "send_trade_event",
        "json.dump", '"w"', "'w'",
    ):
        assert forbidden not in source, (
            f"a diagnostic that can write is not a diagnostic: found {forbidden!r}"
        )


def test_fault_injection_the_signature_is_what_the_risk_agent_builds() -> None:
    """Rebuild the risk agent's arithmetic and confirm it lands on the card.

    If this ever stops matching, either the agent changed or the detector did,
    and the measurement is no longer describing the live system.
    """
    risk_cfg = CONFIG["risk_settings"]
    sl_mult = float(risk_cfg["atr_multiplier_sl"])
    tp1_ratio = float(risk_cfg["atr_multiplier_tp1"]) / sl_mult
    tp2_ratio = float(risk_cfg["atr_multiplier_tp2"]) / sl_mult
    floor_price = float(risk_cfg["min_sl_distance_points"]) / 10.0  # XAU: 10 pts = $1

    entry = 4031.77
    assert round(entry + floor_price, 2) == 4071.77, "the shipped stop"
    assert round(entry - floor_price * tp1_ratio, 2) == 3981.77, "the shipped TP1"
    assert round(entry - floor_price * tp2_ratio, 2) == 3941.77, "the shipped TP2"
    assert (tp1_ratio, tp2_ratio) == (1.25, 2.25), "the card's stated R multiples"
