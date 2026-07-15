from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.open_trades_manager import OpenTradesManager
from agents.risk_management_agent import RiskManagementAgent


def test_risk_management_uses_reversal_profile_and_liquidity_targets() -> None:
    agent = RiskManagementAgent(
        {
            "risk_settings": {"min_rr_ratio": 1.5, "min_sl_distance_points": 50},
            "agent_weights": {"technical": 0.2, "classical": 0.25, "smc": 0.2, "price_action": 0.2, "multitimeframe": 0.15},
        }
    )
    results = {
        "current_price": 4065.0,
        "atr": 2.0,
        "technical": {"direction": "SELL", "confidence": 72},
        "classical": {"direction": "WAIT", "confidence": 0, "support_levels": [4057.0, 4021.4]},
        "smc": {
            "direction": "SELL",
            "confidence": 84,
            "entry_suggestion": {"sl": 4072.8},
            "liquidity": {"sell_side": [4057.0, 4021.4], "buy_side": [4073.6]},
            "order_blocks": [
                {"type": "bearish", "zone": {"top": 4071.8, "bottom": 4066.2}},
            ],
            "setup_structure": {"setup_type": "LIQUIDITY_REVERSAL", "lead_agent": "smc"},
        },
        "price_action": {"direction": "SELL", "confidence": 78},
        "multitimeframe": {"direction": "WAIT", "confidence": 50},
        "portfolio": {"open_trades_count": 0, "today_signals_count": 0, "consecutive_losses": 0},
    }
    res = agent.evaluate(results)
    assert res["management_profile"] == "reversal_profile"
    assert res["target_map"]["tp1_basis"] == "internal_liquidity"
    assert res["take_profit"]["tp1"]["price"] == 4057.0


def _base_trade(profile: str) -> dict:
    return {
        "id": f"TRADE_{profile}",
        "type": "SELL",
        "symbol": "XAU/USD",
        "entry_price": 4065.0,
        "entry_time": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
        "stop_loss": 4073.7,
        "tp1": 4045.0,
        "tp2": 4021.4,
        "status": "OPEN",
        "sl_moved_to_entry": False,
        "partial_close": False,
        "updates_sent": [],
        "signal_snapshot": {
            "risk": {"management_profile": profile},
            "setup_type": "LIQUIDITY_REVERSAL" if profile == "reversal_profile" else "TREND_CONTINUATION",
        },
    }


def test_open_trades_manager_reversal_profile_moves_be_earlier() -> None:
    mgr = OpenTradesManager(
        {
            "trade_management": {
                "early_breakeven_points": 150,
                "profiles": {
                    "reversal_profile": {"early_breakeven_points": 100, "trailing_distance_points": 120, "trailing_step_points": 30},
                    "continuation_profile": {"early_breakeven_points": 170, "trailing_distance_points": 170, "trailing_step_points": 45},
                },
            }
        }
    )
    trade = _base_trade("reversal_profile")
    evaluation = mgr.evaluate_trade(trade, current_price=4054.0, now=datetime.now(timezone.utc))
    assert "MOVE_SL_TO_BE" in evaluation["events"]
    assert evaluation["updates"]["sl_moved_to_entry"] is True
    assert evaluation["updates"]["stop_loss"] == 4065.0


def test_open_trades_manager_continuation_profile_keeps_more_room() -> None:
    mgr = OpenTradesManager(
        {
            "trade_management": {
                "early_breakeven_points": 150,
                "profiles": {
                    "reversal_profile": {"early_breakeven_points": 100},
                    "continuation_profile": {"early_breakeven_points": 170},
                },
            }
        }
    )
    trade = _base_trade("continuation_profile")
    evaluation = mgr.evaluate_trade(trade, current_price=4054.0, now=datetime.now(timezone.utc))
    assert "MOVE_SL_TO_BE" not in evaluation["events"]
    assert evaluation["updates"]["sl_moved_to_entry"] is False
