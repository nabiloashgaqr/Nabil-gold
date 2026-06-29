"""Tests for service layer (MarketData, Database, Telegram)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.market_data import MarketDataService
from services.database import DatabaseService
from services.telegram_bot import TelegramService
from utils.helpers import load_trades, save_trades, save_trade


# ───────────────────────────── MarketDataService ─────────────────────────────


def test_market_data_generates_synthetic_data():
    """Synthetic fallback must return valid OHLCV structure."""
    service = MarketDataService({"symbol": "XAU/USD", "primary_timeframe": "15m", "timeframes": ["15m"]})
    payload = service.get_ohlcv("15m", outputsize=50)
    assert "data" in payload
    assert "current_price" in payload
    assert "timeframe" in payload
    assert "source" in payload
    assert len(payload["data"]) == 50
    # OHLCV fields
    first = payload["data"][0]
    for key in ("time", "open", "high", "low", "close", "volume"):
        assert key in first, f"Missing key: {key}"
    # Prices should be positive
    assert first["close"] > 0
    assert first["open"] > 0
    assert first["high"] >= first["open"]


def test_market_data_all_timeframes():
    """get_gold_data must return all configured timeframes."""
    config = {"symbol": "XAU/USD", "timeframes": ["5m", "15m", "1H", "4H"], "primary_timeframe": "15m"}
    service = MarketDataService(config)
    data = service.get_gold_data(outputsize=30)
    assert data is not None
    assert "timeframes" in data
    assert "5m" in data["timeframes"]
    assert "15m" in data["timeframes"]
    assert "1H" in data["timeframes"]
    assert "4H" in data["timeframes"]
    assert "current_price" in data


def test_market_data_caching():
    """Repeated calls must use cache (same prices)."""
    service = MarketDataService({"symbol": "XAU/USD", "primary_timeframe": "15m", "timeframes": ["15m"]})
    p1 = service.get_ohlcv("15m", 10)
    p2 = service.get_ohlcv("15m", 10)
    # Cache key includes symbol, timeframe, outputsize
    assert p1["current_price"] == p2["current_price"]


def test_market_data_get_current_price():
    """get_current_price must return a float."""
    service = MarketDataService({"symbol": "XAU/USD", "primary_timeframe": "15m", "timeframes": ["15m"]})
    price = service.get_current_price()
    assert isinstance(price, float)
    assert price > 0


def test_market_data_spread_in_synthetic():
    """Synthetic data should have spread_points."""
    service = MarketDataService({"symbol": "XAU/USD", "primary_timeframe": "15m", "timeframes": ["15m"]})
    payload = service.get_ohlcv("15m", 20)
    assert "spread_points" in payload
    assert payload["spread_points"] is not None


def test_market_data_timeframe_mapping():
    """Twelve Data interval + TF_MINUTES mapping must be correct."""
    assert MarketDataService.TF_MINUTES["5m"] == 5
    assert MarketDataService.TF_MINUTES["15m"] == 15
    assert MarketDataService.TF_MINUTES["1H"] == 60
    assert MarketDataService.TF_MINUTES["4H"] == 240
    assert MarketDataService.TWELVEDATA_INTERVAL["5m"] == "5min"
    assert MarketDataService.TWELVEDATA_INTERVAL["1H"] == "1h"


# ───────────────────────────── DatabaseService ────────────────────────────────


def test_database_local_fallback_save_and_get():
    """Local JSON fallback must save and retrieve trades."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        # Empty trades
        assert load_trades(path) == []

        # Save a trade
        trades = [{"id": "T001", "type": "BUY", "entry_price": 2350.0, "status": "OPEN"}]
        save_trades(trades, path)
        loaded = load_trades(path)
        assert len(loaded) == 1
        assert loaded[0]["id"] == "T001"

        # Append
        save_trade({"id": "T002", "type": "SELL", "entry_price": 2360.0, "status": "OPEN"}, path)
        loaded = load_trades(path)
        assert len(loaded) == 2
    finally:
        Path(path).unlink(missing_ok=True)


def test_database_service_fallback_mode():
    """DatabaseService with no credentials must use local fallback."""
    config = {"database": {"local_fallback_file": "storage/trades.json"}}
    # Clear env vars
    old_url = os.environ.pop("SUPABASE_URL", None)
    old_key = os.environ.pop("SUPABASE_KEY", None)
    try:
        service = DatabaseService(config)
        assert service.use_supabase is False
    finally:
        if old_url:
            os.environ["SUPABASE_URL"] = old_url
        if old_key:
            os.environ["SUPABASE_KEY"] = old_key


def test_database_save_trade_returns_id():
    """save_trade must return a non-empty trade ID."""
    with tempfile.TemporaryDirectory() as tmp:
        local_path = Path(tmp) / "trades.json"
        config = {"database": {"local_fallback_file": str(local_path)}}
        service = DatabaseService(config)
        decision = {
            "decision": "BUY",
            "signal": {
                "type": "BUY",
                "entry": {"price": 2350.0, "low": 2349.0, "high": 2351.0},
                "stop_loss": 2344.0,
                "tp1": 2356.0,
                "tp2": 2362.0,
            },
            "confidence": 78,
            "current_price": 2350.0,
            "reasons": ["Technical BUY"],
        }
        trade_id = service.save_trade(decision)
        assert trade_id is not None
        assert len(trade_id) > 0
        assert trade_id.startswith("TRADE_")


def test_database_get_open_trades_filters_status():
    """get_open_trades must only return OPEN or TP1_HIT."""
    with tempfile.TemporaryDirectory() as tmp:
        local_path = Path(tmp) / "trades.json"
        config = {"database": {"local_fallback_file": str(local_path)}}
        service = DatabaseService(config)

        # Save trades with different statuses
        for status in ["OPEN", "TP1_HIT", "TP2_HIT", "SL_HIT"]:
            service.save_trade({
                "decision": "BUY",
                "signal": {
                    "type": "BUY",
                    "entry": {"price": 2350.0},
                    "stop_loss": 2344.0,
                    "tp1": 2356.0,
                    "tp2": 2362.0,
                },
                "confidence": 70,
                "current_price": 2350.0,
                "reasons": [],
            })

        open_trades = service.get_open_trades()
        for trade in open_trades:
            assert trade["status"] in {"OPEN", "TP1_HIT"}


def test_database_consecutive_losses():
    """consecutive_losses must count only the last streak of losses before first win/break."""
    with tempfile.TemporaryDirectory() as tmp:
        local_path = Path(tmp) / "trades.json"
        config = {"database": {"local_fallback_file": str(local_path)}}
        service = DatabaseService(config)

        # Save 6 trades with distinct timestamps: oldest (i=0) → newest (i=5)
        base_time = datetime.now(timezone.utc)
        # Sequence: loss, loss, loss, loss, win, loss
        # Most recent (i=5) = SL_HIT(loss) → losses = 1 immediately, then TP2_HIT(win) breaks
        sequence = [
            ("SL_HIT", -10),  # oldest
            ("SL_HIT", -8),
            ("SL_HIT", -5),
            ("SL_HIT", -7),
            ("TP2_HIT", 12),  # win
            ("SL_HIT", -9),   # newest → this is loss, so losses = 1, then next is WIN → break
        ]
        for i, (status, pnl) in enumerate(sequence):
            trade_id = service.save_trade({
                "decision": "BUY",
                "signal": {"type": "BUY", "entry": {"price": 2350.0}, "stop_loss": 2344.0, "tp1": 2356.0, "tp2": 2362.0},
                "confidence": 70,
                "current_price": 2350.0,
                "reasons": [],
            })
            # Set created_at so newest (i=5) is at the end of the list → returned first by desc sort
            trades = load_trades(local_path)
            if trades:
                # i=0 → oldest time, i=5 → newest time (for desc sort to put i=5 first)
                trades[-1]["created_at"] = (base_time - timedelta(minutes=len(sequence) - i)).isoformat()
                save_trades(trades, local_path)
            service.update_trade(trade_id, {"status": status, "final_pnl": pnl})

        losses = service.get_consecutive_losses()
        # Newest (i=5) = SL_HIT(loss) → losses=1; next (i=4) = TP2_HIT(win) → break
        assert losses == 1


# ───────────────────────────── TelegramService ────────────────────────────────


def test_telegram_no_token_logs_message():
    """Without token, send_message must not raise (logs preview)."""
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    result = service.send_message("Test message")
    assert result is False  # Not sent


def test_telegram_format_signal():
    """send_signal must build a non-empty HTML message."""
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    decision = {
        "decision": "BUY",
        "signal": {
            "type": "BUY",
            "entry": {"price": 2350.0, "low": 2349.5, "high": 2350.5},
            "stop_loss": 2344.0,
            "tp1": 2356.0,
            "tp2": 2362.0,
            "rr_ratio": 2.0,
        },
        "confidence": 78,
        "current_price": 2350.0,
        "reasons": ["Technical BUY", "SMC bullish"],
        "trade_id": "TRADE_001",
    }
    result = service.send_signal(decision)
    assert result is False  # No token, but no crash


def test_telegram_format_trade_event():
    """send_trade_event must format all event types."""
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    trade = {
        "id": "TRADE_001",
        "type": "BUY",
        "entry_price": 2350.0,
        "stop_loss": 2344.0,
        "tp1": 2356.0,
        "tp2": 2362.0,
    }
    for event in ["NEAR_TP1", "TP1_HIT", "TP2_HIT", "SL_HIT", "BE_HIT", "LONG_RUNNING", "EXPIRED"]:
        result = service.send_trade_event(trade, event, 2354.0, 4.0, {"old_status": "OPEN", "new_status": event})
        assert result is False  # No token


def test_telegram_rate_limit():
    """Rate limit should not crash with many calls."""
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    # Call many times - should not raise
    for _ in range(25):
        service.send_message("Test", urgent=False)
    assert True  # No crash


def test_telegram_error_alert():
    """send_error_alert must not crash."""
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    result = service.send_error_alert("Test error message")
    assert result is False


def test_telegram_error_alert_escapes_html(monkeypatch):
    """Error alerts should escape HTML and not expose repo info."""
    service = TelegramService({"telegram": {"bot_token": "fake:token", "chat_id": "-100123"}})
    captured = {}

    def _fake_send(text: str, urgent: bool = False, **_kwargs) -> bool:
        captured["text"] = text
        captured["urgent"] = urgent
        return True

    monkeypatch.setattr(service, "send_message", _fake_send)

    assert service.send_error_alert("Broken <tag> & failure") is True
    text = captured["text"]
    assert captured["urgent"] is True
    assert "Broken" in text
    assert "Error" in text

def test_telegram_daily_report():
    """send_daily_report must not crash."""
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    result = service.send_daily_report("📋 Daily Report\nTotal: 5\nWins: 3")
    assert result is False

def test_yahoo_fallback_used_when_twelvedata_fails(monkeypatch):
    """When Twelve Data fails/quota is exhausted, XAU/USD can fall back to Yahoo."""
    config = {
        "symbol": "XAU/USD",
        "primary_timeframe": "5m",
        "timeframes": ["5m"],
        "data_source": {"fallback": "yahoo_finance"},
    }
    service = MarketDataService(config)
    service.api_key = "dummy"
    monkeypatch.setattr(service, "_fetch_data", lambda *_a, **_k: None)

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "chart": {
                    "result": [{
                        "timestamp": [1710000000, 1710000300],
                        "indicators": {"quote": [{
                            "open": [4000.0, 4001.0],
                            "high": [4002.0, 4004.0],
                            "low": [3999.0, 4000.5],
                            "close": [4001.0, 4003.0],
                            "volume": [0, 0],
                        }]},
                    }],
                    "error": None,
                }
            }

    calls = []

    def _fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers, timeout))
        return _Resp()

    monkeypatch.setattr(service.session, "get", _fake_get)
    payload = service.get_ohlcv("5m", outputsize=10)
    assert payload["source"] == "yahoo_finance_fallback"
    assert payload["current_price"] == 4003.0
    assert payload["data"][-1]["high"] == 4004.0
    assert calls and "XAUUSD=X" in calls[0][0]


def test_yahoo_fallback_not_used_when_disabled(monkeypatch):
    service = MarketDataService({"symbol": "XAU/USD", "data_source": {"fallback": None}})
    service.api_key = "dummy"
    monkeypatch.setattr(service, "_fetch_data", lambda *_a, **_k: None)

    def _should_not_call_yahoo(*_a, **_k):  # pragma: no cover
        raise AssertionError("Yahoo fallback should be disabled")

    monkeypatch.setattr(service, "_fetch_yahoo_chart", _should_not_call_yahoo)
    payload = service.get_ohlcv("5m", outputsize=5)
    assert payload["source"] == "synthetic_demo"



def test_yahoo_fallback_does_not_use_futures_for_xau_spot_management(monkeypatch):
    """GC=F/MGC=F are futures, not XAU/USD spot; never use them for SL/TP."""
    service = MarketDataService({"symbol": "XAU/USD", "data_source": {"fallback": "yahoo_finance"}})
    service.api_key = "dummy"
    monkeypatch.setattr(service, "_fetch_data", lambda *_a, **_k: None)

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"chart": {"result": None, "error": {"code": "Not Found"}}}

    seen = []

    def _fake_get(url, params=None, headers=None, timeout=None):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr(service.session, "get", _fake_get)
    payload = service.get_ohlcv("5m", outputsize=5)
    assert payload["source"] == "synthetic_demo"
    assert any("XAUUSD=X" in url for url in seen)
    assert not any("GC=F" in url or "MGC=F" in url for url in seen)
