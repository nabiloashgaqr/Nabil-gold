"""Tests for:
  * smart entry classification (MARKET / LIMIT / STOP) in RiskManagementAgent
  * the Telegram TRADE PLAN rendering of order type + per-line TPs (no R:R)
  * the real trade-id (no 'PENDING_' placeholder) flow in the DB layer
"""

from __future__ import annotations

import re
from typing import Any, Dict

from agents.risk_management_agent import RiskManagementAgent
from services.database import DatabaseService
from services.telegram_bot import TelegramService


def _base_buy_results() -> Dict[str, Any]:
    return {
        "current_price": 2350.0,
        "spread_points": 2.0,
        "technical": {"signal": "BUY", "confidence": 70},
        "classical": {"signal": "BUY", "confidence": 65},
        "smc": {"signal": "BUY", "confidence": 60, "entry_suggestion": {}},
        "price_action": {"signal": "BUY", "confidence": 62},
        "multitimeframe": {"signal": "BUY", "confidence": 64},
        "atr": 8.0,
        "portfolio": {"open_trades_count": 0, "today_signals_count": 0, "consecutive_losses": 0},
    }


_CFG = {
    "risk_settings": {"min_rr_ratio": 1.0, "max_open_trades": 3, "min_sl_distance_points": 200,
                      "atr_multiplier_sl": 1.5, "atr_multiplier_tp1": 2.0, "atr_multiplier_tp2": 3.5,
                      "max_rr_ratio": 4.0},
    "filters": {"min_atr_for_entry": 1.0, "max_spread_points": 5, "max_consecutive_losses": 3},
    # Gold-calibrated band: 1 point = 0.10$ -> 60..350 points = 6$..35$.
    "order_execution": {"pending_threshold_points": 1.0,
                        "smart_entry": {"enabled": True, "min_pullback_points": 60,
                                        "max_pullback_points": 350, "atr_fraction": 0.5}},
}


# ── Smart entry classification ─────────────────────────────────────────────
def test_market_when_no_level_nearby():
    e = RiskManagementAgent(_CFG).evaluate(_base_buy_results()).get("entry", {})
    assert e.get("kind") == "MARKET"
    assert e.get("order_type") == "BUY_MARKET"


def test_limit_at_support_within_band():
    results = _base_buy_results()
    # Support 15$ below price = 150 points -> inside the 60..350 gold band.
    results["technical"] = {"signal": "BUY", "confidence": 70, "key_levels": {"nearest_support": 2335.0}}
    e = RiskManagementAgent(_CFG).evaluate(results).get("entry", {})
    assert e.get("kind") == "LIMIT"
    assert e.get("order_type") == "BUY_LIMIT"
    assert abs(float(e.get("price")) - 2335.0) < 0.01


def test_market_when_level_too_close():
    results = _base_buy_results()
    # Support only 2$ (20 points) away -> below the 60-point minimum -> MARKET.
    results["technical"] = {"signal": "BUY", "confidence": 70, "key_levels": {"nearest_support": 2348.0}}
    e = RiskManagementAgent(_CFG).evaluate(results).get("entry", {})
    assert e.get("kind") == "MARKET"


def test_market_when_level_too_far():
    results = _base_buy_results()
    # Support 50$ (500 points) away -> beyond the 350-point maximum -> MARKET.
    results["technical"] = {"signal": "BUY", "confidence": 70, "key_levels": {"nearest_support": 2300.0}}
    e = RiskManagementAgent(_CFG).evaluate(results).get("entry", {})
    assert e.get("kind") == "MARKET"


def test_sell_limit_at_resistance():
    results = {
        "current_price": 4124.82, "spread_points": 2.0,
        # Resistance ~15$ (150 points) above price -> inside the band.
        "technical": {"signal": "SELL", "confidence": 70, "key_levels": {"nearest_resistance": 4140.0}},
        "classical": {"signal": "SELL", "confidence": 65},
        "smc": {"signal": "SELL", "confidence": 60, "entry_suggestion": {}},
        "price_action": {"signal": "SELL", "confidence": 62},
        "multitimeframe": {"signal": "SELL", "confidence": 64},
        "atr": 18.0,
        "portfolio": {"open_trades_count": 0, "today_signals_count": 0, "consecutive_losses": 0},
    }
    e = RiskManagementAgent(_CFG).evaluate(results).get("entry", {})
    assert e.get("kind") == "LIMIT"
    assert e.get("order_type") == "SELL_LIMIT"
    assert float(e.get("price")) > 4124.82  # resting above market


# ── Telegram TRADE PLAN rendering ──────────────────────────────────────────
def _render(decision: Dict[str, Any]) -> str:
    svc = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    cap: Dict[str, str] = {}
    svc.send_message = lambda text, urgent=False, **k: cap.__setitem__("t", text) or True  # type: ignore
    svc.send_signal(decision)
    return re.sub(r"</?(b|i|code)>", "", cap["t"])


def _market_decision() -> Dict[str, Any]:
    return {
        "decision": "SELL", "confidence": 64, "current_price": 4124.82,
        "signal": {"type": "SELL",
                   "entry": {"low": 4124.19, "high": 4125.45, "price": 4124.82,
                             "kind": "MARKET", "order_type": "SELL_MARKET",
                             "basis": "Immediate market entry", "current_price": 4124.82,
                             "distance_points": 0.0},
                   "stop_loss": 4144.82, "tp1": 4098.15, "tp2": 4078.15,
                   "order_type": "SELL_MARKET", "entry_kind": "MARKET"},
        "risk": {"stop_loss": {"distance_points": 200}},
        "ai": {"available": True, "signal": "SELL", "confidence": 64},
        "votes": {"SELL": [{"agent": "multitimeframe", "confidence": 64}], "WAIT": []},
        "trade_id": "TRADE_20260623_120035_123456_abcd1234",
    }


def test_market_plan_shows_market_and_zone():
    text = _render(_market_decision())
    assert "Market (immediate)" in text
    assert "Sell Market" in text
    assert "Entry zone:" in text


def test_limit_plan_shows_entry_and_market_now():
    d = _market_decision()
    # A LIMIT order with an explicit entry ZONE (4125-4135, mid 4130).
    d["signal"]["entry"].update({"kind": "LIMIT", "order_type": "SELL_LIMIT", "price": 4130.0,
                                 "low": 4125.0, "high": 4135.0,
                                 "basis": "Sell zone at nearest resistance", "distance_points": 52.0})
    d["signal"]["order_type"] = "SELL_LIMIT"
    d["signal"]["entry_kind"] = "LIMIT"
    text = _render(d)
    assert "Limit (pullback)" in text
    # Zone range + mid fill point both shown.
    assert "Entry zone: 4125.00 – 4135.00" in text
    assert "Fill @ 4130.00" in text
    assert "Market now: 4124.82" in text


def test_tps_on_separate_lines_without_rr():
    text = _render(_market_decision())
    lines = [l.strip() for l in text.split("\n")]
    assert "• TP1: 4098.15" in lines
    assert "• TP2: 4078.15" in lines
    # No R:R anywhere in the message anymore.
    assert "R:R" not in text


def test_no_pending_placeholder_in_id_when_real_id_given():
    text = _render(_market_decision())
    assert "PENDING_" not in text
    assert "TRADE_20260623" in text


# ── DB id reuse (no PENDING in stored id) ──────────────────────────────────
def test_save_trade_reuses_real_id(tmp_path):
    cfg = {"database": {"provider": "local", "local_fallback_file": str(tmp_path / "trades.json")}}
    db = DatabaseService(cfg)
    real_id = db.new_trade_id()
    assert real_id.startswith("TRADE_")
    decision = {
        "decision": "BUY", "confidence": 70, "current_price": 2350.0,
        "trade_id": real_id,
        "signal": {"type": "BUY", "entry": {"price": 2350.0}, "stop_loss": 2344.0, "tp1": 2356.0, "tp2": 2362.0,
                   "trade_id": real_id},
    }
    saved = db.save_trade(decision)
    assert saved == real_id


def test_save_trade_replaces_pending_id(tmp_path):
    cfg = {"database": {"provider": "local", "local_fallback_file": str(tmp_path / "trades.json")}}
    db = DatabaseService(cfg)
    decision = {
        "decision": "BUY", "confidence": 70, "current_price": 2350.0,
        "trade_id": "PENDING_20260623_120000",
        "signal": {"type": "BUY", "entry": {"price": 2350.0}, "stop_loss": 2344.0, "tp1": 2356.0, "tp2": 2362.0},
    }
    saved = db.save_trade(decision)
    assert saved.startswith("TRADE_")
    assert "PENDING" not in saved
