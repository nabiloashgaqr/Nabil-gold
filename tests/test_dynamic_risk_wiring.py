"""The account-level circuit breaker must actually stop trading.

`should_block_signal` halts trading after a losing streak, after the daily
loss limit is spent, and raises the confidence/quality bar in between. It was
imported into run_analysis and its verdict computed into all_results -- then
never read. Every protection it provides was inert: the system kept opening
trades no matter how much it had just lost.

These tests pin the wiring, not just the function.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
from pathlib import Path

import pytest

import scripts.run_analysis as ra
from services.dynamic_risk import DynamicRiskManager, should_block_signal

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


class _Database:
    def __init__(self, consecutive_losses: int = 0, daily_points: float = 0.0) -> None:
        self._losses = consecutive_losses
        self._daily = daily_points

    def get_consecutive_losses(self) -> int:
        return self._losses

    def get_today_trades(self):
        if not self._daily:
            return []
        # `final_pnl` is the key DynamicRiskManager._pnl actually reads.
        return [{"status": "SL_HIT", "final_pnl": self._daily, "closed_at": "now"}]

    def get_recent_trades(self, limit: int = 20):
        return []


def _enabled_config(**overrides):
    config = copy.deepcopy(CONFIG)
    settings = config.setdefault("dynamic_risk_management", {})
    settings["enabled"] = True
    settings.update(overrides)
    return config


def _signal(confidence: float = 79.8, quality: float = 100.0):
    return {"decision": "BUY", "confidence": confidence, "quality": {"score": quality}}


# --- wiring -------------------------------------------------------------

def test_should_block_signal_is_actually_called() -> None:
    """The import existed for months while the call never did."""
    source = inspect.getsource(ra._run_analysis_for_config)
    assert "should_block_signal(" in source, (
        "dynamic risk is computed but never consulted; the halt does nothing"
    )


def test_dynamic_risk_check_precedes_both_execution_routes() -> None:
    """A halt must cover the planner ladder as well as the direct path."""
    source = inspect.getsource(ra._run_analysis_for_config)

    gate = source.find("should_block_signal(")
    ladder = source.find("_execute_session_plan_ladder(")
    direct = source.find("database.new_trade_id()")

    assert gate != -1 and ladder != -1 and direct != -1
    assert gate < ladder, "ladder can create orders before the halt is checked"
    assert gate < direct, "direct path can create orders before the halt is checked"


def test_halt_gate_covers_a_wait_cycle_carrying_a_ready_plan() -> None:
    """The ladder trades the plan's bias, so gating on decision_type is not enough."""
    source = inspect.getsource(ra._run_analysis_for_config)
    start = source.find("should_block_signal(")
    window = source[max(0, start - 900):start]
    assert "session_bias" in window, (
        "the halt only looks at decision_type; a WAIT cycle with a ready plan "
        "would still place ladder orders during a halt"
    )


def test_dynamic_risk_block_returns_before_creating_a_trade() -> None:
    """Reporting the block must not become an alternative to stopping."""
    source = inspect.getsource(ra._run_analysis_for_config)
    start = source.find("should_block_signal(")
    ladder = source.find("_execute_session_plan_ladder(")
    between = source[start:ladder]
    assert "\n                return" in between or "\n            return" in between


# --- behaviour ----------------------------------------------------------

def test_halt_after_consecutive_losses() -> None:
    config = _enabled_config(halt_after_losses=3)
    state = DynamicRiskManager(config).evaluate(_Database(consecutive_losses=3))

    assert state["can_trade"] is False
    assert state["level"] == "HALT"
    reason = should_block_signal(_signal(), state)
    assert reason and "consecutive losses" in reason


def test_daily_loss_limit_halts_trading() -> None:
    config = _enabled_config(daily_loss_limit_points=300)
    state = DynamicRiskManager(config).evaluate(_Database(daily_points=-450))

    assert state["can_trade"] is False
    assert state["level"] == "DAILY_HALT"
    assert should_block_signal(_signal(), state)


def test_strict_mode_raises_the_confidence_bar() -> None:
    """Two losses should not halt, but should demand a better signal."""
    config = _enabled_config(warn_after_losses=2, strict_min_confidence=82)
    state = DynamicRiskManager(config).evaluate(_Database(consecutive_losses=2))

    assert state["can_trade"] is True
    assert state["level"] == "STRICT"
    assert should_block_signal(_signal(confidence=79.8), state), "79.8% < 82% must be refused"
    assert should_block_signal(_signal(confidence=88.0), state) is None


def test_normal_conditions_let_a_sound_signal_through() -> None:
    """The breaker must not become a blanket refusal."""
    config = _enabled_config()
    state = DynamicRiskManager(config).evaluate(_Database())

    assert state["can_trade"] is True
    assert should_block_signal(_signal(), state) is None


def test_disabled_breaker_blocks_nothing() -> None:
    config = copy.deepcopy(CONFIG)
    config["dynamic_risk_management"]["enabled"] = False
    state = DynamicRiskManager(config).evaluate(_Database(consecutive_losses=9))

    assert should_block_signal(_signal(), state) is None


# --- no more dead gates -------------------------------------------------

def test_no_gate_helper_is_left_unwired() -> None:
    """Generalises the two faults found by hand.

    `should_block_signal` and `should_send_status` were both written, correct
    and never called. Any future gate that ships unwired fails here instead of
    silently protecting nothing.
    """
    production_dirs = ("agents", "services", "scripts")
    defined: dict[str, Path] = {}
    for folder in production_dirs:
        for path in sorted((ROOT / folder).glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                name = node.name
                is_gate = name.startswith("should_") or name.endswith(("_gate", "_guard"))
                if is_gate and not name.startswith("__"):
                    defined[name] = path

    unwired = []
    for name, origin in defined.items():
        called = False
        for folder in production_dirs:
            for path in (ROOT / folder).glob("*.py"):
                text = path.read_text(encoding="utf-8")
                for line in text.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith(f"def {name}"):
                        continue
                    if f"{name}(" in stripped:
                        called = True
                        break
                if called:
                    break
            if called:
                break
        if not called:
            unwired.append(f"{name} ({origin.relative_to(ROOT)})")

    assert not unwired, (
        "these gates are defined but never called from production code, so "
        "they protect nothing: " + ", ".join(sorted(unwired))
    )
