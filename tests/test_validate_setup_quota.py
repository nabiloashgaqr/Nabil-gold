"""Guards that setup validation does not burn Twelve Data quota."""

from __future__ import annotations

import scripts.validate_setup as vs


def test_analyze_validation_skips_live_twelvedata_quota_test(monkeypatch, capsys):
    """validate_setup.py must only check that the key exists.

    The real market-data call happens once in run_analysis.py and is reused for
    analysis + open-trade updates. Calling Twelve Data from validation would add
    a second quota hit every 5 minutes.
    """
    for name in vs.REQUIRED_BY_MODE["analyze"]:
        monkeypatch.setenv(name, "dummy-secret")
    monkeypatch.setattr(vs.sys, "argv", ["validate_setup.py", "analyze"])

    def _should_not_call_live_api():  # pragma: no cover - executed only on regression
        raise AssertionError("validate_setup must not call Twelve Data live API")

    monkeypatch.setattr(vs, "_test_twelvedata_key", _should_not_call_live_api)
    assert vs.main() == 0
    out = capsys.readouterr().out
    assert "live quota test skipped" in out


def test_trade_update_extremes_prefer_latest_5m_payload():
    import scripts.run_analysis as ra

    high, low = ra._latest_candle_extremes({
        "current_price": 100.0,
        "data": [{"high": 999.0, "low": 1.0}],  # primary 15m fallback, should be ignored
        "timeframes": {
            "5m": {"data": [
                {"high": 101.0, "low": 99.0},
                {"high": 103.0, "low": 98.5},
            ]}
        },
    })
    assert high == 103.0
    assert low == 98.5
