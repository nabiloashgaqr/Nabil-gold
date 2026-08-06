"""The day-map card must name the near and far liquidity as numbers.

Operator directive (2026-08-04): "I want the map publication message to carry
the near and far liquidity, as numbers, so I know." The line reads the same
levels the execution resolvers aim at (primary_poi.details.liquidity), so the
card never advertises pools the engine will not use.

FAULT INJECTION: delete the LIQUIDITY block from `send_session_plan` in
`services/telegram_bot.py` and these tests fail -- the card goes silent about
the very numbers this directive asked for.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.telegram_bot import TelegramService


def _capture(monkeypatch) -> tuple:
    sent: dict = {}

    def _send(text: str, urgent: bool = False, chat_id=None) -> bool:
        sent["text"] = text
        return True

    service = TelegramService({"telegram": {}})
    monkeypatch.setattr(service, "send_message", _send)
    return service, sent


def _buy_plan() -> dict:
    return {
        "symbol": "XAU/USD",
        "session_bias": "BUY",
        "session_label": "Asia Morning",
        "session_quality": "HIGH",
        "planner_confidence": 97.8,
        "planner_grade": "A+",
        "authority_state": "WEAK",
        "scenario_type": "FAILED_RECLAIM_CONTINUATION",
        "primary_entry_zone": {"low": 4057.95, "high": 4063.95},
        "primary_entry_price": 4060.95,
        "invalidation_level": 4045.95,
        "target_liquidity": 4090.00,
        "plan_ready": True,
        "plan_status": "READY",
        # 4055.00 sits BEHIND the entry and must never be advertised ahead.
        "primary_poi": {
            "details": {"liquidity": {"buy_side": [4055.00, 4075.00, 4090.00, 4105.00]}},
        },
    }


def test_buy_card_shows_near_and_far_liquidity_with_r_multiples(monkeypatch) -> None:
    service, sent = _capture(monkeypatch)
    service.send_session_plan(_buy_plan())
    text = sent["text"]

    line = next(l for l in text.split("\n") if "LIQUIDITY" in l)
    # Directive 2026-08-06: only nearest + the one after it.
    # risk = 150 pts; near 4075 = 0.9R, next 4090 = 1.9R. Far 4105 not shown.
    assert "Near 4075.00 (0.9R)" in line
    assert "Next 4090.00 (1.9R)" in line
    assert "4105.00" not in line
    assert "pools ahead" not in line
    # The behind-entry pool is not an objective.
    assert "4055.00" not in line


def test_sell_card_reads_the_sell_side_below_entry(monkeypatch) -> None:
    plan = _buy_plan()
    plan.update({
        "session_bias": "SELL",
        "primary_entry_price": 4022.00,
        "invalidation_level": 4045.00,
        "target_liquidity": 3965.00,
        "primary_poi": {
            "details": {"liquidity": {"sell_side": [4030.00, 4010.00, 3995.00, 3965.00]}},
        },
    })
    service, sent = _capture(monkeypatch)
    service.send_session_plan(plan)
    text = sent["text"]

    line = next(l for l in text.split("\n") if "LIQUIDITY" in l)
    # risk = 23.0; near 4010 = 0.5R, next 3995 = 1.2R. Far 3965 not shown.
    assert "Near 4010.00 (0.5R)" in line
    assert "Next 3995.00 (1.2R)" in line
    assert "3965.00" not in line
    assert "4030.00" not in line  # behind a SELL entry, not an objective


def test_card_without_liquidity_still_delivers_silently_about_it(monkeypatch) -> None:
    plan = _buy_plan()
    plan["primary_poi"] = {"details": {}}
    service, sent = _capture(monkeypatch)
    assert service.send_session_plan(plan)
    assert "LIQUIDITY" not in sent["text"]
    assert "TARGETS" in sent["text"]  # the card itself is intact


def test_single_pool_shows_near_only(monkeypatch) -> None:
    plan = _buy_plan()
    plan["primary_poi"] = {"details": {"liquidity": {"buy_side": [4090.00]}}}
    service, sent = _capture(monkeypatch)
    service.send_session_plan(plan)
    line = next(l for l in sent["text"].split("\n") if "LIQUIDITY" in l)
    assert "Near 4090.00 (1.9R)" in line
    assert "Far " not in line
