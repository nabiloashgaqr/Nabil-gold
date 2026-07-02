"""Hourly macro-context provider for gold quality/learning.

Uses Twelve Data Basic-friendly symbols to infer USD/risk context with a small,
predictable quota footprint.  It is intentionally advisory: downstream decision
logic may use it for quality/attribution/learning, not as a standalone entry gate.
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


class MacroDataProvider:
    """Fetch compact macro proxies and convert them to MacroFundamentalAgent input."""

    TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

    DEFAULT_SYMBOLS = {
        # USD proxy basket available on Basic via Forex.
        "EUR/USD": {"component": "usd", "inverse_usd": True},
        "GBP/USD": {"component": "usd", "inverse_usd": True},
        "USD/JPY": {"component": "usd", "inverse_usd": False},
        "USD/CNY": {"component": "usd", "inverse_usd": False},
        # Basic includes US market data; use SPY as a lightweight risk proxy.
        "SPY": {"component": "risk", "inverse_usd": False},
    }

    def __init__(self, config: Dict[str, Any] | None = None, session: requests.Session | None = None) -> None:
        self.config = config or load_config()
        self.settings = self.config.get("macro_data_provider", {}) or {}
        self.api_key = self._resolve_api_key()
        self.session = session or requests.Session()
        self.interval = str(self.settings.get("interval", "1h"))
        self.outputsize = int(self.settings.get("outputsize", 30) or 30)
        self.symbols = self._symbols()
        self.request_pause_seconds = float(self.settings.get("request_pause_seconds", 0.2) or 0.2)

    def build_context(self) -> Dict[str, Any]:
        """Fetch symbols and return the compact macro_context payload."""
        if not self.api_key:
            return self._empty_context("TWELVEDATA_API_KEY missing")

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
        risk_score = sum(float(x.get("trend_pct", 0) or 0) for x in risk_items) / len(risk_items) if risk_items else 0.0

        dxy_trend = self._trend_label(usd_score, up="rising", down="falling") if usd_items else "unknown"
        risk_sentiment = "risk_on" if risk_score >= 0.25 else "risk_off" if risk_score <= -0.25 else "neutral"
        context = {
            "source": "twelvedata_hourly_macro_proxy",
            "provider": "twelvedata",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "freshness": "OK" if observations else "UNKNOWN",
            "update_frequency": "hourly",
            "quota_policy": {
                "credits_used_estimate": credits_used,
                "daily_estimate_at_hourly": credits_used * 24,
                "free_daily_limit": 800,
            },
            # MacroFundamentalAgent fields.
            "dxy_trend": dxy_trend,
            "usd_trend": dxy_trend,
            "risk_sentiment": risk_sentiment,
            # Explicitly unknown on the free-safe plan; do not hallucinate yields/Fed.
            "us10y_trend": "unknown",
            "real_yields_trend": "unknown",
            "fed_tone": "unknown",
            "inflation_surprise": "unknown",
            "oil_trend": "unknown",
            "observations": observations,
            "errors": errors[:5],
            "data_quality": {
                "source": "twelvedata",
                "freshness": "OK" if observations else "UNKNOWN",
                "usable_symbols": len([v for v in observations.values() if v.get("usable")]),
                "missing_fields": ["us10y_trend", "real_yields_trend", "fed_tone", "inflation_surprise", "oil_trend"],
            },
        }
        return context

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
        if value >= 0.25:
            return up
        if value <= -0.25:
            return down
        return "flat"

    @staticmethod
    def _empty_context(reason: str) -> Dict[str, Any]:
        return {
            "source": "twelvedata_hourly_macro_proxy",
            "provider": "twelvedata",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "freshness": "UNKNOWN",
            "dxy_trend": "unknown",
            "usd_trend": "unknown",
            "risk_sentiment": "neutral",
            "data_quality": {"source": "twelvedata", "freshness": "UNKNOWN", "missing_fields": ["api_key"], "usable_symbols": 0},
            "errors": [reason],
            "quota_policy": {"credits_used_estimate": 0, "daily_estimate_at_hourly": 0, "free_daily_limit": 800},
        }
