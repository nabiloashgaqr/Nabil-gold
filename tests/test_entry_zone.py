"""Tests for ZONE-based smart entry: order block / level zones, mid-fill, and
the stop placed behind the zone's distal edge."""

from __future__ import annotations

from agents.risk_management_agent import RiskManagementAgent
from utils.helpers import load_config

CFG = load_config()


def _sell(**over):
    res = {
        "current_price": 4068.24, "spread_points": 2.0,
        "technical": {"signal": "SELL", "confidence": 80},
        "classical": {"signal": "SELL", "confidence": 70},
        "smc": {"signal": "SELL", "confidence": 75, "entry_suggestion": {}},
        "price_action": {"signal": "SELL", "confidence": 65},
        "multitimeframe": {"signal": "SELL", "confidence": 78},
        "atr": 18.0,
        "portfolio": {"open_trades_count": 0, "today_signals_count": 0, "consecutive_losses": 0},
    }
    res.update(over)
    return RiskManagementAgent(CFG).evaluate(res)


def test_order_block_zone_used():
    r = _sell(smc={"signal": "SELL", "confidence": 75, "entry_suggestion": {},
                   "order_blocks": [{"type": "bearish", "zone": {"top": 4105.0, "bottom": 4095.0}}]})
    e = r["entry"]
    assert e["zone"]["source"] == "smc"
    assert e["zone"]["low"] == 4095.0 and e["zone"]["high"] == 4105.0
    # Mid fill.
    assert abs(e["price"] - 4100.0) < 0.01
    assert e["order_type"] == "SELL_LIMIT"


def test_stop_behind_distal_edge():
    r = _sell(smc={"signal": "SELL", "confidence": 75, "entry_suggestion": {},
                   "order_blocks": [{"type": "bearish", "zone": {"top": 4105.0, "bottom": 4095.0}}]})
    # SL must be ABOVE the distal edge (4105) for a SELL, not inside the zone.
    assert r["stop_loss"]["price"] > 4105.0


def test_level_zone_synthesized_when_no_order_block():
    r = _sell(technical={"signal": "SELL", "confidence": 80, "key_levels": {"nearest_resistance": 4083.0}})
    e = r["entry"]
    assert e["zone"]["source"] == "level"
    # 50-pt (5$) wide zone around 4083 -> 4080.5..4085.5, mid 4083.
    assert abs(e["price"] - 4083.0) < 0.01
    assert e["zone"]["high"] - e["zone"]["low"] > 0


def test_market_when_no_zone():
    r = _sell()  # no order block, no nearby level
    e = r["entry"]
    assert e["kind"] == "MARKET"
    assert e["zone"]["source"] == "market"


def test_buy_order_block_zone_below_price():
    res = {
        "current_price": 4100.0, "spread_points": 2.0,
        "technical": {"signal": "BUY", "confidence": 80},
        "classical": {"signal": "BUY", "confidence": 70},
        "smc": {"signal": "BUY", "confidence": 75, "entry_suggestion": {},
                "order_blocks": [{"type": "bullish", "zone": {"top": 4090.0, "bottom": 4080.0}}]},
        "price_action": {"signal": "BUY", "confidence": 65},
        "multitimeframe": {"signal": "BUY", "confidence": 78},
        "atr": 18.0,
        "portfolio": {"open_trades_count": 0, "today_signals_count": 0, "consecutive_losses": 0},
    }
    r = RiskManagementAgent(CFG).evaluate(res)
    e = r["entry"]
    assert e["zone"]["source"] == "smc"
    assert abs(e["price"] - 4085.0) < 0.01  # mid of 4080-4090
    assert e["order_type"] == "BUY_LIMIT"
    # SL below distal edge 4080.
    assert r["stop_loss"]["price"] < 4080.0


def test_fill_at_edge_config(monkeypatch):
    import copy
    cfg = copy.deepcopy(CFG)
    cfg["order_execution"]["smart_entry"]["fill_at"] = "edge"
    res = {
        "current_price": 4068.24, "spread_points": 2.0,
        "technical": {"signal": "SELL", "confidence": 80},
        "classical": {"signal": "SELL", "confidence": 70},
        "smc": {"signal": "SELL", "confidence": 75, "entry_suggestion": {},
                "order_blocks": [{"type": "bearish", "zone": {"top": 4105.0, "bottom": 4095.0}}]},
        "price_action": {"signal": "SELL", "confidence": 65},
        "multitimeframe": {"signal": "SELL", "confidence": 78},
        "atr": 18.0,
        "portfolio": {"open_trades_count": 0, "today_signals_count": 0, "consecutive_losses": 0},
    }
    e = RiskManagementAgent(cfg).evaluate(res)["entry"]
    # proximal edge for a SELL pullback = bottom (4095), price hits it first.
    assert abs(e["price"] - 4095.0) < 0.01
