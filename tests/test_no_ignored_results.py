"""Computed verdicts must be consumed, not just stored.

`should_block_signal` was imported, its verdict written into `all_results`,
and then never read -- so the account-level halt protected nothing while every
test stayed green. `should_send_status` had the same shape. Both were found by
hand, one round apart.

These tests generalise that class of fault so the next one fails in CI instead
of in production:

  - every gate helper must be called from production code;
  - every key written into `all_results` must be read by someone;
  - the safety checks that exist must sit ahead of order creation.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import scripts.run_analysis as ra

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DIRS = ("agents", "services", "scripts")


def _production_sources() -> dict[Path, str]:
    out: dict[Path, str] = {}
    for folder in PRODUCTION_DIRS:
        for path in sorted((ROOT / folder).glob("*.py")):
            out[path] = path.read_text(encoding="utf-8")
    return out


def test_every_gate_helper_is_called_somewhere() -> None:
    """A gate nobody calls is decoration."""
    sources = _production_sources()

    defined: dict[str, Path] = {}
    for path, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.FunctionDef):
                name = node.name
                if name.startswith("__"):
                    continue
                if name.startswith("should_") or name.endswith(("_gate", "_guard")):
                    defined[name] = path

    assert defined, "no gate helpers discovered; the scan is broken"

    unwired = []
    for name, origin in defined.items():
        called = any(
            any(
                f"{name}(" in line.strip() and not line.strip().startswith(f"def {name}")
                for line in text.split("\n")
            )
            for text in sources.values()
        )
        if not called:
            unwired.append(f"{name} ({origin.relative_to(ROOT)})")

    assert not unwired, (
        "defined but never called from production code, so they protect "
        "nothing: " + ", ".join(sorted(unwired))
    )


def test_every_all_results_key_is_read() -> None:
    """A verdict written and never read is the dynamic-risk fault again."""
    sources = _production_sources()
    analysis = sources[ROOT / "scripts" / "run_analysis.py"]

    written = set(re.findall(r'all_results\[[\'"]([a-z_]+)[\'"]\]\s*=', analysis))
    assert written, "no all_results writes found; the scan is broken"

    ignored = []
    for key in sorted(written):
        pattern = re.compile(r'\.get\(\s*[\'"]' + re.escape(key) + r'[\'"]')
        if not any(pattern.search(text) for text in sources.values()):
            ignored.append(key)

    assert not ignored, (
        "written into all_results but never read by anything: " + ", ".join(ignored)
    )


def test_safety_checks_precede_order_creation() -> None:
    """Order of operations is the whole point of a guard."""
    source = inspect.getsource(ra._run_analysis_for_config)
    create = source.find("database.new_trade_id()")
    assert create != -1, "order creation not found; the scan is broken"

    for label, needle in (
        ("dynamic risk halt", "_dynamic_risk_block_for_cycle("),
        ("final signal validation", "validate_signal_before_send("),
    ):
        position = source.find(needle)
        assert position != -1, f"{label} is not wired into the analysis cycle"
        assert position < create, f"{label} runs after the order is created"


def test_execution_path_helpers_have_tests() -> None:
    """Guards against another filter shipping with no coverage at all.

    `_cross_path_distance_check` decided whether orders could stack and had
    no test of any kind; a refactor could have inverted it silently.
    """
    tests_text = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "tests").glob("*.py")
    )

    untested = [
        name
        for name in (
            "_cross_path_distance_check",
            "_planner_execution_gate",
            "_resolve_reward_target",
            "_planner_trade_levels",
            "validate_signal_before_send",
            "duplicate_signal_reason",
        )
        if name not in tests_text
    ]

    assert not untested, "execution-path helpers with no test coverage: " + ", ".join(untested)
