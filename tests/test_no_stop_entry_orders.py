"""Entries are MARKET or LIMIT. Never STOP.

Background
----------
``_planned_order_type`` priced an entry as a STOP order whenever the mapped
level sat on the wrong side of the current price:

    BUY  above the price -> BUY_STOP
    SELL below the price -> SELL_STOP

A STOP entry buys strength or sells weakness -- it chases. The 2026-07-29
disaster was exactly that shape: BUY STOP at 4028.77 placed above the market
while three qualified agents read SELL, and price went to 4009.

A LIMIT does the opposite: it sells into a rally or buys into a dip, at a
price better than the market. That is the discipline the operator asked for.

The rule now:

    entry better than the market  -> LIMIT  (wait at the level)
    entry at the market           -> MARKET
    entry worse than the market   -> MARKET at the current price

The last line is the operator's explicit choice. Rather than refuse the trade,
the system takes the entry it can get now, which is by definition better than
the STOP price it would otherwise have waited for. Nothing is chased: a SELL
that would have triggered lower is filled higher, and a BUY that would have
triggered higher is filled lower.

``order_execution.allowed_order_types`` already existed in config.json listing
all six kinds, and nothing read it. It is now honoured.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.decision_agent import DecisionAgent
from agents.risk_management_agent import RiskManagementAgent
from scripts.run_analysis import _planned_order_type

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
STOP_KINDS = {"BUY_STOP", "SELL_STOP"}


# ── The order type this repo must never emit again ─────────────────────────

def test_sell_below_market_is_not_a_stop() -> None:
    """The mapped SELL sits under the price: fill now, do not chase down.

    Failure injection: restoring ``"SELL_LIMIT" if entry > current else
    "SELL_STOP"`` makes this fail.
    """
    order = _planned_order_type(CONFIG, "SELL", 4027.50, 4044.0, "XAU/USD")
    assert order not in STOP_KINDS, f"got {order}"
    assert order == "SELL_MARKET", (
        f"an unreachable SELL level must fill at the market, got {order}"
    )


def test_buy_above_market_is_not_a_stop() -> None:
    """The mapped BUY sits above the price: fill now, do not chase up.

    This is the 12:21 shape that lost 198 points.
    """
    order = _planned_order_type(CONFIG, "BUY", 4028.77, 4021.96, "XAU/USD")
    assert order not in STOP_KINDS, f"got {order}"
    assert order == "BUY_MARKET"


def test_decision_agent_never_prices_a_stop() -> None:
    """The second place order types are decided must agree."""
    agent = DecisionAgent(CONFIG)
    assert agent._order_type("SELL", 4027.50, 4044.0) not in STOP_KINDS
    assert agent._order_type("BUY", 4028.77, 4021.96) not in STOP_KINDS


def test_risk_agent_never_prices_a_stop() -> None:
    """And the third."""
    agent = RiskManagementAgent(CONFIG)
    assert agent._classify_order("SELL", 4027.50, 4044.0) not in STOP_KINDS
    assert agent._classify_order("BUY", 4028.77, 4021.96) not in STOP_KINDS


def test_no_price_relationship_produces_a_stop() -> None:
    """Sweep the whole space: no combination may yield a STOP."""
    agent = DecisionAgent(CONFIG)
    risk = RiskManagementAgent(CONFIG)
    current = 4040.0
    for entry in (3950.0, 4000.0, 4035.0, 4039.9, 4040.0, 4040.1, 4045.0,
                  4080.0, 4150.0):
        for side in ("BUY", "SELL"):
            for order in (
                _planned_order_type(CONFIG, side, entry, current, "XAU/USD"),
                agent._order_type(side, entry, current),
                risk._classify_order(side, entry, current),
            ):
                assert order not in STOP_KINDS, (
                    f"{side} entry {entry} vs price {current} produced {order}"
                )


# ── Guards: LIMIT behaviour must be untouched ──────────────────────────────

def test_sell_above_market_is_still_a_limit() -> None:
    """Selling into strength is the whole point; it must survive."""
    order = _planned_order_type(CONFIG, "SELL", 4047.00, 4010.0, "XAU/USD")
    assert order == "SELL_LIMIT"


def test_buy_below_market_is_still_a_limit() -> None:
    """Buying the dip likewise."""
    order = _planned_order_type(CONFIG, "BUY", 4000.00, 4040.0, "XAU/USD")
    assert order == "BUY_LIMIT"


def test_entry_inside_the_threshold_is_still_market() -> None:
    """The existing market-threshold shortcut is unchanged."""
    order = _planned_order_type(CONFIG, "SELL", 4041.0, 4040.0, "XAU/USD")
    assert order == "SELL_MARKET"


def test_uploaded_chart_entry_prices_as_a_limit() -> None:
    """The 2026-07-29 swept level is above the market: a proper SELL LIMIT."""
    order = _planned_order_type(CONFIG, "SELL", 4047.00, 4044.0, "XAU/USD")
    assert order in {"SELL_LIMIT", "SELL_MARKET"}
    assert order not in STOP_KINDS


def test_allowed_order_types_config_excludes_stops() -> None:
    """The config must state the rule, not just the code."""
    allowed = set(
        (CONFIG.get("order_execution") or {}).get("allowed_order_types") or []
    )
    assert allowed, "allowed_order_types must be present"
    assert not (allowed & STOP_KINDS), (
        f"config still permits stop entries: {allowed & STOP_KINDS}"
    )
    assert {"BUY_MARKET", "SELL_MARKET", "BUY_LIMIT", "SELL_LIMIT"} <= allowed
