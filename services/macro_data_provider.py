"""Hourly macro-context provider for gold quality/learning.

Uses TWO free data sources:
  1. Twelve Data (Basic Free) — FX pairs + SPY for USD strength & risk proxy
  2. Yahoo Finance (yfinance) — Treasury yields, VIX, DXY, Oil (completely free, no API key)

This fills all 7 macro inputs that were previously "unknown", giving the
MacroFundamentalAgent real data for yields, Fed tone, oil, and risk sentiment.

Cost: 5 Twelve Data credits/hour (120/day) + 0 yfinance credits = well under limits.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

from utils.helpers import load_config

logger = logging.getLogger(__name__)

# yfinance is optional — if not installed, macro still works with Twelve Data only
try:
    import yfinance as _yf
    _YF_AVAILABLE = True
except ImportError:
    _yf = None
    _YF_AVAILABLE = False


class MacroDataProvider:
    """Fetch compact macro proxies and convert them to MacroFundamentalAgent input."""

    TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

    DEFAULT_SYMBOLS = {
        # USD strength basket — 4 majors + 1 risk proxy
        # Total: 5 credits/hour = 120/day — well under 800/day free limit
        # DXY weight coverage: EUR ~57.6%, JPY ~13.6%, GBP ~11.9%, AUD ~proxy
        # SPY: risk-on/risk-off sentiment proxy (SPY up = risk-on → bearish gold)
        "EUR/USD": {"component": "usd", "inverse_usd": True},
        "GBP/USD": {"component": "usd", "inverse_usd": True},
        "USD/JPY": {"component": "usd", "inverse_usd": False},
        "AUD/USD": {"component": "usd", "inverse_usd": True},
        "SPY":     {"component": "risk", "inverse_usd": False},
    }

    # yfinance symbols — completely free, no API key, no quota limits
    YFINANCE_SYMBOLS = {
        "^TNX":  {"field": "us10y_trend",          "description": "US 10-Year Treasury Yield"},
        "^FVX":  {"field": "real_yields_trend",     "description": "US 5-Year Treasury (real yields proxy)"},
        "^VIX":  {"field": "volatility_index",       "description": "CBOE Volatility Index"},
        "DX-Y.NYB": {"field": "dxy_direct",         "description": "US Dollar Index (direct)"},
        "CL=F":  {"field": "oil_trend",              "description": "WTI Crude Oil Futures"},
    }

    def __init__(self, config: Dict[str, Any] | None = None, session: requests.Session | None = None) -> None:
        self.config = config or load_config()
        self.settings = self.config.get("macro_data_provider", {}) or {}
        self.api_key = self._resolve_api_key()
        self.session = session or requests.Session()
        self.interval = str(self.settings.get("interval", "1h") or "1h")
        self.outputsize = int(self.settings.get("outputsize", 30) or 30)
        self.symbols = self._symbols()
        self.request_pause_seconds = float(self.settings.get("request_pause_seconds", 0.2) or 0.2)

    def build_context(self) -> Dict[str, Any]:
        """Fetch symbols and return the compact macro_context payload."""
        if not self.api_key:
            # Still try yfinance even without Twelve Data key
            return self._build_yfinance_only_context("TWELVEDATA_API_KEY missing")

        observations: Dict[str, Dict[str, Any]] = {}
        credits_used = 0
        errors: List[str] = []
        for symbol, meta in self.symbols.items():
            try:
                series = self._fetch_series(symbol)
                credits_used += 1
                observations[symbol] = self._analyze_symbol(symbol, series, meta)
                if self.request_pause_seconds:
                    time.sleep(self.request_pause_seconds)
            except Exception as exc:  # noqa: BLE001
                credits_used += 1
                msg = f"{symbol}: {exc}"
                logger.warning("Macro provider fetch failed: %s", msg)
                errors.append(msg[:160])

        usd_items = [v for v in observations.values() if v.get("component") == "usd" and v.get("usable")]
        risk_items = [v for v in observations.values() if v.get("component") == "risk" and v.get("usable")]
        usd_score = sum(float(x.get("usd_score", 0) or 0) for x in usd_items) / len(usd_items) if usd_items else 0.0
        # Use the strongest pair's absolute score for a more responsive DXY read,
        # since averaging 4 pairs often cancels out individual moves.
        max_usd_abs = max((abs(float(x.get("usd_score", 0) or 0)) for x in usd_items), default=0.0)
        # DXY trend uses strongest pair direction when avg is flat but a pair moved
        if abs(usd_score) < 0.15 and max_usd_abs >= 0.15:
            # Average cancelled out — use sign of the strongest pair
            for x in usd_items:
                if abs(float(x.get("usd_score", 0) or 0)) == max_usd_abs:
                    usd_score = float(x.get("usd_score", 0) or 0)
                    break
        risk_score = sum(float(x.get("trend_pct", 0) or 0) for x in risk_items) / len(risk_items) if risk_items else 0.0

        dxy_trend = self._trend_label(usd_score, up="rising", down="falling") if usd_items else "unknown"
        risk_sentiment = "risk_on" if risk_score >= 0.15 else "risk_off" if risk_score <= -0.15 else "neutral"

        # ── Fetch yfinance data (free, no quota) ──
        yf_data = self._fetch_yfinance_data()
        yf_errors = yf_data.pop("_errors", [])

        # Derive macro fields from yfinance
        us10y_trend = yf_data.get("us10y_trend", "unknown")
        real_yields_trend = yf_data.get("real_yields_trend", "unknown")
        oil_trend = yf_data.get("oil_trend", "unknown")
        vix_level = yf_data.get("vix_level")

        # Override DXY with direct index if available and FX-derived is flat
        dxy_direct_trend = yf_data.get("dxy_direct_trend")
        if dxy_direct_trend and dxy_direct_trend != "flat" and dxy_trend == "flat":
            dxy_trend = dxy_direct_trend
            logger.info("DXY overridden from yfinance direct: %s (FX was flat)", dxy_direct_trend)

        # Derive fed_tone from yield curve shape and 10Y direction
        fed_tone = self._derive_fed_tone(yf_data)

        # Enhance risk_sentiment with VIX if available
        if vix_level is not None and risk_sentiment == "neutral":
            if vix_level >= 25:
                risk_sentiment = "risk_off"
                logger.info("Risk overridden from VIX: risk_off (VIX=%.1f)", vix_level)
            elif vix_level <= 14:
                risk_sentiment = "risk_on"
                logger.info("Risk overridden from VIX: risk_on (VIX=%.1f)", vix_level)

        # Build missing fields list
        missing = []
        if us10y_trend == "unknown":
            missing.append("us10y_trend")
        if real_yields_trend == "unknown":
            missing.append("real_yields_trend")
        if fed_tone == "unknown":
            missing.append("fed_tone")
        if oil_trend == "unknown":
            missing.append("oil_trend")

        context = {
            "source": "twelvedata_yfinance_macro_proxy",
            "provider": "twelvedata+yfinance",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "freshness": "OK" if observations or yf_data else "UNKNOWN",
            "update_frequency": "hourly",
            "quota_policy": {
                "credits_used_estimate": credits_used,
                "daily_estimate_at_hourly": credits_used * 24,
                "free_daily_limit": 800,
                "yfinance_credits": 0,
                "yfinance_note": "completely free, no API key, no quota",
            },
            # MacroFundamentalAgent fields — now populated from real data
            "dxy_trend": dxy_trend,
            "usd_trend": dxy_trend,
            "risk_sentiment": risk_sentiment,
            "us10y_trend": us10y_trend,
            "real_yields_trend": real_yields_trend,
            "fed_tone": fed_tone,
            "inflation_surprise": "unknown",  # No free real-time source
            "oil_trend": oil_trend,
            # Extra context for smarter analysis
            "vix_level": vix_level,
            "yield_curve_spread": yf_data.get("yield_curve_spread"),
            "dxy_direct_trend": dxy_direct_trend,
            "observations": observations,
            "yfinance_observations": yf_data,
            "errors": (errors + yf_errors)[:8],
            "data_quality": {
                "source": "twelvedata+yfinance",
                "freshness": "OK" if observations or yf_data else "UNKNOWN",
                "usable_symbols": len([v for v in observations.values() if v.get("usable")]),
                "yfinance_symbols": len(yf_data),
                "missing_fields": missing,
            },
        }
        return context

    # ── yfinance integration (free data, no API key) ──

    def _fetch_yfinance_data(self) -> Dict[str, Any]:
        """Fetch macro data from Yahoo Finance (completely free, no API key).

        Returns dict with trend labels for yields, oil, VIX, DXY.
        """
        if not _YF_AVAILABLE:
            logger.info("yfinance not installed — skipping free macro data")
            return {"_errors": ["yfinance not installed"]}

        result: Dict[str, Any] = {}
        errors: List[str] = []

        for symbol, meta in self.YFINANCE_SYMBOLS.items():
            try:
                ticker = _yf.Ticker(symbol)
                hist = ticker.history(period="5d", interval="1h")
                if hist.empty:
                    continue
                closes = hist["Close"].dropna()
                if len(closes) < 2:
                    continue
                first = float(closes.iloc[0])
                last = float(closes.iloc[-1])
                trend_pct = ((last - first) / first * 100.0) if first else 0.0

                field = meta["field"]
                result[field] = round(last, 4)
                result[f"{field}_trend_pct"] = round(trend_pct, 4)

                # Map to trend label
                if field == "oil_trend":
                    result["oil_trend"] = self._trend_label(trend_pct, up="rising", down="falling")
                elif field == "us10y_trend":
                    result["us10y_trend"] = self._trend_label(trend_pct, up="rising", down="falling")
                elif field == "real_yields_trend":
                    result["real_yields_trend"] = self._trend_label(trend_pct, up="rising", down="falling")
                elif field == "dxy_direct":
                    result["dxy_direct_trend"] = self._trend_label(trend_pct, up="rising", down="falling")
                    result["dxy_direct_value"] = round(last, 2)
                elif field == "volatility_index":
                    result["vix_level"] = round(last, 2)
                    result["vix_trend_pct"] = round(trend_pct, 4)

            except Exception as exc:  # noqa: BLE001
                errors.append(f"yfinance {symbol}: {exc}")
                logger.warning("yfinance fetch failed for %s: %s", symbol, exc)

        # Derive yield curve spread (10Y - 13-week) for fed_tone
        try:
            irx = _yf.Ticker("^IRX")
            irx_hist = irx.history(period="5d")
            if not irx_hist.empty and "us10y_trend" in result:
                irx_last = float(irx_hist["Close"].iloc[-1])
                tnx_last = result.get("us10y_trend")
                # If us10y_trend is a string ("rising"/"falling"), get numeric value
                tnx_value = result.get("us10y_trend", 0)
                if isinstance(tnx_value, (int, float)):
                    result["yield_curve_spread"] = round(tnx_value - irx_last, 3)
        except Exception:  # noqa: BLE001
            pass

        result["_errors"] = errors
        return result

    def _derive_fed_tone(self, yf_data: Dict[str, Any]) -> str:
        """Derive Fed tone from yield curve shape and 10Y yield direction.

        Logic:
        - 10Y rising + curve steepening → HAWKISH (Fed likely to hold/hike)
        - 10Y falling + curve flattening/inverting → DOVISH (cuts expected)
        - Mixed or flat → NEUTRAL
        """
        us10y = yf_data.get("us10y_trend", "unknown")
        spread = yf_data.get("yield_curve_spread")

        if us10y == "rising":
            if spread is not None and spread < 0:
                # Yields rising but curve inverted → conflicting signals
                return "neutral"
            return "hawkish"
        elif us10y == "falling":
            if spread is not None and spread > 1.0:
                # Yields falling but steep curve → may be temporary
                return "neutral"
            return "dovish"
        elif us10y == "flat":
            return "neutral"

        # Fallback: use real_yields_trend if 10Y unavailable
        real_yields = yf_data.get("real_yields_trend", "unknown")
        if real_yields == "rising":
            return "hawkish"
        elif real_yields == "falling":
            return "dovish"

        return "unknown"

    def _build_yfinance_only_context(self, reason: str) -> Dict[str, Any]:
        """Build context using only yfinance when Twelve Data key is missing."""
        yf_data = self._fetch_yfinance_data()
        yf_errors = yf_data.pop("_errors", [])

        dxy_trend = yf_data.get("dxy_direct_trend", "unknown")
        us10y_trend = yf_data.get("us10y_trend", "unknown")
        real_yields_trend = yf_data.get("real_yields_trend", "unknown")
        oil_trend = yf_data.get("oil_trend", "unknown")
        fed_tone = self._derive_fed_tone(yf_data)
        vix_level = yf_data.get("vix_level")

        risk_sentiment = "neutral"
        if vix_level is not None:
            if vix_level >= 25:
                risk_sentiment = "risk_off"
            elif vix_level <= 14:
                risk_sentiment = "risk_on"

        return {
            "source": "yfinance_macro_proxy",
            "provider": "yfinance",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "freshness": "OK" if yf_data else "UNKNOWN",
            "update_frequency": "hourly",
            "quota_policy": {"credits_used_estimate": 0, "daily_estimate_at_hourly": 0, "free_daily_limit": "unlimited", "yfinance_credits": 0},
            "dxy_trend": dxy_trend,
            "usd_trend": dxy_trend,
            "risk_sentiment": risk_sentiment,
            "us10y_trend": us10y_trend,
            "real_yields_trend": real_yields_trend,
            "fed_tone": fed_tone,
            "inflation_surprise": "unknown",
            "oil_trend": oil_trend,
            "vix_level": vix_level,
            "yield_curve_spread": yf_data.get("yield_curve_spread"),
            "observations": {},
            "yfinance_observations": yf_data,
            "errors": [reason] + yf_errors[:4],
            "data_quality": {
                "source": "yfinance",
                "freshness": "OK" if yf_data else "UNKNOWN",
                "usable_symbols": 0,
                "yfinance_symbols": len(yf_data),
                "missing_fields": ["inflation_surprise"],
            },
        }

    def _resolve_api_key(self) -> str:
        key = os.environ.get("TWELVEDATA_API_KEY") or self.settings.get("api_key") or self.config.get("api_key")
        if isinstance(key, str) and key.startswith("ENV:"):
            key = os.environ.get(key.replace("ENV:", "", 1))
        return str(key or "").strip()

    def _symbols(self) -> Dict[str, Dict[str, Any]]:
        configured = self.settings.get("symbols")
        if isinstance(configured, dict) and configured:
            result: Dict[str, Dict[str, Any]] = {}
            for symbol, meta in configured.items():
                if isinstance(meta, dict):
                    result[str(symbol)] = dict(meta)
                else:
                    result[str(symbol)] = {"component": "usd", "inverse_usd": False}
            return result
        return dict(self.DEFAULT_SYMBOLS)

    def _fetch_series(self, symbol: str) -> List[Dict[str, Any]]:
        params = {
            "symbol": symbol,
            "interval": self.interval,
            "outputsize": self.outputsize,
            "apikey": self.api_key,
        }
        response = self.session.get(self.TWELVEDATA_URL, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "error":
            raise RuntimeError(str(payload.get("message") or "Twelve Data error"))
        values = payload.get("values") or []
        if not values:
            raise RuntimeError("no values returned")
        return list(reversed(values))  # old -> new

    def _analyze_symbol(self, symbol: str, series: List[Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
        closes: List[float] = []
        for item in series:
            try:
                closes.append(float(item.get("close")))
            except (TypeError, ValueError):
                continue
        if len(closes) < 2:
            return {"symbol": symbol, "component": meta.get("component"), "usable": False, "reason": "not_enough_data"}
        first = closes[0]
        last = closes[-1]
        trend_pct = ((last - first) / first * 100.0) if first else 0.0
        inverse_usd = bool(meta.get("inverse_usd"))
        usd_score = -trend_pct if inverse_usd else trend_pct
        return {
            "symbol": symbol,
            "component": meta.get("component", "usd"),
            "usable": True,
            "first_close": round(first, 6),
            "last_close": round(last, 6),
            "trend_pct": round(trend_pct, 4),
            "usd_score": round(usd_score, 4),
            "usd_read": self._trend_label(usd_score, up="stronger", down="weaker"),
        }

    @staticmethod
    def _trend_label(value: float, up: str, down: str) -> str:
        # Lowered from 0.25 to 0.15: averaged FX pairs often cancel out individual
        # moves, so 0.25 was too high and produced "flat" most of the time.
        if value >= 0.15:
            return up
        if value <= -0.15:
            return down
        return "flat"

    @staticmethod
    def _empty_context(reason: str) -> Dict[str, Any]:
        return {
            "source": "twelvedata_yfinance_macro_proxy",
            "provider": "none",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "freshness": "UNKNOWN",
            "dxy_trend": "unknown",
            "usd_trend": "unknown",
            "risk_sentiment": "neutral",
            "us10y_trend": "unknown",
            "real_yields_trend": "unknown",
            "fed_tone": "unknown",
            "oil_trend": "unknown",
            "data_quality": {"source": "none", "freshness": "UNKNOWN", "missing_fields": ["api_key"], "usable_symbols": 0},
            "errors": [reason],
            "quota_policy": {"credits_used_estimate": 0, "daily_estimate_at_hourly": 0, "free_daily_limit": 800},
        }
