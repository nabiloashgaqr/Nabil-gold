"""Entry zone tests (updated after pending/limit/stop removal).

All entries are now strictly MARKET.
Zone logic is used only for SL/TP placement.
"""
import pytest
from agents.risk_management_agent import RiskManagementAgent

def test_market_entry_always():
    cfg = {"risk_settings": {"min_rr_ratio": 1.5, "min_sl_distance_points": 200}}
    agent = RiskManagementAgent(cfg)
    # Simulate results
    results = {
        "current_price": 4100.0,
        "technical": {"direction": "BUY"},
        "smc": {"direction": "BUY"},
    }
    res = agent.evaluate(results)
    assert res["entry"]["kind"] == "MARKET"
    assert res["entry"]["order_type"] == "BUY_MARKET"
    assert "MARKET" in res["entry"]["order_type"]
