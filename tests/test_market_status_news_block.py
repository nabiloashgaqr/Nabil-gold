"""Market Status formatting — simplified status with open trades."""

from __future__ import annotations

from scripts.run_analysis import _build_market_status_message


class _DB:
    def get_open_trades(self):
        return []


class _DBWithTrade:
    def get_open_trades(self):
        return [
            {
                "id": "TRADE_20260703_050059_480384_8e102119",
                "type": "BUY",
                "symbol": "XAU/USD",
                "entry_price": 4178.78,
                "stop_loss": 4148.78,
                "tp1": 4218.78,
                "tp2": 4248.78,
                "status": "OPEN",
                "current_pnl_points": 120.0,
            }
        ]


def _technical_context() -> dict:
    return {
        "current_price": 4027.06,
        "technical": {
            "technical": {
                "rsi": 50.33,
                "key_levels": {"nearest_support": 4022.85, "nearest_resistance": 4034.3},
            }
        },
        "news": {"can_trade": False, "market_status": "HIGH_VOLATILITY"},
    }


def test_market_status_wait_no_open_trades() -> None:
    decision = {
        "decision": "WAIT",
        "confidence": 0,
        "current_price": 4027.06,
        "warnings": ["News blocked: Tier 1 event"],
    }
    msg = _build_market_status_message(decision, _technical_context(), _DB())
    assert "Decision: WAIT" in msg
    # No trades section when empty
    assert "Open Trades" not in msg


def test_market_status_wait_with_open_trades() -> None:
    decision = {
        "decision": "WAIT",
        "confidence": 45,
        "current_price": 4190.78,
    }
    ctx = _technical_context()
    ctx["news"] = {"can_trade": True, "market_status": "SAFE"}
    msg = _build_market_status_message(decision, ctx, _DBWithTrade())
    assert "Decision: WAIT" in msg
    assert "Open Trades (1)" in msg
    assert "BUY" in msg
    assert "#8e102119" in msg
    assert "pts" in msg
    assert "Net:" in msg


def test_market_status_includes_prices() -> None:
    decision = {
        "decision": "WAIT",
        "current_price": 4027.06,
    }
    msg = _build_market_status_message(decision, _technical_context(), _DB())
    assert "XAU/USD" in msg
    assert "Next update in ~1 hour" in msg
