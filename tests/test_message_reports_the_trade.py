"""Numbers in the alert must describe the trade, not the settings file.

A signal risking 150 points announced "SL distance: 400.0 pts" -- the value of
`risk_settings.min_sl_distance_points`, printed verbatim. The operator was
reading a configured floor and believing it was the trade's own risk.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.telegram_bot import TelegramService

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


class _Capture(TelegramService):
    """Renders a message without touching the network."""

    def __init__(self, config):
        super().__init__(config)
        self.bot_token = None
        self.sent: list[str] = []

    def send_message(self, text: str, **_kwargs) -> bool:
        self.sent.append(text)
        return True


def _decision(entry=4028.32, stop=4013.32, configured_floor=400.0):
    return {
        "decision": "BUY",
        "symbol": "XAU/USD",
        "current_price": entry,
        "confidence": 79.8,
        "signal": {
            "entry": {"price": entry},
            "stop_loss": stop,
            "tp1": 4055.33,
            "tp2": 4082.34,
            "order_type": "BUY_STOP",
        },
        "risk": {"stop_loss": {"distance_points": configured_floor}},
    }


def _sl_line(text: str) -> str:
    for line in text.split("\n"):
        if "SL distance" in line:
            return line
    return ""


def test_sl_distance_is_measured_from_the_trade() -> None:
    telegram = _Capture(CONFIG)
    telegram.send_signal(_decision())

    line = _sl_line(telegram.sent[0])
    assert "150" in line, f"expected the real 150 pt risk, got: {line!r}"
    assert "400" not in line, "the configured floor leaked into the message"


def test_a_wider_stop_reports_its_own_distance() -> None:
    telegram = _Capture(CONFIG)
    telegram.send_signal(_decision(entry=4000.0, stop=3960.0))

    assert "400" in _sl_line(telegram.sent[0])


def test_sell_side_distance_is_positive() -> None:
    telegram = _Capture(CONFIG)
    decision = _decision(entry=4051.18, stop=4066.18)
    decision["decision"] = "SELL"
    decision["signal"].update({"tp1": 4021.18, "tp2": 3971.18, "order_type": "SELL_STOP"})

    line = _sl_line(telegram.sent[0]) if telegram.sent else ""
    telegram.send_signal(decision)
    line = _sl_line(telegram.sent[-1])
    assert "150" in line
    assert "-" not in line.split(":")[-1]
