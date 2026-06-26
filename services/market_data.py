"""Market data service for all configured instruments.

Fetches OHLCV data from Twelve Data. A synthetic fallback exists for local tests;
production workflows block synthetic prices unless explicitly allowed.
"""

from __future__ import annotations

import logging
import math
import os
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import requests

from utils.helpers import load_config


class MarketDataService:
    """Fetch and normalize XAU/USD OHLCV data."""

    TWELVE_URL = "https://api.twelvedata.com/time_series"
    TWELVE_QUOTE_URL = "https://api.twelvedata.com/quote"

    INTERVAL_MAP = {
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1H": "1h",
        "4H": "4h",
        "1D": "1day",
    }

    TF_MINUTES = {
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1H": 60,
        "4H": 240,
        "1D": 1440,
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.api_key = os.environ.get("TWELVE_DATA_API_KEY") or self.config.get("data_source", {}).get("api_keys", {}).get("twelve_data")
        if isinstance(self.api_key, str) and self.api_key.startswith("ENV:"):
            self.api_key = os.environ.get(self.api_key.replace("ENV:", "", 1))
        self.symbol = self.config.get("symbol", "XAU/USD")
        self._last_request_at = 0.0
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.session = requests.Session()

    def get_gold_data(self, outputsize: int = 220) -> Dict[str, Any] | None:
        """Return normalized data for all configured timeframes."""
        timeframes = self.config.get("timeframes", ["5m", "15m", "1H", "4H"])
        primary_tf = self.config.get("primary_timeframe", "15m")
        tf_payloads: Dict[str, Dict[str, Any]] = {}

        for timeframe in timeframes:
            tf_payloads[timeframe] = self.get_ohlcv(timeframe=timeframe, outputsize=outputsize)

        primary_payload = tf_payloads.get(primary_tf) or next(iter(tf_payloads.values()), None)
        if not primary_payload:
            return None

        return {
            "symbol": self.symbol,
            "timeframe": primary_tf,
            "data": primary_payload["data"],
            "timeframes": tf_payloads,
            "current_price": primary_payload["current_price"],
            "spread_points": primary_payload.get("spread_points"),
            "last_updated": primary_payload["last_updated"],
            "source": primary_payload.get("source", "unknown"),
        }

    def get_ohlcv(self, timeframe: str = "15m", outputsize: int = 220) -> Dict[str, Any]:
        """Fetch OHLCV for a timeframe with retry, cache and synthetic fallback."""
        cache_key = f"{self.symbol}:{timeframe}:{outputsize}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - float(cached.get("cached_at", 0)) < 60:
            return cached["payload"]

        payload: Dict[str, Any] | None = None
        if self.api_key and self.api_key != "YOUR_API_KEY":
            payload = self._fetch_twelve_data(timeframe, outputsize)

        if payload is None:
            self.logger.warning("Using synthetic demo data for %s. Configure TWELVE_DATA_API_KEY for live prices.", timeframe)
            payload = self._generate_synthetic_data(timeframe, outputsize)

        self._cache[cache_key] = {"cached_at": time.time(), "payload": payload}
        return payload

    def get_current_price(self) -> float | None:
        """Return current gold price, using Twelve quote first then OHLC fallback."""
        if self.api_key and self.api_key != "YOUR_API_KEY":
            try:
                self._rate_limit()
                response = self.session.get(
                    self.TWELVE_QUOTE_URL,
                    params={"symbol": self.symbol, "apikey": self.api_key},
                    timeout=20,
                )
                response.raise_for_status()
                data = response.json()
                if "close" in data:
                    return float(data["close"])
                if "price" in data:
                    return float(data["price"])
            except Exception as exc:  # noqa: BLE001 - external API must not crash workflow
                self.logger.warning("Quote fetch failed, falling back to OHLC: %s", exc)
        payload = self.get_ohlcv(self.config.get("primary_timeframe", "15m"), outputsize=60)
        return float(payload["current_price"]) if payload else None

    def _fetch_twelve_data(self, timeframe: str, outputsize: int) -> Dict[str, Any] | None:
        interval = self.INTERVAL_MAP.get(timeframe, timeframe)
        params = {
            "symbol": self.symbol,
            "interval": interval,
            "apikey": self.api_key,
            "outputsize": outputsize,
            "timezone": "UTC",
        }
        for attempt in range(3):
            try:
                self._rate_limit()
                response = self.session.get(self.TWELVE_URL, params=params, timeout=25)
                response.raise_for_status()
                raw = response.json()
                if raw.get("status") == "error" or "values" not in raw:
                    self.logger.warning("Twelve Data returned error for %s: %s", timeframe, raw.get("message", raw))
                    return None
                candles = self._normalize_twelve_values(raw.get("values", []))
                if not candles:
                    return None
                current_price = float(candles[-1]["close"])
                return {
                    "symbol": self.symbol,
                    "timeframe": timeframe,
                    "data": candles,
                    "current_price": current_price,
                    # Twelve time_series does not reliably provide bid/ask; unknown spread will not block risk filter.
                    "spread_points": None,
                    "last_updated": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "source": "twelve_data",
                }
            except Exception as exc:  # noqa: BLE001
                wait = 2**attempt
                self.logger.warning("Twelve Data attempt %s failed for %s: %s", attempt + 1, timeframe, exc)
                time.sleep(wait)
        return None

    def _normalize_twelve_values(self, values: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        candles: List[Dict[str, Any]] = []
        for row in values:
            try:
                dt_text = row.get("datetime") or row.get("time")
                dt = self._parse_dt(str(dt_text))
                candles.append(
                    {
                        "time": dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row.get("volume") or 0),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("Skipping invalid candle %s: %s", row, exc)
        candles.sort(key=lambda item: item["time"])
        return candles

    def _parse_dt(self, value: str) -> datetime:
        value = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _generate_synthetic_data(self, timeframe: str, outputsize: int) -> Dict[str, Any]:
        """Generate deterministic-ish demo data for local tests, not trading."""
        minutes = self.TF_MINUTES.get(timeframe, 15)
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        start = now - timedelta(minutes=minutes * outputsize)
        seed = int(now.strftime("%Y%m%d%H")) + minutes
        rng = random.Random(seed)
        base_prices = {
            "XAU/USD": 2350.0,
            "EUR/USD": 1.0850,
            "GBP/USD": 1.2700,
            "USD/JPY": 155.00,
            "USD/CHF": 0.9000,
            "USD/CAD": 1.3600,
            "AUD/USD": 0.6650,
            "WTI/USD": 75.00,
        }
        base = base_prices.get(str(self.symbol).upper(), 1.0000) * (1 + math.sin(seed / 1000) * 0.002)
        from utils.instruments import price_decimals
        decimals = price_decimals(self.symbol)
        candles: List[Dict[str, Any]] = []
        close = base
        for i in range(outputsize):
            dt = start + timedelta(minutes=minutes * i)
            scale = max(base * 0.00035, 0.00005)
            if str(self.symbol).upper() in {"XAU/USD", "WTI/USD"}:
                scale = max(base * 0.0008, 0.03)
            drift = (math.sin(i / 12) * 0.45 + math.sin(i / 37) * 0.25) * scale
            noise = rng.uniform(-1.2, 1.2) * scale
            open_price = close
            close = max(0.0001, open_price + drift + noise)
            high = max(open_price, close) + rng.uniform(0.2, 2.2) * scale
            low = min(open_price, close) - rng.uniform(0.2, 2.2) * scale
            candles.append(
                {
                    "time": dt.isoformat().replace("+00:00", "Z"),
                    "open": round(open_price, decimals),
                    "high": round(high, decimals),
                    "low": round(low, decimals),
                    "close": round(close, decimals),
                    "volume": int(1000 + rng.random() * 1200),
                }
            )
        return {
            "symbol": self.symbol,
            "timeframe": timeframe,
            "data": candles,
            "current_price": float(candles[-1]["close"]),
            # Demo spread estimate: 2 points = 0.20 USD in our helper convention.
            "spread_points": 2.0,
            "last_updated": now.isoformat().replace("+00:00", "Z"),
            "source": "synthetic_demo",
        }

    def _rate_limit(self) -> None:
        """Basic in-run rate limit to avoid API bursts."""
        elapsed = time.time() - self._last_request_at
        if elapsed < 0.8:
            time.sleep(0.8 - elapsed)
        self._last_request_at = time.time()
