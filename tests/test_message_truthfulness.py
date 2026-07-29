"""What the alert says must match what the engine will do.

Four claims in a live message were false at the same time:

  - "SL distance: 400.0 pts" on a trade risking 150 (fixed in phase 2);
  - "SL → entry after +170 pts before TP1" while TP1 sat 22 points away, so
    the trigger could never fire before the target it was said to precede;
  - "target 4029.33" in the thesis of a BUY entered at 4029.64;
  - "3 qualified agents aligned" printed above three agents disagreeing.

None of these changed a trade's outcome on their own. Together they meant the
operator could not use the message to reason about the position.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.session_planner import SessionPlannerService
from services.telegram_bot import TelegramService

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


class _Capture(TelegramService):
    def __init__(self, config):
        super().__init__(config)
        self.bot_token = None
        self.sent: list[str] = []

    def send_message(self, text: str, **_kwargs) -> bool:
        self.sent.append(text)
        return True


def _decision(tp1=4055.33, tp2=4082.34, **extra):
    decision = {
        "decision": "BUY",
        "symbol": "XAU/USD",
        "current_price": 4028.32,
        "confidence": 79.8,
        "signal": {
            "entry": {"price": 4028.32},
            "stop_loss": 4013.32,
            "tp1": tp1,
            "tp2": tp2,
            "order_type": "BUY_STOP",
        },
    }
    decision.update(extra)
    return decision


def _line(text: str, needle: str) -> str:
    for line in text.split("\n"):
        if needle in line:
            return line
    return ""


# --- 3.2 protection threshold -------------------------------------------

def test_protection_is_qualified_when_tp1_is_out_of_reach() -> None:
    """The +150 trigger cannot precede a target 5 points away."""
    telegram = _Capture(CONFIG)
    telegram.send_signal(_decision(tp1=4028.85))

    line = _line(telegram.sent[0], "Protection")
    assert "before TP1" not in line.replace("(before TP1)", "")
    assert "TP1 is 5 pts away" in line


def test_protection_says_before_tp1_when_that_is_true() -> None:
    telegram = _Capture(CONFIG)
    telegram.send_signal(_decision(tp1=4055.33))

    assert "(before TP1)" in _line(telegram.sent[0], "Protection")


def test_protection_states_the_r_multiple_gate() -> None:
    """min_breakeven_rr also governs the move; silence about it misleads."""
    telegram = _Capture(CONFIG)
    telegram.send_signal(_decision())

    assert "0.50R" in _line(telegram.sent[0], "Protection")


# --- 3.3 thesis coherence -----------------------------------------------

def _path(direction, primary, midpoint, current):
    service = SessionPlannerService({"symbol": "XAU/USD", "session_planner": {"enabled": True}})
    return service._expected_path(
        direction, primary, {"recent_sweep": {"reference_type": "previous_day_low"}},
        {"midpoint": midpoint}, current,
    )


def test_thesis_never_names_an_objective_behind_the_entry() -> None:
    """The live contradiction: buy at 4029.64, 'target 4029.33'."""
    text = _path("BUY", {"stop_loss": 4026.71, "entry_price": 4029.64}, 4029.33, 4029.64)

    assert "4029.33" not in text
    assert "next mapped liquidity ahead" in text


def test_thesis_uses_a_real_target_when_one_exists() -> None:
    primary = {"stop_loss": 4026.71, "entry_price": 4029.64, "target_liquidity": 4089.64}
    assert "target 4089.64" in _path("BUY", primary, 4029.33, 4029.64)


def test_sell_thesis_objective_must_sit_below_entry() -> None:
    primary = {"stop_loss": 4066.18, "entry_price": 4051.18, "target_liquidity": 4060.0}
    text = _path("SELL", primary, 4060.0, 4051.18)

    assert "4060.0" not in text, "an objective above a SELL entry is not a target"


# --- 3.4 capped targets -------------------------------------------------

def test_a_capped_tp2_is_marked_as_such() -> None:
    """4.00R exactly is a ceiling, not a pool the market is drawn to."""
    telegram = _Capture(CONFIG)
    decision = _decision()
    decision["signal"]["target_method"] = "mapped_target+max_rr_cap"
    telegram.send_signal(decision)

    assert "capped at max R:R" in _line(telegram.sent[0], "TP2")


def test_a_structural_tp2_carries_no_caveat() -> None:
    telegram = _Capture(CONFIG)
    decision = _decision()
    decision["signal"]["target_method"] = "mapped_target"
    telegram.send_signal(decision)

    assert "capped" not in _line(telegram.sent[0], "TP2")


# --- 3.5 dissent ---------------------------------------------------------

def test_admission_reports_the_agents_that_disagreed() -> None:
    telegram = _Capture(CONFIG)
    decision = _decision(entry_path=3, entry_mode="session_plan_ladder")
    decision["planner_execution_gate"] = {
        "allow": True,
        "reason": "3 qualified agents aligned with the mapped direction",
        "oppose_agents": ["technical", "multitimeframe"],
    }
    telegram.send_signal(decision)

    line = _line(telegram.sent[0], "Dissent")
    assert "2 qualified agent(s) opposed" in line
    assert "technical" in line and "multitimeframe" in line


def test_unanimous_admission_shows_no_dissent_line() -> None:
    telegram = _Capture(CONFIG)
    decision = _decision(entry_path=3, entry_mode="session_plan_ladder")
    decision["planner_execution_gate"] = {
        "allow": True,
        "reason": "3 qualified agents aligned with the mapped direction",
        "oppose_agents": [],
    }
    telegram.send_signal(decision)

    assert "Dissent" not in telegram.sent[0]
