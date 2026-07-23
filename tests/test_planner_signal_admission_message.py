from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.telegram_bot import TelegramService


def test_planner_led_signal_shows_admission_line() -> None:
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    captured = {}

    def _fake_send(text: str, urgent: bool = False, **_k):
        captured["text"] = text
        return True

    service.send_message = _fake_send  # type: ignore[assignment]
    decision = {
        "decision": "SELL",
        "symbol": "XAU/USD",
        "confidence": 96.3,
        "current_price": 4055.94,
        "entry_mode": "session_plan_ladder",
        "entry_path": 3,
        "planner_execution_gate": {
            "allow": True,
            "kind": "OBJECTIVE_ALIGNED_TWO_AGENT_OVERRIDE",
            "support_count": 2,
            "support_agents": ["smc", "price_action"],
            "reason": "objective-aligned continuation override: 2 qualified agents including SMC + local confirmation, with aligned sweep and structure",
        },
        "quality": {"grade": "A+", "score": 100.0},
        "signal": {
            "type": "SELL",
            "entry": {"price": 4065.05, "low": 4063.24, "high": 4066.86, "kind": "LIMIT", "order_type": "SELL_LIMIT", "current_price": 4055.94, "distance_points": 91.0},
            "stop_loss": 4105.05,
            "tp1": 4015.05,
            "tp2": 3975.05,
            "rr_ratio": 2.25,
            "entry_kind": "LIMIT",
            "order_type": "SELL_LIMIT",
        },
        "session_plan": {
            "plan_ready": True,
            "session_bias": "SELL",
            "planner_confidence": 96.3,
            "planner_grade": "A+",
            "authority_state": "CONFIRMED",
            "execution_preference": "LADDER_PENDING",
        },
        "trade_id": "TRADE_ADMISSION_MSG",
    }
    service.send_signal(decision)
    text = captured["text"]
    assert "Admission:" in text
    assert "objective-aligned continuation override" in text
