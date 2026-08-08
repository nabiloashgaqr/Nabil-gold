"""Fault-injection: the VPS must NEVER signal from synthetic candles.

Server-only era: EXECUTION_MODE is set (paper|mt5_demo) and GITHUB_ACTIONS is
not. If the MT5 feed is down and there is no TwelveData key, the cycle must
STOP CLEANLY (return None) — exactly like the old GitHub production guard.
Silent synthetic candles would push garbage signals to the subscribers
channel. These tests must FAIL if the guard keys only on GITHUB_ACTIONS again.

NOTE: the env scrub happens INSIDE each test body (not in a fixture) because
pytest re-stamps PYTEST_CURRENT_TEST when the call phase starts, after every
fixture has run.
"""
import services.mt5_feed as mt5_feed
from services.market_data import MarketDataService


def _enter_vps_env(monkeypatch):
    # Exactly what the VPS .bat wrappers set — and what they do NOT set.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("PYTEST_RUNNING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    monkeypatch.setenv("EXECUTION_MODE", "mt5_demo")
    monkeypatch.setenv("DATA_SOURCE_PRIMARY", "mt5")


def test_vps_blocks_synthetic_when_all_feeds_are_down(monkeypatch):
    _enter_vps_env(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("MT5 terminal down")

    monkeypatch.setattr(mt5_feed, "get_candles", _boom)
    svc = MarketDataService({"symbol": "XAU/USD", "data_source": {"api_keys": {}}})
    payload = svc.get_ohlcv(timeframe="5m", outputsize=50)
    assert payload is None  # stopped cleanly — no synthetic signal data


def test_no_twelvedata_key_means_no_twelvedata_call(monkeypatch):
    _enter_vps_env(monkeypatch)
    calls = []

    def _boom(*a, **k):
        raise RuntimeError("MT5 terminal down")

    def _fake_fetch(*a, **k):
        calls.append(1)
        return {"data": []}

    monkeypatch.setattr(mt5_feed, "get_candles", _boom)
    svc = MarketDataService({"symbol": "XAU/USD", "data_source": {"api_keys": {}}})
    monkeypatch.setattr(svc, "_fetch_data", _fake_fetch)
    assert svc.get_ohlcv(timeframe="5m", outputsize=50) is None
    assert calls == []  # without a key, TwelveData is never called


def test_local_dev_still_gets_synthetic_when_unconfigured(monkeypatch):
    """No EXECUTION_MODE + no GITHUB_ACTIONS = local dev → synthetic allowed.

    The guard must not break manual/offline experiments.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("PYTEST_RUNNING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    monkeypatch.setenv("DATA_SOURCE_PRIMARY", "twelvedata")
    svc = MarketDataService({"symbol": "XAU/USD", "data_source": {"api_keys": {}}})
    payload = svc.get_ohlcv(timeframe="5m", outputsize=50)
    assert payload is not None
    assert str(payload.get("source") or "") != "twelvedata"
