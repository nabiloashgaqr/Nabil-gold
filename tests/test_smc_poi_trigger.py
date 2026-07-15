from __future__ import annotations

from agents.risk_management_agent import RiskManagementAgent
from agents.smc_agent import SMCAgent


def test_primary_poi_prefers_fresh_strong_order_block_over_weaker_fvg() -> None:
    agent = SMCAgent({"symbol": "XAU/USD"})
    poi = agent._primary_poi(
        direction="SELL",
        current_price=4065.0,
        atr=2.0,
        order_blocks=[
            {
                "type": "bearish",
                "zone": {"top": 4071.8, "bottom": 4066.2},
                "strength": "strong",
                "created_at": "2026-07-15T10:00:00Z",
                "mitigation_status": "FRESH",
                "displacement_atr": 2.3,
                "displacement_quality": "STRONG",
                "invalidated": False,
            }
        ],
        fvg=[
            {
                "type": "bearish",
                "zone": {"top": 4069.0, "bottom": 4067.5},
                "strength": "medium",
                "created_at": "2026-07-15T10:05:00Z",
                "partial_fill": True,
                "filled": False,
                "size": 1.5,
            }
        ],
        dealing_range={"high": 4075.0, "low": 4045.0},
        market_structure={"trend": "BEARISH"},
        sweep={"type": "buy_side", "confirmation": "STRONG", "occurred": True},
    )
    assert poi is not None
    assert poi["poi_type"] == "order_block"
    assert float(poi["rank_score"]) > 30
    assert any("order_block" in reason for reason in poi["rank_reasons"])


def test_trigger_signal_confirms_bearish_rejection_at_poi() -> None:
    agent = SMCAgent({"symbol": "XAU/USD", "smc_engine": {"trigger_logic": {"market_entry_min_trigger_score": 70}}})
    trigger = agent._trigger_signal(
        direction="SELL",
        poi={"zone": {"top": 4071.8, "bottom": 4066.2}},
        candles=[
            {"open": 4062.0, "high": 4065.0, "low": 4060.5, "close": 4064.0},
            {"open": 4069.4, "high": 4070.6, "low": 4063.8, "close": 4064.5},
        ],
        current_price=4064.5,
        atr=2.0,
    )
    assert trigger["state"] == "REJECTION_CONFIRMED"
    assert trigger["market_ready"] is True
    assert trigger["execution_hint"] == "MARKET"
    assert float(trigger["score"]) >= 70


def test_hybrid_entry_uses_market_when_trigger_confirmed() -> None:
    agent = RiskManagementAgent(
        {
            "symbol": "XAU/USD",
            "order_execution": {
                "entry_style": "hybrid",
                "market_threshold_points": 30,
                "smart_entry": {"enabled": True, "fill_at": "mid", "zone_width_points": 50, "min_pullback_points": 60, "max_pullback_points": 350},
            },
            "risk_settings": {"min_rr_ratio": 1.5, "max_open_trades": 3, "min_sl_distance_points": 50},
            "filters": {"min_atr_for_entry": 1.0, "max_spread_points": 5, "max_consecutive_losses": 3},
            "agent_weights": {"technical": 0.2, "classical": 0.25, "smc": 0.2, "price_action": 0.2, "multitimeframe": 0.15},
        }
    )
    results = {
        "current_price": 4067.0,
        "atr": 2.0,
        "spread_points": 2.0,
        "technical": {"direction": "SELL", "confidence": 74},
        "classical": {"direction": "WAIT", "confidence": 0, "resistance_levels": [4072.0], "support_levels": [4057.0]},
        "smc": {
            "direction": "SELL",
            "confidence": 86,
            "entry_suggestion": {"sl": 4073.2, "tp": 4057.0},
            "setup_structure": {
                "setup_type": "LIQUIDITY_REVERSAL",
                "poi_type": "order_block",
                "poi_zone": {"top": 4071.8, "bottom": 4066.2},
                "trigger_state": "REJECTION_CONFIRMED",
                "trigger_ready": True,
                "trigger_score": 84,
                "execution_hint": "MARKET",
            },
            "order_blocks": [
                {"type": "bearish", "zone": {"top": 4071.8, "bottom": 4066.2}},
            ],
            "liquidity": {"sell_side": [4057.0, 4021.4]},
        },
        "price_action": {"direction": "SELL", "confidence": 78},
        "multitimeframe": {"direction": "WAIT", "confidence": 55},
        "portfolio": {"open_trades_count": 0, "today_signals_count": 0, "consecutive_losses": 0},
    }
    result = agent.evaluate(results)
    assert result["entry"]["kind"] == "MARKET"
    assert result["entry"]["order_type"] == "SELL_MARKET"
    assert "trigger confirmed" in result["entry"]["basis"].lower()
