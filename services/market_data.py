"""Market data service for all configured instruments.

Fetches OHLCV data from Finnhub (primary). Twelve Data kept as optional fallback.
A synthetic fallback exists for local tests;
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
    """Fetch and normalize OHLCV data — Finnhub primary."""

    # Finnhub
    FINNHUB_URL = "https://finnhub.io/api/v1/forex/candle"
    FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"

    # Twelve Data (optional fallback)
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

    # Finnhub resolution map
    FINNHUB_RESOLUTION = {
        "1m": "1",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1H": "60",
        "4H": "240",
        "1D": "D",
    }

    # Twelve → Finnhub OANDA symbol map
    SYMBOL_MAP_FINNHUB = {
        "XAU/USD": "OANDA:XAU_USD",
        "EUR/USD": "OANDA:EUR_USD",
        "GBP/USD": "OANDA:GBP_USD",
        "USD/JPY": "OANDA:USD_JPY",
        "USD/CHF": "OANDA:USD_CHF",
        "USD/CAD": "OANDA:USD_CAD",
        "AUD/USD": "OANDA:AUD_USD",
        "WTI/USD": "OANDA:WTICO_USD",
        "USOIL": "OANDA:WTICO_USD",
        "WTICO_USD": "OANDA:WTICO_USD",
    }

    WTI_FALLBACKS = [
        "OANDA:WTICO_USD",
        "OANDA:WTI_USD",
        "OANDA:BCO_USD",
        "OANDA:XBR_USD",
        "OANDA:XTI_USD",
    ]

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.finnhub_key = (
            os.environ.get("FINNHUB_API_KEY")
            or self._get_cfg_key("finnhub")
            or self._get_cfg_key("FINNHUB_API_KEY")
        )
        self.api_key = (
            os.environ.get("TWELVE_DATA_API_KEY")
            or self._get_cfg_key("twelve_data")
        )
        if isinstance(self.api_key, str) and self.api_key.startswith("ENV:"):
            self.api_key = os.environ.get(self.api_key.replace("ENV:", "", 1))
        self.symbol = self.config.get("symbol", "XAU/USD")
        self.finnhub_symbol = self._map_symbol(self.symbol)
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

    def _map_symbol(self, symbol: str) -> str:
        s = symbol.upper().strip()
        if s in self.SYMBOL_MAP_FINNHUB:
            return self.SYMBOL_MAP_FINNHUB[s]
        if "/" in s:
            base, quote = s.split("/", 1)
            return f"OANDA:{base}_{quote}"
        if s.startswith("OANDA:"):
            return s
        return f"OANDA:{s.replace('/', '_')}"

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
        if self.finnhub_key and self.finnhub_key != "YOUR_API_KEY":
            payload = self._fetch_finnhub_data(timeframe, outputsize)
        if payload is None and self.api_key and self.api_key != "YOUR_API_KEY":
            payload = self._fetch_twelve_data(timeframe, outputsize)
        if payload is None:
            self.logger.warning("Using synthetic demo data for %s %s. Configure FINNHUB_API_KEY for live prices.", self.symbol, timeframe)
            payload = self._generate_synthetic_data(timeframe, outputsize)
        self._cache[cache_key] = {"cached_at": time.time(), "payload": payload}
        return payload

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

    def get_current_price(self) -> float | None:
        if self.finnhub_key and self.finnhub_key != "YOUR_API_KEY":
            try:
                self._rate_limit()
                q = self._fetch_finnhub_data("1m" if "1m" in self.FINNHUB_RESOLUTION else "5m", outputsize=2)
                if q and q.get("current_price"):
                    return float(q["current_price"])
            except Exception as exc:
                self.logger.debug("Finnhub quote fallback failed: %s", exc)
        if self.api_key and self.api_key != "YOUR_API_KEY":
            try:
                self._rate_limit()
                response = self.session.get(self.TWELVE_QUOTE_URL, params={"symbol": self.symbol, "apikey": self.api_key}, timeout=20)
                response.raise_for_status()
                data = response.json()
                if "close" in data:
                    return float(data["close"])
                if "price" in data:
                    return float(data["price"])
            except Exception as exc:
                self.logger.warning("Quote fetch failed, falling back to OHLC: %s", exc)
        payload = self.get_ohlcv(self.config.get("primary_timeframe", "15m"), outputsize=60)
        return float(payload["current_price"]) if payload else None

    def _fetch_finnhub_data(self, timeframe: str, outputsize: int) -> Dict[str, Any] | None:
        resolution = self.FINNHUB_RESOLUTION.get(timeframe)
        if not resolution:
            self.logger.warning("Finnhub: unsupported timeframe %s", timeframe)
            return None
        tf_minutes = self.TF_MINUTES.get(timeframe, 15)
        if resolution == "D":
            tf_minutes = 1440
        end_ts = int(time.time())
        start_ts = end_ts - int(outputsize * tf_minutes * 60 * 1.8)
        symbols_to_try = [self.finnhub_symbol]
        if self.symbol.upper().startswith("WTI"):
            symbols_to_try = self.WTI_FALLBACKS
        for sym in symbols_to_try:
            for attempt in range(2):
                try:
                    self._rate_limit()
                    params = {"symbol": sym, "resolution": resolution, "from": start_ts, "to": end_ts, "token": self.finnhub_key}
                    resp = self.session.get(self.FINNHUB_URL, params=params, timeout=25)
                    resp.raise_for_status()
                    raw = resp.json()
                    if raw.get("s") != "ok" or not raw.get("c"):
                        if attempt == 0:
                            params["from"] = start_ts - outputsize * tf_minutes * 60 * 3
                            continue
                        self.logger.debug("Finnhub no_data %s %s: %s", sym, timeframe, raw.get("s"))
                        break
                    candles = self._normalize_finnhub_values(raw)
                    if not candles:
                        break
                    if len(candles) > outputsize:
                        candles = candles[-outputsize:]
                    current_price = float(candles[-1]["close"])
                    return {"symbol": self.symbol, "timeframe": timeframe, "data": candles, "current_price": current_price, "spread_points": None, "last_updated": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "source": "finnhub", "finnhub_symbol": sym}
                except Exception as exc:
                    self.logger.warning("Finnhub attempt %s failed %s %s: %s", attempt + 1, sym, timeframe, exc)
                    time.sleep(0.5 * (attempt + 1))
        return None

    def _normalize_finnhub_values(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            closes = raw.get("c", [])
            opens = raw.get("o", [])
            highs = raw.get("h", [])
            lows = raw.get("l", [])
            volumes = raw.get("v", [])
            times = raw.get("t", [])
            n = min(len(closes), len(opens), len(highs), len(lows), len(times))
            candles: List[Dict[str, Any]] = []
            from utils.instruments import price_decimals
            decimals = price_decimals(self.symbol)
            for i in range(n):
                try:
                    ts = int(times[i])
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    candles.append({"time": dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "open": round(float(opens[i]), decimals), "high": round(float(highs[i]), decimals), "low": round(float(lows[i]), decimals), "close": round(float(closes[i]), decimals), "volume": float(volumes[i]) if i < len(volumes) else 0.0})
                except Exception:
                    continue
            candles.sort(key=lambda x: x["time"])
            return candles
        except Exception as exc:
            self.logger.debug("Finnhub normalize failed: %s", exc)
            return []

    def _fetch_twelve_data(self, timeframe: str, outputsize: int) -> Dict[str, Any] | None:
        interval = self.INTERVAL_MAP.get(timeframe, timeframe)
        params = {"symbol": self.symbol, "interval": interval, "apikey": self.api_key, "outputsize": outputsize, "timezone": "UTC"}
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
                return {"symbol": self.symbol, "timeframe": timeframe, "data": candles, "current_price": current_price, "spread_points": None, "last_updated": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "source": "twelve_data"}
            except Exception as exc:
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
                candles.append({"time": dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "open": float(row["open"]), "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"]), "volume": float(row.get("volume") or 0)})
            except Exception as exc:
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
        minutes = self.TF_MINUTES.get(timeframe, 15)
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        start = now - timedelta(minutes=minutes * outputsize)
        seed = int(now.strftime("%Y%m%d%H")) + minutes
        rng = random.Random(seed)
        base_prices = {"XAU/USD": 2350.0, "EUR/USD": 1.0850, "GBP/USD": 1.2700, "USD/JPY": 155.00, "USD/CHF": 0.9000, "USD/CAD": 1.3600, "AUD/USD": 0.6650, "WTI/USD": 75.00}
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
        if elapsed < 1.05:
            time.sleep(1.05 - elapsed)
        self._last_request_at = time.time()
