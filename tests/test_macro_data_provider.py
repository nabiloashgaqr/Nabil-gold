"""Tests for MacroDataProvider (yfinance-only, no Twelve Data)."""

from unittest.mock import patch, MagicMock
from services.macro_data_provider import MacroDataProvider


def _make_yf_history(closes):
    """Create a mock yfinance DataFrame with given close prices."""
    import pandas as pd
    import numpy as np

    n = len(closes)
    dates = pd.date_range("2025-01-01", periods=n, freq="1h")
    data = {
        "Open": closes,
        "High": [c * 1.001 for c in closes],
        "Low": [c * 0.999 for c in closes],
        "Close": closes,
        "Volume": [1000] * n,
    }
    return pd.DataFrame(data, index=dates)


class TestMacroDataProvider:
    """Test MacroDataProvider with mocked yfinance data."""

    @patch("services.macro_data_provider._yf")
    @patch("services.macro_data_provider._YF_AVAILABLE", True)
    def test_build_context_with_yfinance_data(self, mock_yf):
        """build_context should populate all 6/7 macro fields from yfinance."""
        # Mock yfinance Ticker.history responses
        def mock_ticker(symbol):
            t = MagicMock()
            if symbol == "EURUSD=X":
                t.history.return_value = _make_yf_history([1.00, 1.02, 1.03])
            elif symbol == "GBPUSD=X":
                t.history.return_value = _make_yf_history([1.25, 1.27, 1.28])
            elif symbol == "USDJPY=X":
                t.history.return_value = _make_yf_history([150.0, 149.0, 148.5])
            elif symbol == "AUDUSD=X":
                t.history.return_value = _make_yf_history([0.65, 0.66, 0.665])
            elif symbol == "SPY":
                t.history.return_value = _make_yf_history([500.0, 510.0, 515.0])
            elif symbol == "^TNX":
                t.history.return_value = _make_yf_history([4.5, 4.6, 4.65])
            elif symbol == "^FVX":
                t.history.return_value = _make_yf_history([4.2, 4.3, 4.35])
            elif symbol == "^VIX":
                t.history.return_value = _make_yf_history([18.0, 17.5, 16.0])
            elif symbol == "DX-Y.NYB":
                t.history.return_value = _make_yf_history([104.0, 103.5, 103.2])
            elif symbol == "CL=F":
                t.history.return_value = _make_yf_history([75.0, 76.0, 76.5])
            elif symbol == "^IRX":
                t.history.return_value = _make_yf_history([5.2, 5.25, 5.3])
            else:
                t.history.return_value = _make_yf_history([100.0, 100.0, 100.0])
            return t

        mock_yf.Ticker.side_effect = mock_ticker

        provider = MacroDataProvider({})
        context = provider.build_context()

        # Should use yfinance as source
        assert context["source"] == "yfinance_macro_proxy"
        assert context["provider"] == "yfinance"

        # DXY trend should be computed from FX basket
        assert context["dxy_trend"] in {"rising", "falling", "flat", "unknown"}
        assert context["usd_trend"] == context["dxy_trend"]

        # Risk sentiment should be determined
        assert context["risk_sentiment"] in {"risk_on", "risk_off", "neutral"}

        # Macro fields should be populated
        assert context["us10y_trend"] in {"rising", "falling", "flat", "unknown"}
        assert context["real_yields_trend"] in {"rising", "falling", "flat", "unknown"}
        assert context["oil_trend"] in {"rising", "falling", "flat", "unknown"}
        assert context["fed_tone"] in {"hawkish", "dovish", "neutral", "unknown"}

        # inflation_surprise is always unknown (no free source)
        assert context["inflation_surprise"] == "unknown"

        # VIX level should be populated
        assert isinstance(context.get("vix_level"), (int, float, type(None)))

        # Zero credits used (yfinance is free)
        assert context["quota_policy"]["credits_used_estimate"] == 0
        assert context["quota_policy"]["free_daily_limit"] == "unlimited"

    @patch("services.macro_data_provider._YF_AVAILABLE", False)
    def test_yfinance_not_installed(self):
        """Should return empty context when yfinance is not installed."""
        provider = MacroDataProvider({})
        context = provider.build_context()

        assert context["source"] == "yfinance_macro_proxy"
        assert context["provider"] == "none"
        assert context["freshness"] == "UNKNOWN"
        assert "yfinance not installed" in context["errors"]

    def test_empty_context(self):
        """_empty_context should have all required fields."""
        ctx = MacroDataProvider._empty_context("test error")
        assert ctx["source"] == "yfinance_macro_proxy"
        assert ctx["dxy_trend"] == "unknown"
        assert ctx["risk_sentiment"] == "neutral"
        assert ctx["inflation_surprise"] == "unknown"
        assert "test error" in ctx["errors"]

    def test_trend_label(self):
        """_trend_label should classify values correctly."""
        assert MacroDataProvider._trend_label(0.20, up="rising", down="falling") == "rising"
        assert MacroDataProvider._trend_label(-0.20, up="rising", down="falling") == "falling"
        assert MacroDataProvider._trend_label(0.10, up="rising", down="falling") == "flat"
        assert MacroDataProvider._trend_label(0.15, up="rising", down="falling") == "rising"
        assert MacroDataProvider._trend_label(-0.15, up="rising", down="falling") == "falling"

    @patch("services.macro_data_provider._yf")
    @patch("services.macro_data_provider._YF_AVAILABLE", True)
    def test_risk_sentiment_vix_override(self, mock_yf):
        """VIX >= 25 should override neutral SPY to risk_off."""
        def mock_ticker(symbol):
            t = MagicMock()
            if symbol == "SPY":
                # SPY barely up → neutral from SPY alone
                t.history.return_value = _make_yf_history([500.0, 501.0, 502.0])
            elif symbol == "^VIX":
                # VIX at 27 → risk_off
                t.history.return_value = _make_yf_history([27.0, 26.5, 27.0])
            elif symbol in ("EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X"):
                t.history.return_value = _make_yf_history([1.0, 1.0, 1.0])
            elif symbol == "^TNX":
                t.history.return_value = _make_yf_history([4.5, 4.5, 4.5])
            elif symbol == "^FVX":
                t.history.return_value = _make_yf_history([4.2, 4.2, 4.2])
            elif symbol == "DX-Y.NYB":
                t.history.return_value = _make_yf_history([104.0, 104.0, 104.0])
            elif symbol == "CL=F":
                t.history.return_value = _make_yf_history([75.0, 75.0, 75.0])
            elif symbol == "^IRX":
                t.history.return_value = _make_yf_history([5.2, 5.2, 5.2])
            else:
                t.history.return_value = _make_yf_history([100.0, 100.0, 100.0])
            return t

        mock_yf.Ticker.side_effect = mock_ticker
        provider = MacroDataProvider({})
        context = provider.build_context()
        # VIX=27 should push neutral → risk_off
        assert context["risk_sentiment"] == "risk_off"

    @patch("services.macro_data_provider._yf")
    @patch("services.macro_data_provider._YF_AVAILABLE", True)
    def test_dxy_strongest_pair_override(self, mock_yf):
        """When FX average cancels out but one pair moved, use that pair."""
        def mock_ticker(symbol):
            t = MagicMock()
            if symbol == "EURUSD=X":
                # EUR up 0.3% → USD down 0.3%
                t.history.return_value = _make_yf_history([1.00, 1.01, 1.003])
            elif symbol == "GBPUSD=X":
                # GBP barely flat
                t.history.return_value = _make_yf_history([1.25, 1.25, 1.251])
            elif symbol == "USDJPY=X":
                # JPY barely flat
                t.history.return_value = _make_yf_history([150.0, 150.0, 149.9])
            elif symbol == "AUDUSD=X":
                # AUD barely flat
                t.history.return_value = _make_yf_history([0.65, 0.65, 0.651])
            elif symbol == "SPY":
                t.history.return_value = _make_yf_history([500.0, 500.0, 500.0])
            elif symbol == "^TNX":
                t.history.return_value = _make_yf_history([4.5, 4.5, 4.5])
            elif symbol == "^FVX":
                t.history.return_value = _make_yf_history([4.2, 4.2, 4.2])
            elif symbol == "^VIX":
                t.history.return_value = _make_yf_history([18.0, 18.0, 18.0])
            elif symbol == "DX-Y.NYB":
                t.history.return_value = _make_yf_history([104.0, 104.0, 104.0])
            elif symbol == "CL=F":
                t.history.return_value = _make_yf_history([75.0, 75.0, 75.0])
            elif symbol == "^IRX":
                t.history.return_value = _make_yf_history([5.2, 5.2, 5.2])
            else:
                t.history.return_value = _make_yf_history([100.0, 100.0, 100.0])
            return t

        mock_yf.Ticker.side_effect = mock_ticker
        provider = MacroDataProvider({})
        context = provider.build_context()
        # Should have a directional DXY (not unknown)
        assert context["dxy_trend"] in {"rising", "falling", "flat"}

    @patch("services.macro_data_provider._yf")
    @patch("services.macro_data_provider._YF_AVAILABLE", True)
    def test_fed_tone_hawkish(self, mock_yf):
        """Rising 10Y yields → hawkish Fed tone."""
        def mock_ticker(symbol):
            t = MagicMock()
            if symbol == "^TNX":
                # 10Y yields rising
                t.history.return_value = _make_yf_history([4.3, 4.5, 4.7])
            elif symbol == "^IRX":
                t.history.return_value = _make_yf_history([5.2, 5.2, 5.2])
            else:
                t.history.return_value = _make_yf_history([100.0, 100.0, 100.0])
            return t

        mock_yf.Ticker.side_effect = mock_ticker
        provider = MacroDataProvider({})
        context = provider.build_context()
        assert context["fed_tone"] == "hawkish"

    @patch("services.macro_data_provider._yf")
    @patch("services.macro_data_provider._YF_AVAILABLE", True)
    def test_fed_tone_dovish(self, mock_yf):
        """Falling 10Y yields → dovish Fed tone."""
        def mock_ticker(symbol):
            t = MagicMock()
            if symbol == "^TNX":
                # 10Y yields falling
                t.history.return_value = _make_yf_history([4.7, 4.5, 4.3])
            elif symbol == "^IRX":
                t.history.return_value = _make_yf_history([5.2, 5.2, 5.2])
            else:
                t.history.return_value = _make_yf_history([100.0, 100.0, 100.0])
            return t

        mock_yf.Ticker.side_effect = mock_ticker
        provider = MacroDataProvider({})
        context = provider.build_context()
        assert context["fed_tone"] == "dovish"


def test_database_macro_context_falls_back_local(tmp_path):
    from services.database import DatabaseService

    db = DatabaseService({"database": {"local_fallback_file": str(tmp_path / "trades.json")}})
    assert db.get_macro_context() == {}
