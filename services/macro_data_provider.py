"""Hourly macro-context provider for gold quality/learning.

Uses Yahoo Finance (yfinance) as the SOLE data source — completely free,
no API key, no quota limits.

Provides 7/7 macro inputs:
  ✓ dxy_trend       — DXY direct + FX basket (EUR/USD, GBP/USD, USD/JPY, AUD/USD)
  ✓ risk_sentiment  — VIX + SPY trend
  ✓ us10y_trend     — 10-Year Treasury Yield (^TNX)
  ✓ real_yields_trend — 5-Year Treasury (^FVX)
  ✓ fed_tone        — yield curve shape + 10Y direction
  ✓ oil_trend       — WTI Crude Oil (CL=F)
  ✓ inflation_surprise — TIP/STIP ETF trend (inflation expectations proxy)

Data freshness:
  - Primary fetch: 5d × 1h interval (fine-grained trend)
  - Stale fallback: if last candle > 48h old, retry with 5d × 1d (daily close)
  - freshness_summary reports any stale symbols with age in hours
  - stale symbols still contribute to trend but with a warning

Cost: 0 API credits — yfinance is completely free, no API key, no quota.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from utils.helpers import load_config

logger = logging.getLogger(__name__)

# yfinance is required for macro data
try:
    import yfinance as _yf
    _YF_AVAILABLE = True
except ImportError:
    _yf = None
    _YF_AVAILABLE = False

# Age threshold: if the last candle is older than this, data is "stale"
_STALE_THRESHOLD_HOURS = 48.0


class MacroDataProvider:
    """Fetch compact macro proxies from Yahoo Finance and convert them to
    MacroFundamentalAgent input.
    """

    # ── Core macro symbols (all free via yfinance, no API key) ──
    YFINANCE_SYMBOLS: Dict[str, Dict[str, Any]] = {
        # FX basket for USD strength (DXY proxy)
        # DXY weight coverage: EUR ~57.6%, JPY ~13.6%, GBP ~11.9%, AUD ~proxy
        "EURUSD=X":   {"component": "usd", "inverse_usd": True,  "description": "EUR/USD"},
        "GBPUSD=X":   {"component": "usd", "inverse_usd": True,  "description": "GBP/USD"},
        "USDJPY=X":   {"component": "usd", "inverse_usd": False, "description": "USD/JPY"},
        "AUDUSD=X":   {"component": "usd", "inverse_usd": True,  "description": "AUD/USD"},
        # Risk proxy
        "SPY":        {"component": "risk", "inverse_usd": False, "description": "S&P 500 ETF (risk-on/off proxy)"},
        # Treasury yields
        "^TNX":       {"field": "us10y_trend",       "description": "US 10-Year Treasury Yield"},
        "^FVX":       {"field": "real_yields_trend",  "description": "US 5-Year Treasury (real yields proxy)"},
        # Volatility
        "^VIX":       {"field": "volatility_index",    "description": "CBOE Volatility Index"},
        # DXY direct
        "DX-Y.NYB":   {"field": "dxy_direct",          "description": "US Dollar Index (direct)"},
        # Oil
        "CL=F":       {"field": "oil_trend",            "description": "WTI Crude Oil Futures"},
        # Inflation expectations — TIPS bond ETFs
        # TIP: iShares TIPS Bond ETF (broad inflation protection, 10Y+ duration)
        # When TIP rises → investors buying inflation protection → expectations HOT
        # When TIP falls → inflation expectations cooling → COOL
        "TIP":        {"field": "inflation_tips",       "description": "iShares TIPS Bond ETF (inflation expectations proxy)"},
        # STIP: 0-5 Year TIPS ETF (short-term inflation expectations)
        "STIP":       {"field": "inflation_stip",       "description": "0-5 Year TIPS ETF (short-term inflation proxy)"},
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        self.settings = self.config.get("macro_data_provider", {}) or {}

    # ── Public API ────────────────────────────────────────────────────────

    def build_context(self) -> Dict[str, Any]:
        """Fetch all macro data from yfinance and return the compact context."""
        if not _YF_AVAILABLE:
            logger.error("yfinance not installed — macro data unavailable")
            return self._empty_context("yfinance not installed")

        yf_data = self._fetch_yfinance_data()
        yf_errors = yf_data.pop("_errors", [])
        staleness = yf_data.pop("_staleness", {})

        # ── DXY from FX basket ──
        dxy_trend = self._compute_dxy_from_fx(yf_data)

        # Override DXY with direct index if FX-derived is flat but direct is not
        dxy_direct_trend = yf_data.get("dxy_direct_trend")
        if dxy_direct_trend and dxy_direct_trend != "flat" and dxy_trend == "flat":
            dxy_trend = dxy_direct_trend
            logger.info("DXY overridden from yfinance direct: %s (FX was flat)", dxy_direct_trend)

        # ── Risk sentiment from SPY + VIX ──
        risk_sentiment = self._compute_risk_sentiment(yf_data)

        # ── Individual macro fields ──
        us10y_trend = yf_data.get("us10y_trend", "unknown")
        real_yields_trend = yf_data.get("real_yields_trend", "unknown")
        oil_trend = yf_data.get("oil_trend", "unknown")
        vix_level = yf_data.get("vix_level")
        fed_tone = self._derive_fed_tone(yf_data)
        inflation_surprise = self._derive_inflation_surprise(yf_data)

        # ── Build missing fields list ──
        missing = []
        if dxy_trend == "unknown":
            missing.append("dxy_trend")
        if us10y_trend == "unknown":
            missing.append("us10y_trend")
        if real_yields_trend == "unknown":
            missing.append("real_yields_trend")
        if fed_tone == "unknown":
            missing.append("fed_tone")
        if oil_trend == "unknown":
            missing.append("oil_trend")
        if inflation_surprise == "unknown":
            missing.append("inflation_surprise")

        # ── Freshness summary ──
        stale_symbols = {k: v for k, v in staleness.items() if v.get("stale")}
        freshness = "OK" if not stale_symbols else "STALE"
        freshness_warnings = []
        for sym, info in stale_symbols.items():
            hours = info.get("age_hours", 0)
            freshness_warnings.append(f"{sym}: data {hours:.0f}h old (market closed or holiday)")
        if freshness_warnings:
            logger.warning("Stale macro data: %s", "; ".join(freshness_warnings))

        context = {
            "source": "yfinance_macro_proxy",
            "provider": "yfinance",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "freshness": freshness,
            "update_frequency": "hourly",
            "quota_policy": {
                "credits_used_estimate": 0,
                "daily_estimate_at_hourly": 0,
                "free_daily_limit": "unlimited",
                "yfinance_credits": 0,
                "yfinance_note": "completely free, no API key, no quota",
            },
            # MacroFundamentalAgent fields — populated from yfinance data
            "dxy_trend": dxy_trend,
            "usd_trend": dxy_trend,
            "risk_sentiment": risk_sentiment,
            "us10y_trend": us10y_trend,
            "real_yields_trend": real_yields_trend,
            "fed_tone": fed_tone,
            "inflation_surprise": inflation_surprise,
            "oil_trend": oil_trend,
            # Extra context
            "vix_level": vix_level,
            "yield_curve_spread": yf_data.get("yield_curve_spread"),
            "dxy_direct_trend": dxy_direct_trend,
            "fx_observations": yf_data.get("fx_observations", {}),
            "yfinance_observations": yf_data,
            "freshness_detail": staleness,
            "stale_symbols": list(stale_symbols.keys()),
            "errors": yf_errors[:8],
            "data_quality": {
                "source": "yfinance",
                "freshness": freshness,
                "stale_symbols": len(stale_symbols),
                "fx_symbols": len(yf_data.get("fx_observations", {})),
                "macro_symbols": len([k for k in yf_data if k not in {"fx_observations", "_errors", "_staleness"}]),
                "missing_fields": missing,
            },
        }
        return context

    # ── yfinance data fetching ────────────────────────────────────────────

    def _fetch_yfinance_data(self) -> Dict[str, Any]:
        """Fetch all macro data from Yahoo Finance (completely free, no API key).

        Uses 1h interval first. If last candle is >48h old (market closed /
        holiday), retries with 1d interval for that symbol.

        Returns dict with:
          - fx_observations: {symbol: {trend_pct, usd_score, usd_read, ...}}
          - us10y_trend, real_yields_trend, oil_trend: trend labels
          - dxy_direct_trend, dxy_direct_value: DXY direct index
          - vix_level: current VIX value
          - yield_curve_spread: 10Y - 13-week spread
          - _staleness: {symbol: {age_hours, stale, last_candle_time}}
        """
        if not _YF_AVAILABLE:
            return {"_errors": ["yfinance not installed"]}

        result: Dict[str, Any] = {}
        errors: List[str] = []
        fx_observations: Dict[str, Dict[str, Any]] = {}
        staleness: Dict[str, Dict[str, Any]] = {}

        for symbol, meta in self.YFINANCE_SYMBOLS.items():
            try:
                hist, age_hours, last_time = self._fetch_with_freshness(symbol)
                if hist is None:
                    continue

                closes = hist["Close"].dropna()
                if len(closes) < 2:
                    continue

                # Track staleness
                staleness[symbol] = {
                    "age_hours": round(age_hours, 1),
                    "stale": age_hours > _STALE_THRESHOLD_HOURS,
                    "last_candle_time": last_time.isoformat() if last_time else None,
                }

                first = float(closes.iloc[0])
                last = float(closes.iloc[-1])
                trend_pct = ((last - first) / first * 100.0) if first else 0.0

                component = meta.get("component")
                field = meta.get("field")

                # ── FX pairs + SPY (component-based) ──
                if component in ("usd", "risk"):
                    inverse_usd = bool(meta.get("inverse_usd"))
                    usd_score = -trend_pct if inverse_usd else trend_pct

                    obs = {
                        "symbol": symbol,
                        "description": meta.get("description", symbol),
                        "component": component,
                        "usable": True,
                        "first_close": round(first, 6),
                        "last_close": round(last, 6),
                        "trend_pct": round(trend_pct, 4),
                        "usd_score": round(usd_score, 4) if component == "usd" else None,
                        "usd_read": self._trend_label(usd_score, up="stronger", down="weaker") if component == "usd" else None,
                        "data_age_hours": round(age_hours, 1),
                    }
                    fx_observations[symbol] = obs
                    # Store SPY trend for risk sentiment
                    if component == "risk":
                        result["risk_trend_pct"] = round(trend_pct, 4)

                # ── Macro fields (field-based) ──
                elif field:
                    result[field] = round(last, 4)
                    result[f"{field}_trend_pct"] = round(trend_pct, 4)

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
                    elif field == "inflation_tips":
                        result["tips_trend_pct"] = round(trend_pct, 4)
                        result["tips_value"] = round(last, 2)
                    elif field == "inflation_stip":
                        result["stip_trend_pct"] = round(trend_pct, 4)
                        result["stip_value"] = round(last, 2)

            except Exception as exc:  # noqa: BLE001
                errors.append(f"yfinance {symbol}: {exc}")
                logger.warning("yfinance fetch failed for %s: %s", symbol, exc)

        # ── Yield curve spread (10Y - 13-week T-bill) for fed_tone ──
        try:
            irx = _yf.Ticker("^IRX")
            irx_hist = irx.history(period="5d")
            if not irx_hist.empty:
                irx_last = float(irx_hist["Close"].iloc[-1])
                tnx_value = result.get("us10y_trend")
                if isinstance(tnx_value, (int, float)):
                    result["yield_curve_spread"] = round(tnx_value - irx_last, 3)
        except Exception:  # noqa: BLE001
            pass

        result["fx_observations"] = fx_observations
        result["_errors"] = errors
        result["_staleness"] = staleness
        return result

    def _fetch_with_freshness(self, symbol: str) -> Tuple[Optional[Any], float, Optional[datetime]]:
        """Fetch symbol data with freshness tracking.

        Primary: 5d × 1h interval. If last candle > 48h old, retry with 5d × 1d.

        Returns: (DataFrame_or_None, age_hours, last_candle_time)
        """
        now = datetime.now(timezone.utc)

        # ── Primary: 1h interval ──
        ticker = _yf.Ticker(symbol)
        hist = ticker.history(period="5d", interval="1h")

        if hist.empty:
            # ── Fallback: 1d interval ──
            hist = ticker.history(period="5d", interval="1d")
            if hist.empty:
                return None, 999.0, None

        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None, 999.0, None

        # Calculate age of last candle
        last_time = closes.index[-1]
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        age_hours = (now - last_time.astimezone(timezone.utc)).total_seconds() / 3600.0

        # If stale (>48h), retry with daily interval for better coverage
        if age_hours > _STALE_THRESHOLD_HOURS:
            logger.info(
                "%s: 1h data %.0fh old — retrying with 1d interval",
                symbol, age_hours,
            )
            try:
                hist_daily = ticker.history(period="5d", interval="1d")
                if not hist_daily.empty:
                    closes_daily = hist_daily["Close"].dropna()
                    if len(closes_daily) >= 2:
                        last_time_d = closes_daily.index[-1]
                        if last_time_d.tzinfo is None:
                            last_time_d = last_time_d.replace(tzinfo=timezone.utc)
                        age_daily = (now - last_time_d.astimezone(timezone.utc)).total_seconds() / 3600.0
                        # Use daily if it's same age or fresher
                        if age_daily <= age_hours:
                            logger.info(
                                "%s: using 1d data (%.0fh old, %d candles)",
                                symbol, age_daily, len(closes_daily),
                            )
                            return hist_daily, age_daily, last_time_d
            except Exception:  # noqa: BLE001
                pass

            logger.info(
                "%s: keeping stale 1h data (%.0fh old) — likely market holiday",
                symbol, age_hours,
            )

        return hist, age_hours, last_time

    # ── DXY from FX basket ────────────────────────────────────────────────

    def _compute_dxy_from_fx(self, yf_data: Dict[str, Any]) -> str:
        """Compute DXY trend from FX basket observations."""
        fx_obs = yf_data.get("fx_observations", {})
        usd_items = [v for v in fx_obs.values() if v.get("component") == "usd" and v.get("usable")]

        if not usd_items:
            return "unknown"

        usd_score = sum(float(x.get("usd_score", 0) or 0) for x in usd_items) / len(usd_items)

        # Use strongest pair's absolute score for more responsive DXY read,
        # since averaging 4 pairs often cancels out individual moves.
        max_usd_abs = max((abs(float(x.get("usd_score", 0) or 0)) for x in usd_items), default=0.0)
        if abs(usd_score) < 0.15 and max_usd_abs >= 0.15:
            for x in usd_items:
                if abs(float(x.get("usd_score", 0) or 0)) == max_usd_abs:
                    usd_score = float(x.get("usd_score", 0) or 0)
                    break

        return self._trend_label(usd_score, up="rising", down="falling")

    # ── Risk sentiment from SPY + VIX ─────────────────────────────────────

    def _compute_risk_sentiment(self, yf_data: Dict[str, Any]) -> str:
        """Compute risk sentiment from SPY trend and VIX level.

        VIX is the dominant signal when elevated:
          VIX ≥ 25 → risk_off (elevated fear overrides SPY)
          VIX ≤ 14 → risk_on  (complacency overrides SPY)
        Otherwise, use SPY trend.
        """
        vix_level = yf_data.get("vix_level")

        # VIX is the strongest risk signal — overrides everything
        if vix_level is not None:
            if vix_level >= 25:
                logger.info("Risk from VIX: risk_off (VIX=%.1f)", vix_level)
                return "risk_off"
            if vix_level <= 14:
                logger.info("Risk from VIX: risk_on (VIX=%.1f)", vix_level)
                return "risk_on"

        # VIX neutral or unavailable → use SPY trend
        risk_trend = float(yf_data.get("risk_trend_pct", 0.0) or 0.0)
        if risk_trend >= 0.15:
            return "risk_on"
        elif risk_trend <= -0.15:
            return "risk_off"

        return "neutral"

    # ── Fed tone from yield curve ─────────────────────────────────────────

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
                return "neutral"
            return "hawkish"
        elif us10y == "falling":
            if spread is not None and spread > 1.0:
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

    # ── Inflation surprise from TIPS ETFs ─────────────────────────────────

    def _derive_inflation_surprise(self, yf_data: Dict[str, Any]) -> str:
        """Derive inflation surprise from TIPS bond ETF trends.

        Logic:
        - TIP (broad TIPS) rising → investors buying inflation protection → HOT
        - TIP falling → inflation expectations cooling → COOL
        - Flat → NEUTRAL
        - Use STIP (short-term TIPS) as tiebreaker when TIP is flat
        """
        tips_pct = float(yf_data.get("tips_trend_pct", 0.0) or 0.0)
        stip_pct = float(yf_data.get("stip_trend_pct", 0.0) or 0.0)

        has_tips = "tips_trend_pct" in yf_data
        has_stip = "stip_trend_pct" in yf_data

        if not has_tips and not has_stip:
            return "unknown"

        # Primary signal from TIP (broad, more reliable)
        if has_tips:
            if tips_pct >= 0.15:
                return "hot"
            elif tips_pct <= -0.15:
                return "cool"
            # TIP flat — check STIP for short-term signal
            if has_stip:
                if stip_pct >= 0.15:
                    return "hot"
                elif stip_pct <= -0.15:
                    return "cool"
            return "neutral"

        # Only STIP available
        if has_stip:
            if stip_pct >= 0.15:
                return "hot"
            elif stip_pct <= -0.15:
                return "cool"
            return "neutral"

        return "unknown"

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _trend_label(value: float, up: str, down: str) -> str:
        if value >= 0.15:
            return up
        if value <= -0.15:
            return down
        return "flat"

    @staticmethod
    def _empty_context(reason: str) -> Dict[str, Any]:
        return {
            "source": "yfinance_macro_proxy",
            "provider": "none",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "freshness": "UNKNOWN",
            "dxy_trend": "unknown",
            "usd_trend": "unknown",
            "risk_sentiment": "neutral",
            "us10y_trend": "unknown",
            "real_yields_trend": "unknown",
            "fed_tone": "unknown",
            "inflation_surprise": "unknown",
            "oil_trend": "unknown",
            "data_quality": {"source": "none", "freshness": "UNKNOWN", "missing_fields": ["yfinance"], "fx_symbols": 0},
            "errors": [reason],
            "quota_policy": {"credits_used_estimate": 0, "daily_estimate_at_hourly": 0, "free_daily_limit": "unlimited"},
        }
