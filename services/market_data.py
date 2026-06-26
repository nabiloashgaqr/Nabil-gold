"""Market data service for all configured instruments.

Fetches OHLCV data from **Twelve Data** (primary and exclusive).
Free tier: 800 calls/day — enough for 8 symbols every 5 minutes.

A synthetic fallback exists **only** for local tests / development;
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
    """Fetch and normalize OHLCV data — Twelve Data only."""

    TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

    TF_MINUTES = {
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1H": 60,
        "4H": 240,
        "1D": 1440,
    }

    TWELVEDATA_INTERVAL = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1H": "1h",
        "4H": "4h",
        "1D": "1day",
    }

    SYMBOL_MAP = {
        "XAU/USD": "XAU/USD",
        "WTI/USD": "WTI/USD",
        "USOIL": "WTI/USD",
        "WTICO_USD": "WTI/USD",
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.api_key = (
            os.environ.get("TWELVEDATA_API_KEY")
            or self._get_cfg_key("twelvedata")
            or self._get_cfg_key("TWELVEDATA_API_KEY")
        )
        self.symbol = self.config.get("symbol", "XAU/USD")
        self.td_symbol = self.SYMBOL_MAP.get(self.symbol.upper(), self.symbol)
        self._last_request_at = 0.0
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.session = requests.Session()

    def _get_cfg_key(self, name: str) -> str | None:
        ds = self.config.get("data_source", {}) or {}
        api_keys = ds.get("api_keys", {}) or {}
        val = api_keys.get(name)
        if isinstance(val, str) and val.startswith("ENV:"):
            return os.environ.get(val.replace("ENV:", "", 1))
        return val if isinstance(val, str) else None

    def get_gold_data(self, outputsize: int = 220) -> Dict[str, Any] | None:
        timeframes = self.config.get("timeframes", ["5m", "15m", "1H", "4H"])
        primary_tf = self.config.get("primary_timeframe", "15m")
        data_cfg = self.config.get("data_source", {}) or {}
        if data_cfg.get("resample_timeframes_from_base", False):
            base_tf = str(data_cfg.get("base_timeframe", "5m"))
            base_outputsize = int(data_cfg.get("base_outputsize", max(outputsize, 2500)) or max(outputsize, 2500))
            base_payload = self.get_ohlcv(timeframe=base_tf, outputsize=base_outputsize)
            if not base_payload:
                return None
            tf_payloads = self._build_resampled_payloads(base_payload, timeframes)
        else:
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
        cache_key = f"{self.symbol}:{timeframe}:{outputsize}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - float(cached.get("cached_at", 0)) < 60:
            return cached["payload"]

        payload: Dict[str, Any] | None = None
        if self.api_key and self.api_key != "YOUR_API_KEY":
            payload = self._fetch_data(timeframe, outputsize)

        if payload is None:
            self.logger.warning("Using synthetic demo data for %s %s. Configure TWELVEDATA_API_KEY.", self.symbol, timeframe)
            payload = self._generate_synthetic_data(timeframe, outputsize)

        self._cache[cache_key] = {"cached_at": time.time(), "payload": payload}
        return payload

    # ── Twelve Data fetch ───────────────────────────────────────────
    def _fetch_data(self, timeframe: str, outputsize: int) -> Dict[str, Any] | None:
        interval = self.TWELVEDATA_INTERVAL.get(timeframe)
        if not interval:
            self.logger.warning("Twelve Data: unsupported timeframe %s", timeframe)
            return None

        params = {
            "symbol": self.td_symbol,
            "interval": interval,
            "outputsize": min(outputsize, 5000),
            "apikey": self.api_key,
        }

        for attempt in range(2):
            try:
                self._rate_limit()
                resp = self.session.get(self.TWELVEDATA_URL, params=params, timeout=25)
                resp.raise_for_status()
                raw = resp.json()

                if raw.get("status") == "error":
                    self.logger.warning("Twelve Data error: %s", raw.get("message", "unknown"))
                    return None

                values = raw.get("values", [])
                if not values:
                    self.logger.warning("Twelve Data: no data for %s %s", self.symbol, timeframe)
                    return None

                candles = self._normalize_values(values)
                if not candles:
                    return None

                if len(candles) > outputsize:
                    candles = candles[-outputsize:]

                current_price = float(candles[-1]["close"])
                return {
                    "symbol": self.symbol,
                    "timeframe": timeframe,
                    "data": candles,
                    "current_price": current_price,
                    "spread_points": None,
                    "last_updated": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "source": "twelvedata",
                }
            except Exception as exc:
                self.logger.warning("Twelve Data attempt %s failed %s %s: %s", attempt + 1, self.symbol, timeframe, exc)
                time.sleep(0.5 * (attempt + 1))

        return None

    def _normalize_values(self, values: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        from utils.instruments import price_decimals
        decimals = price_decimals(self.symbol)
        candles: List[Dict[str, Any]] = []
        for item in values:
            try:
                time_str = str(item.get("datetime", ""))
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                candles.append({
                    "time": dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "open": round(float(item["open"]), decimals),
                    "high": round(float(item["high"]), decimals),
                    "low": round(float(item["low"]), decimals),
                    "close": round(float(item["close"]), decimals),
                    "volume": float(item.get("volume", 0) or 0),
                })
            except (ValueError, KeyError, TypeError):
                continue
        candles.sort(key=lambda x: x["time"])
        return candles

    def get_current_price(self) -> float | None:
        payload = self.get_ohlcv(self.config.get("primary_timeframe", "15m"), outputsize=5)
        return float(payload["current_price"]) if payload else None

    # ── Resampling ──────────────────────────────────────────────────
    def _build_resampled_payloads(self, base_payload: Dict[str, Any], timeframes: List[str]) -> Dict[str, Dict[str, Any]]:
        base_tf = str(base_payload.get("timeframe", "5m"))
        base_minutes = int(self.TF_MINUTES.get(base_tf, 5) or 5)
        base_data = list(base_payload.get("data", []) or [])
        payloads: Dict[str, Dict[str, Any]] = {}
        for tf in timeframes:
            tf_minutes = int(self.TF_MINUTES.get(tf, base_minutes) or base_minutes)
            if tf == base_tf or tf_minutes <= base_minutes:
                candles = base_data
            else:
                candles = self._resample_candles(base_data, tf_minutes)
            if not candles:
                candles = base_data[-1:] if base_data else []
            payloads[tf] = {
                "symbol": self.symbol,
                "timeframe": tf,
                "data": candles,
                "current_price": float(candles[-1]["close"]) if candles else base_payload.get("current_price"),
                "spread_points": base_payload.get("spread_points"),
                "last_updated": base_payload.get("last_updated"),
                "source": base_payload.get("source", "unknown"),
                "resampled_from": base_tf if tf != base_tf else None,
            }
        return payloads

    def _resample_candles(self, candles: List[Dict[str, Any]], timeframe_minutes: int) -> List[Dict[str, Any]]:
        buckets: Dict[int, List[Dict[str, Any]]] = {}
        bucket_seconds = timeframe_minutes * 60
        for candle in candles:
            try:
                dt = self._parse_dt(str(candle.get("time")))
                bucket = int(dt.timestamp()) // bucket_seconds * bucket_seconds
                buckets.setdefault(bucket, []).append(candle)
            except Exception:
                continue
        out: List[Dict[str, Any]] = []
        for bucket in sorted(buckets):
            group = sorted(buckets[bucket], key=lambda c: str(c.get("time")))
            if not group:
                continue
            out.append({
                "time": datetime.fromtimestamp(bucket, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "open": float(group[0]["open"]),
                "high": max(float(c["high"]) for c in group),
                "low": min(float(c["low"]) for c in group),
                "close": float(group[-1]["close"]),
                "volume": sum(float(c.get("volume") or 0) for c in group),
            })
        return out

    # ── Helpers ─────────────────────────────────────────────────────
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
        minutes = self.TF_MINUTES.get(timeframe, 15)
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        start = now - timedelta(minutes=minutes * outputsize)
        seed = int(now.strftime("%Y%m%d%H")) + minutes
        rng = random.Random(seed)
        base_prices = {"XAU/USD": 3350.0, "WTI/USD": 75.00}
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
            candles.append({"time": dt.isoformat().replace("+00:00", "Z"), "open": round(open_price, decimals), "high": round(high, decimals), "low": round(low, decimals), "close": round(close, decimals), "volume": int(1000 + rng.random() * 1200)})
        return {"symbol": self.symbol, "timeframe": timeframe, "data": candles, "current_price": float(candles[-1]["close"]), "spread_points": 2.0, "last_updated": now.isoformat().replace("+00:00", "Z"), "source": "synthetic_demo"}

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_at
        # Twelve Data free tier: 8 calls/min. Wait at least 8 seconds between
        # calls to stay safely under the limit (60s / 8 = 7.5s per call).
        min_delay = 8.0
        if elapsed < min_delay:
            time.sleep(min_delay - elapsed)
        self._last_request_at = time.time()
