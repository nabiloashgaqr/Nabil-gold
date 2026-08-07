"""The trading laws have ONE source: utils/trading_rules.py.

Operator directive 2026-08-07 (phase 1): stops, targets, trailing and the
post-TP2 rule live in a single loader; every door delegates. These tests are
the teeth:

  1. GREP GUARD -- no module outside the loader may READ the rule keys or
     carry the formula defaults. Re-copying the maths anywhere fails CI.
  2. CROSS-DOOR EQUALITY -- the planner door, the consensus agent and the
     loader return identical stops and targets on the same inputs.
  3. CONFIG <-> LOADER -- the loader exposes exactly what config.json says.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import trading_rules as tr  # noqa: E402
from scripts.run_analysis import _stop_from_liquidity_points, _resolve_reward_target  # noqa: E402
from agents.risk_management_agent import RiskManagementAgent  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
ENTRY = 4316.21

READ_PATTERNS = [
    '.get("min_liquidity_points"', '.get("safety_buffer_points"',
    '.get("max_stop_points"', '.get("max_tp2_beyond_tp1_points"',
    '.get("min_tp2_multiple_of_tp1"', '.get("min_tp1_rr"',
    '.get("min_distance_points"', '.get("window_hours"',
]
SCAN_DIRS = ["scripts", "agents", "services"]


def _code_lines(path: Path):
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]  # comments may narrate history freely
        yield lineno, code


def test_no_module_reads_the_rule_keys_but_the_loader():
    for d in SCAN_DIRS:
        for path in sorted((ROOT / d).glob("*.py")):
            for lineno, code in _code_lines(path):
                for pat in READ_PATTERNS:
                    assert pat not in code, (
                        f"{d}/{path.name}:{lineno} reads {pat} -- only "
                        f"utils/trading_rules.py may own these keys"
                    )


def test_loader_is_the_only_home_of_the_formula_defaults():
    src = (ROOT / "utils" / "trading_rules.py").read_text(encoding="utf-8")
    for token in ("200.0", "70.0", "400.0"):
        assert token in src
    for d in SCAN_DIRS:
        for path in sorted((ROOT / d).glob("*.py")):
            for lineno, code in _code_lines(path):
                assert "_safe_float(rule_cfg.get(" not in code, (
                    f"{path.name}:{lineno} re-implements the stop rule"
                )


# ── cross-door equality ─────────────────────────────────────────────────────

CASES = [
    {"sell_side": [ENTRY - 25.0]},                    # 250 pts -> 320
    {"sell_side": [ENTRY - 35.0]},                    # 350 -> capped 400
    {"sell_side": [ENTRY - 5.0]},                     # noise -> 400
    {"sell_side": []},                                # none -> 400
]


@pytest.mark.parametrize("liq", CASES)
def test_all_doors_agree_on_the_rule_stop(liq):
    candidate = {"details": {"liquidity": liq}}
    rule = tr.stop_rule(CONFIG)
    via_loader = tr.stop_from_liquidity_points(
        direction="BUY", entry=ENTRY, liquidity_map=liq, cfg=CONFIG, symbol="XAU/USD")
    via_run_analysis = _stop_from_liquidity_points(
        "BUY", ENTRY, candidate, {k: rule[k] for k in ("min_liquidity_points", "safety_buffer_points", "max_stop_points", "enabled")}, "XAU/USD")
    agent = RiskManagementAgent(json.loads(json.dumps(CONFIG)))
    via_agent = agent._stop_from_liquidity_points(
        "BUY", ENTRY, liq, rule)
    assert via_loader == via_run_analysis == via_agent


def test_targets_law_matches_both_doors():
    liq = {"buy_side": [4350.0, 4375.0, 4400.0]}
    candidate = {"direction": "BUY", "entry_price": ENTRY, "details": {"liquidity": liq}}
    risk = 40.0
    l_tp1, l_tp2, l_used = tr.targets_law(
        direction="BUY", entry=ENTRY, risk_price=risk,
        levels=[4350.0, 4375.0, 4400.0], cfg=CONFIG, symbol="XAU/USD")
    r_tp1, r_tp2, _ = _resolve_reward_target(
        "BUY", ENTRY, ENTRY - risk, 4350.0, candidate, 1.5,
        symbol="XAU/USD", risk_cfg=CONFIG["risk_settings"])
    assert (l_tp1, l_tp2) == (r_tp1, r_tp2)
    agent = RiskManagementAgent(json.loads(json.dumps(CONFIG)))
    a_tp1, a_tp2, _ = agent._liquidity_chain_targets(
        direction="BUY", entry=ENTRY, stop_loss=ENTRY - risk,
        liquidity_map=liq, supports=[], resistances=[], atr=2.0)
    assert (a_tp1, a_tp2) == (l_tp1, l_tp2)


# ── config <-> loader ───────────────────────────────────────────────────────

def test_loader_exposes_the_config_numbers():
    rule = tr.stop_rule(CONFIG)
    raw = CONFIG["risk_settings"]["stop_from_liquidity"]
    assert rule["min_liquidity_points"] == raw["min_liquidity_points"] == 200
    assert rule["safety_buffer_points"] == raw["safety_buffer_points"] == 70
    assert rule["max_stop_points"] == raw["max_stop_points"] == 400

    ratios = tr.target_ratios(CONFIG)
    assert ratios["min_tp1_rr"] == CONFIG["risk_settings"]["min_tp1_rr"]
    assert ratios["tp2_multiple"] == CONFIG["risk_settings"]["min_tp2_multiple_of_tp1"]
    assert ratios["max_beyond_points"] == CONFIG["risk_settings"]["max_tp2_beyond_tp1_points"]

    post = tr.post_tp2_rule(CONFIG)
    assert post["min_distance_points"] == CONFIG["post_tp2_reentry"]["min_distance_points"] == 250
    assert post["window_hours"] == CONFIG["post_tp2_reentry"]["window_hours"] == 2.5

    trail = tr.trailing_params(CONFIG)
    assert trail["distance_points"] == CONFIG["trade_management"]["trailing_distance_points"]
    assert trail["step_points"] == CONFIG["trade_management"]["trailing_step_points"]
    assert trail["early_breakeven_points"] == CONFIG["trailing_stop"]["early_breakeven_points"]


# ── phase 2: canonical block + legacy aliases ──────────────────────────────

def test_shipped_config_has_canonical_block_matching_legacy():
    canon = CONFIG["trading_rules"]
    legacy_stop = CONFIG["risk_settings"]["stop_from_liquidity"]
    for key in ("min_liquidity_points", "safety_buffer_points", "max_stop_points"):
        assert canon["stop"][key] == legacy_stop[key]
    assert canon["targets"]["min_tp1_rr"] == CONFIG["risk_settings"]["min_tp1_rr"]
    assert canon["targets"]["tp2_multiple"] == CONFIG["risk_settings"]["min_tp2_multiple_of_tp1"]
    assert canon["targets"]["max_beyond_points"] == CONFIG["risk_settings"]["max_tp2_beyond_tp1_points"]
    assert canon["post_tp2"]["min_distance_points"] == CONFIG["post_tp2_reentry"]["min_distance_points"]
    assert canon["post_tp2"]["window_hours"] == CONFIG["post_tp2_reentry"]["window_hours"]
    assert canon["trailing"]["distance_points"] == CONFIG["trade_management"]["trailing_distance_points"]
    assert canon["trailing"]["step_points"] == CONFIG["trade_management"]["trailing_step_points"]


def test_canonical_block_wins_over_legacy_aliases():
    import copy
    cfg = copy.deepcopy(CONFIG)
    cfg["trading_rules"]["stop"]["max_stop_points"] = 350
    cfg["risk_settings"]["stop_from_liquidity"]["max_stop_points"] = 400
    assert tr.stop_rule(cfg)["max_stop_points"] == 350
    cfg["trading_rules"]["targets"]["tp2_multiple"] = 3.0
    assert tr.target_ratios(cfg)["tp2_multiple"] == 3.0
    cfg["trading_rules"]["post_tp2"]["window_hours"] = 4.0
    assert tr.post_tp2_rule(cfg)["window_hours"] == 4.0


def test_legacy_only_config_still_reads():
    legacy = {"risk_settings": {"stop_from_liquidity": {"enabled": True,
                "min_liquidity_points": 300, "safety_buffer_points": 80,
                "max_stop_points": 450}},
              "post_tp2_reentry": {"min_distance_points": 100, "window_hours": 1}}
    assert tr.stop_rule(legacy)["max_stop_points"] == 450
    assert tr.post_tp2_rule(legacy)["window_hours"] == 1
    assert tr.trailing_params({"trade_management": {"trailing_distance_points": 120}})["distance_points"] == 120
