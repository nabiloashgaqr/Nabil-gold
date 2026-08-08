"""Tick-manager pure decisions (VPS tick loop, phase 3+)."""
from __future__ import annotations

import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_tick_manager import decide_be, decide_tp1, decide_trailing  # noqa: E402

PV = 0.10


def test_be_arms_at_150_and_half_r():
    # fav 20$ = 200 pts >= max(150, 0.5*400=200)
    assert decide_be("BUY", 4300.0, 4320.0, 400.0, 150.0, 0.5, False)
    assert not decide_be("BUY", 4300.0, 4310.0, 400.0, 150.0, 0.5, False)
    assert not decide_be("BUY", 4300.0, 4320.0, 400.0, 150.0, 0.5, True)


def test_be_sell_mirror():
    assert decide_be("SELL", 4300.0, 4280.0, 400.0, 150.0, 0.5, False)


def test_tp1_touch_per_side():
    assert decide_tp1("BUY", 4340.0, 4339.0, 4345.0, False)
    assert decide_tp1("SELL", 4260.0, 4255.0, 4261.0, False)
    assert not decide_tp1("BUY", 4340.0, 4341.0, 4345.0, False)
    assert not decide_tp1("BUY", 4340.0, 4339.0, 4345.0, True)


def test_trailing_ratchets_in_steps_buy():
    # gap 150 pts = $15, step 40 pts = $4
    assert decide_trailing("BUY", 4300.0, 4300.0, 4360.0, 150, 40, PV) == 4345.0
    # no move backward
    assert decide_trailing("BUY", 4300.0, 4345.0, 4346.0, 150, 40, PV) is None
    # step respected: candidate below current+step -> no move
    assert decide_trailing("BUY", 4300.0, 4345.0, 4350.0, 150, 40, PV) is None
    assert decide_trailing("BUY", 4300.0, 4330.0, 4350.0, 150, 40, PV) == 4335.0


def test_trailing_never_below_entry_buy():
    assert decide_trailing("BUY", 4300.0, 4300.0, 4310.0, 150, 40, PV) is None
