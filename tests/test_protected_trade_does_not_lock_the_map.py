"""A risk-free position must not lock the symbol to its day map.

`DirectionalAuthorityService` refuses an opposite idea while a trade is still
riding the confirmed map. That is right while the trade is exposed: a fresh
contradicting signal should not be opened against a position that can still
lose money.

But the check counted trades, not risk. On 2026-07-30 a BUY runner sat with
its stop carried to breakeven at 4062.05 and +191 points banked -- a position
whose worst possible outcome is zero. Because it was merely *open*, it closed
the map-retirement path, so five qualified agents reading SELL could neither
retire the BUY map nor plan against it. One risk-free trade vetoed the entire
book.

Risk is now measured from the prices themselves rather than from
`sl_moved_to_entry`, because the manager already distrusts that flag: older
rows carry it while `stop_loss` still shows the original wider stop
(open_trades_manager.py:733).

Fault injection: revert `live_opposite` to the plain status/direction filter
and `test_the_live_breakeven_runner_no_longer_blocks` fails.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.directional_authority import DirectionalAuthorityService

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOL = "XAU/USD"
ENTRY = 4062.05


def _config() -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _plan() -> dict:
    return {"authority_state": "CONFIRMED", "authority_direction": "BUY", "plan_ready": True}


def _unanimous_sell_decision() -> dict:
    """Five qualified agents reading SELL, but not a reversal-grade setup.

    This is the case the five flip conditions are designed to refuse, so the
    only way through is map retirement -- which is precisely what an open
    trade used to disable.
    """
    book = {
        name: {"direction": "SELL", "confidence": conf}
        for name, conf in (
            ("technical", 85), ("classical", 80), ("smc", 88),
            ("price_action", 82), ("multitimeframe", 90),
        )
    }
    return {
        "decision": "SELL", "symbol": SYMBOL, "confidence": 86,
        "agent_details": book,
        "setup_context": {
            "setup_type": "STRUCTURE_CONTINUATION",
            "trigger_state": "AT_POI_WAIT_TRIGGER",
            "trigger_score": 60, "sweep_side": "",
        },
    }


def _buy_trade(stop: float | None, status: str = "OPEN") -> dict:
    trade = {"id": "T", "type": "BUY", "status": status,
             "symbol": SYMBOL, "entry_price": ENTRY}
    if stop is not None:
        trade["stop_loss"] = stop
    return trade


def _review(trades):
    return DirectionalAuthorityService(_config()).review(
        _unanimous_sell_decision(), _plan(), trades,
    )


# ── the trade that prompted this ───────────────────────────────────────────

def test_the_live_breakeven_runner_no_longer_blocks() -> None:
    """Stop at entry: worst case zero, so it defends nothing."""
    assert _review([_buy_trade(ENTRY)])["action"] == "ALLOW_MAP_RETIRED"


def test_a_trailed_stop_in_profit_does_not_block() -> None:
    assert _review([_buy_trade(4067.00)])["action"] == "ALLOW_MAP_RETIRED"


def test_the_baseline_with_no_trades_is_unchanged() -> None:
    assert _review([])["action"] == "ALLOW_MAP_RETIRED"


# ── what must still block ──────────────────────────────────────────────────

def test_a_trade_still_at_risk_keeps_the_map() -> None:
    """The original protection, intact: real exposure still owns the symbol."""
    result = _review([_buy_trade(4043.57)])
    assert result["action"] == "BLOCK_OPPOSITE_LOCAL"
    assert "at-risk" in result["reason"]


def test_a_pending_order_counts_as_at_risk() -> None:
    """It is not filled yet, so it will open at full risk the moment it is."""
    assert _review([_buy_trade(ENTRY, status="PENDING")])["action"] == "BLOCK_OPPOSITE_LOCAL"


def test_a_missing_stop_price_fails_safe() -> None:
    """Unknown risk must never quietly unlock the map."""
    assert _review([_buy_trade(None)])["action"] == "BLOCK_OPPOSITE_LOCAL"


def test_a_sell_map_mirrors_the_rule() -> None:
    service = DirectionalAuthorityService(_config())
    plan = {"authority_state": "CONFIRMED", "authority_direction": "SELL", "plan_ready": True}
    book = {
        name: {"direction": "BUY", "confidence": conf}
        for name, conf in (("technical", 85), ("classical", 80), ("smc", 88))
    }
    decision = {
        "decision": "BUY", "symbol": SYMBOL, "confidence": 86, "agent_details": book,
        "setup_context": {"setup_type": "STRUCTURE_CONTINUATION",
                          "trigger_state": "AT_POI_WAIT_TRIGGER",
                          "trigger_score": 60, "sweep_side": ""},
    }
    protected = {"id": "S", "type": "SELL", "status": "OPEN", "symbol": SYMBOL,
                 "entry_price": 4047.76, "stop_loss": 4047.76}
    at_risk = {**protected, "stop_loss": 4062.76}

    assert service.review(dict(decision), dict(plan), [protected])["action"] == "ALLOW_MAP_RETIRED"
    assert service.review(dict(decision), dict(plan), [at_risk])["action"] == "BLOCK_OPPOSITE_LOCAL"


def test_one_at_risk_trade_among_protected_ones_still_blocks() -> None:
    assert _review([_buy_trade(ENTRY), _buy_trade(4043.57)])["action"] == "BLOCK_OPPOSITE_LOCAL"


# ── the message must not blame the wrong thing ─────────────────────────────

def test_the_refusal_says_a_protected_trade_was_not_the_cause() -> None:
    """A weak SELL is refused on its own merits, not by the runner."""
    service = DirectionalAuthorityService(_config())
    weak = {
        "decision": "SELL", "symbol": SYMBOL, "confidence": 55,
        "agent_details": {"technical": {"direction": "SELL", "confidence": 55},
                          "smc": {"direction": "BUY", "confidence": 80}},
        "setup_context": {"setup_type": "STRUCTURE_CONTINUATION",
                          "trigger_state": "AT_POI_WAIT_TRIGGER",
                          "trigger_score": 40, "sweep_side": ""},
    }
    result = service.review(weak, _plan(), [_buy_trade(ENTRY)])
    assert result["action"] == "BLOCK_OPPOSITE_LOCAL"
    assert "not what refused this" in result["reason"]


def test_retirement_reports_the_protected_runner() -> None:
    result = _review([_buy_trade(ENTRY)])
    assert "protected at breakeven" in result["reason"]


# ── a genuine reversal was never affected ──────────────────────────────────

def test_a_reversal_grade_flip_still_passes_either_way() -> None:
    service = DirectionalAuthorityService(_config())
    strong = {
        "decision": "SELL", "symbol": SYMBOL, "confidence": 92,
        "agent_details": {"smc": {"direction": "SELL", "confidence": 90}},
        "setup_context": {"setup_type": "LIQUIDITY_REVERSAL",
                          "trigger_state": "REJECTION_CONFIRMED",
                          "trigger_score": 78, "sweep_side": "buy_side"},
    }
    for trades in ([], [_buy_trade(ENTRY)], [_buy_trade(4043.57)]):
        assert service.review(dict(strong), _plan(), trades)["action"] == "ALLOW_REGIME_FLIP"
