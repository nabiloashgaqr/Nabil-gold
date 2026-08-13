"""Build the two mandatory calibrated-confidence artifacts from MT5 history.

Executed by the one-shot VPS installer before the voting book is switched.
It reads native M5/M15/H1/H4 separately and broker ticks directly; it never
resamples one timeframe into another and never stores credentials.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import sqlite3
import statistics
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from agents.unified_trend_agent import FEATURE_NAMES, UnifiedTrendAgent
from services.auction_flow_store import AuctionFlowStore
from services.timeframe_fusion import CANONICAL_TIMEFRAMES
from utils.helpers import load_config
from utils.indicators import calculate_atr
from utils.instruments import point_size


def _mt5_ready():
    import MetaTrader5 as mt5
    path = os.environ.get("MT5_PATH") or None
    ok = mt5.initialize(path=path) if path else mt5.initialize()
    if not ok:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    login = int(os.environ.get("MT5_LOGIN") or 0)
    if login and not mt5.login(
        login,
        password=os.environ.get("MT5_PASSWORD") or "",
        server=os.environ.get("MT5_SERVER") or "",
    ):
        raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")
    info = mt5.account_info()
    if info is None:
        raise RuntimeError("MT5 account_info unavailable")
    demo_const = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
    if int(getattr(info, "trade_mode", -1)) != int(demo_const):
        raise RuntimeError("REAL ACCOUNT REFUSED: calibration installer requires MT5 DEMO")
    return mt5


def _symbol(config: Mapping[str, Any]) -> str:
    public = str(config.get("symbol") or "XAU/USD")
    return str((((config.get("execution") or {}).get("demo") or {}).get("symbol_map") or {}).get(public) or public.replace("/", ""))


def _native_rates(mt5, symbol: str, timeframe: str, days: int) -> List[Dict[str, Any]]:
    constants = {
        "5m": mt5.TIMEFRAME_M5,
        "15m": mt5.TIMEFRAME_M15,
        "1H": mt5.TIMEFRAME_H1,
        "4H": mt5.TIMEFRAME_H4,
    }
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, constants[timeframe], start, end)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No native MT5 {timeframe} rates for {symbol}: {mt5.last_error()}")
    result = [
        {
            "time": int(r["time"]), "open": float(r["open"]),
            "high": float(r["high"]), "low": float(r["low"]),
            "close": float(r["close"]), "volume": float(r["tick_volume"]),
        }
        for r in rates
    ]
    result.sort(key=lambda c: int(c["time"]))
    return result


def _index_at(times: Sequence[int], ts: int) -> int:
    return bisect.bisect_right(times, int(ts)) - 1


def _signed_percentile(value: float, distribution: Sequence[float]) -> float:
    if not distribution or value == 0:
        return 0.0
    rank = bisect.bisect_right(distribution, abs(float(value))) / len(distribution)
    return math.copysign(min(1.0, rank), value)


def _families(normalized: Mapping[str, float]) -> Dict[str, float]:
    return UnifiedTrendAgent._families(normalized)


def _edge_from_family_by_tf(by_tf: Mapping[str, Mapping[str, float]]) -> float:
    families = {}
    for family in ("ema", "structure", "momentum", "macd", "rsi"):
        families[family] = statistics.mean(float(by_tf[tf][family]) for tf in CANONICAL_TIMEFRAMES)
    values = list(families.values())
    base = statistics.mean(values)
    absolute = sum(abs(v) for v in values)
    coherence = abs(sum(values)) / absolute if absolute else 0.0
    return max(-1.0, min(1.0, base * coherence))


def _barrier_label(
    direction: int, price: float, atr: float,
    future: Sequence[Mapping[str, Any]],
) -> int | None:
    if direction == 0 or price <= 0 or atr <= 0:
        return None
    upper = price + atr
    lower = price - atr
    for bar in future[:16]:
        high = float(bar.get("high", 0) or 0)
        low = float(bar.get("low", 0) or 0)
        hit_up = high >= upper
        hit_down = low <= lower
        if hit_up and hit_down:
            return None
        if hit_up:
            return 1 if direction > 0 else 0
        if hit_down:
            return 1 if direction < 0 else 0
    return None


def _fit_monotonic_logistic(xs: Sequence[float], ys: Sequence[int]) -> Dict[str, float]:
    if len(xs) != len(ys) or len(xs) < 50:
        raise RuntimeError("Not enough samples for logistic calibration")
    rate = min(0.999, max(0.001, sum(ys) / len(ys)))
    a = math.log(rate / (1.0 - rate))
    b = 0.5
    for step in range(12000):
        ga = 0.0
        gb = 0.0
        for x, y in zip(xs, ys):
            z = max(-40.0, min(40.0, a + b * float(x)))
            p = 1.0 / (1.0 + math.exp(-z))
            err = p - float(y)
            ga += err
            gb += err * float(x)
        ga /= len(xs)
        gb /= len(xs)
        lr = 0.08 / (1.0 + step / 3000.0)
        a -= lr * ga
        b = max(0.0, b - lr * gb)
    return {"a": round(a, 10), "b": round(b, 10)}


def _write_checked(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = dict(payload)
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    raw["checksum"] = hashlib.sha256(canonical).hexdigest()
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def build_unified(
    config: Mapping[str, Any], rates: Mapping[str, List[Dict[str, Any]]], output: Path,
) -> Dict[str, Any]:
    times = {tf: [int(c["time"]) for c in rates[tf]] for tf in CANONICAL_TIMEFRAMES}
    raw_samples: List[tuple[int, Dict[str, Dict[str, float]]]] = []
    distributions: Dict[str, Dict[str, List[float]]] = {
        tf: {feature: [] for feature in FEATURE_NAMES} for tf in CANONICAL_TIMEFRAMES
    }
    for anchor_idx in range(204, len(rates["15m"]) - 16):
        ts = int(rates["15m"][anchor_idx]["time"])
        by_tf: Dict[str, Dict[str, float]] = {}
        valid = True
        for tf in CANONICAL_TIMEFRAMES:
            idx = _index_at(times[tf], ts)
            if idx < 204:
                valid = False
                break
            raw = UnifiedTrendAgent.raw_features(rates[tf][idx - 204: idx + 1])
            if raw is None:
                valid = False
                break
            by_tf[tf] = raw
            for feature, value in raw.items():
                distributions[tf][feature].append(abs(float(value)))
        if valid:
            raw_samples.append((anchor_idx, by_tf))
    for tf in CANONICAL_TIMEFRAMES:
        for feature in FEATURE_NAMES:
            distributions[tf][feature].sort()

    atr15 = calculate_atr(rates["15m"], 14)
    xs: List[float] = []
    ys: List[int] = []
    for idx, raw_by_tf in raw_samples:
        family_by_tf: Dict[str, Dict[str, float]] = {}
        for tf in CANONICAL_TIMEFRAMES:
            normalized = {
                feature: _signed_percentile(value, distributions[tf][feature])
                for feature, value in raw_by_tf[tf].items()
            }
            family_by_tf[tf] = _families(normalized)
        edge = _edge_from_family_by_tf(family_by_tf)
        label = _barrier_label(
            1 if edge > 0 else -1 if edge < 0 else 0,
            float(rates["15m"][idx]["close"]),
            float(atr15[idx] or 0),
            rates["15m"][idx + 1: idx + 17],
        )
        if label is not None:
            xs.append(abs(edge))
            ys.append(label)
    minimum = int((config.get("unified_trend") or {}).get("min_calibration_samples", 500) or 500)
    if len(xs) < minimum:
        raise RuntimeError(f"Unified Trend calibration has {len(xs)} resolved samples; need {minimum}")
    payload = {
        "version": str((config.get("unified_trend") or {}).get("calibration_version", "unified_trend_v1")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "mt5_native_M5_M15_H1_H4",
        "symbol": _symbol(config),
        "sample_count": len(xs),
        "correct_count": int(sum(ys)),
        "base_accuracy": round(sum(ys) / len(ys) * 100.0, 3),
        "outcome": {"barrier_atr_15m": 1.0, "horizon_15m_bars": 16},
        "features": distributions,
        "logistic": _fit_monotonic_logistic(xs, ys),
    }
    _write_checked(output, payload)
    return payload


def _fetch_ticks(mt5, symbol: str, days: int, store: AuctionFlowStore) -> int:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    cursor = start
    total = 0
    while cursor < end:
        stop = min(cursor + timedelta(days=1), end)
        ticks = mt5.copy_ticks_range(symbol, cursor, stop, mt5.COPY_TICKS_ALL)
        if ticks is None:
            raise RuntimeError(f"MT5 tick history failed {cursor.date()}: {mt5.last_error()}")
        total += store.bulk_record_ticks(ticks)
        cursor = stop
    current = mt5.symbol_info_tick(symbol)
    if current is not None:
        store.record_tick(current)
    store.mark_heartbeat()
    return total


def _value_area(profile: Mapping[float, int], pct: float = 70.0) -> tuple[float, float, float]:
    return AuctionFlowStore._value_area(profile, pct)


def _auction_response(rows: Sequence[Mapping[str, Any]], pressure: float, vah: float, val: float) -> float:
    closes = [float(r["close"]) for r in rows]
    if len(closes) >= 3 and all(c > vah for c in closes[-3:]) and pressure > 0:
        return 1.0
    if len(closes) >= 3 and all(c < val for c in closes[-3:]) and pressure < 0:
        return -1.0
    if rows and any(float(r["high"]) > vah for r in rows[-5:]) and closes[-1] < vah and pressure < 0:
        return -1.0
    if rows and any(float(r["low"]) < val for r in rows[-5:]) and closes[-1] > val and pressure > 0:
        return 1.0
    return 0.0


def build_auction(
    config: Mapping[str, Any], rates: Mapping[str, List[Dict[str, Any]]],
    store: AuctionFlowStore, output: Path,
) -> Dict[str, Any]:
    with sqlite3.connect(str(store.path)) as con:
        rows_raw = con.execute(
            "SELECT ts,open_mid,high_mid,low_mid,close_mid,sum_mid,tick_count,up_count,down_count "
            "FROM minutes ORDER BY ts"
        ).fetchall()
    rows = [
        {"ts": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
         "close": float(r[4]), "sum_mid": float(r[5]), "ticks": int(r[6]),
         "up": int(r[7]), "down": int(r[8])}
        for r in rows_raw
    ]
    if len(rows) < 500:
        raise RuntimeError(f"Auction history has only {len(rows)} minute rows")
    tf_times = {tf: [int(c["time"]) for c in rates[tf]] for tf in CANONICAL_TIMEFRAMES}
    tf_atr = {tf: calculate_atr(rates[tf], 14) for tf in CANONICAL_TIMEFRAMES}
    m15 = rates["15m"]
    atr15 = tf_atr["15m"]
    bin_size = float((config.get("auction_flow") or {}).get("profile_bin_points", 10) or 10) * point_size(str(config.get("symbol") or "XAU/USD"), dict(config))

    xs: List[float] = []
    ys: List[int] = []
    session_key = None
    session_zone = ZoneInfo(str((config.get("auction_flow") or {}).get("session_timezone", "Asia/Hebron")))
    profile: Dict[float, int] = {}
    sum_mid = 0.0
    sum_ticks = 0
    recent: deque[Dict[str, Any]] = deque(maxlen=31)
    for i, row in enumerate(rows):
        dt = datetime.fromtimestamp(row["ts"], tz=timezone.utc)
        key = dt.astimezone(session_zone).date()
        if key != session_key:
            session_key = key
            profile = {}
            sum_mid = 0.0
            sum_ticks = 0
            recent.clear()
        ticks = row["ticks"]
        if ticks <= 0:
            continue
        avg = row["sum_mid"] / ticks
        pbin = round(round(avg / max(bin_size, 1e-9)) * max(bin_size, 1e-9), 8)
        profile[pbin] = profile.get(pbin, 0) + ticks
        sum_mid += row["sum_mid"]
        sum_ticks += ticks
        recent.append(row)
        if i % 5 != 0 or len(recent) < 6 or sum_ticks <= 0:
            continue
        vwap = sum_mid / sum_ticks
        poc, vah, val = _value_area(profile, 70.0)
        if min(vwap, poc, vah, val) <= 0:
            continue
        last5 = list(recent)[-5:]
        up5 = sum(r["up"] for r in last5)
        down5 = sum(r["down"] for r in last5)
        pressure5 = (up5 - down5) / max(up5 + down5, 1)
        last1 = last5[-1]
        pressure1 = (last1["up"] - last1["down"]) / max(last1["up"] + last1["down"], 1)
        pressure = max(-1.0, min(1.0, statistics.mean([pressure1, pressure5])))
        tf_context: List[float] = []
        valid = True
        for tf in CANONICAL_TIMEFRAMES:
            idx = _index_at(tf_times[tf], row["ts"])
            if idx < 14 or tf_atr[tf][idx] in (None, 0):
                valid = False
                break
            close = float(rates[tf][idx]["close"])
            atr = float(tf_atr[tf][idx] or 0)
            tf_context.append(max(-1.0, min(1.0, statistics.mean([(close - vwap) / atr, (close - poc) / atr]))))
        if not valid:
            continue
        idx15 = _index_at(tf_times["15m"], row["ts"])
        if idx15 < 14 or idx15 + 16 >= len(m15):
            continue
        atr = float(atr15[idx15] or 0)
        current_value = max(-1.0, min(1.0, statistics.mean([(row["close"] - vwap) / atr, (row["close"] - poc) / atr])))
        value_location = max(-1.0, min(1.0, statistics.mean([current_value, *tf_context])))
        response = _auction_response(last5, pressure, vah, val)
        families = [pressure, value_location, response]
        base = statistics.mean(families)
        absolute = sum(abs(v) for v in families)
        coherence = abs(sum(families)) / absolute if absolute else 0.0
        prior_rates = [r["ticks"] for r in list(recent)[:-1] if r["ticks"] > 0]
        median_rate = statistics.median(prior_rates) if prior_rates else 0.0
        activity = row["ticks"] / median_rate if median_rate else 0.0
        quality = max(0.50, min(1.0, activity))
        edge = max(-1.0, min(1.0, base * coherence * quality))
        label = _barrier_label(
            1 if edge > 0 else -1 if edge < 0 else 0,
            row["close"], atr, m15[idx15 + 1: idx15 + 17],
        )
        if label is not None:
            xs.append(abs(edge))
            ys.append(label)
    minimum = int((config.get("auction_flow") or {}).get("min_calibration_samples", 300) or 300)
    if len(xs) < minimum:
        raise RuntimeError(f"Auction Flow calibration has {len(xs)} resolved samples; need {minimum}")
    payload = {
        "version": str((config.get("auction_flow") or {}).get("calibration_version", "auction_flow_v1")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "mt5_native_ticks_plus_M5_M15_H1_H4",
        "symbol": _symbol(config),
        "sample_count": len(xs),
        "correct_count": int(sum(ys)),
        "base_accuracy": round(sum(ys) / len(ys) * 100.0, 3),
        "outcome": {"barrier_atr_15m": 1.0, "horizon_15m_bars": 16},
        "logistic": _fit_monotonic_logistic(xs, ys),
    }
    _write_checked(output, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--tick-days", type=int, default=30)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--reset-auction-store", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    mt5 = _mt5_ready()
    symbol = _symbol(config)
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"MT5 symbol_select failed for {symbol}: {mt5.last_error()}")
    rates = {tf: _native_rates(mt5, symbol, tf, args.days) for tf in CANONICAL_TIMEFRAMES}
    for tf in CANONICAL_TIMEFRAMES:
        print(f"NATIVE {tf}: {len(rates[tf])} candles")
    unified_cfg = config.get("unified_trend", {}) or {}
    unified = build_unified(
        config, rates,
        Path(str(unified_cfg.get("calibration_path") or "storage/model_calibration/unified_trend_v1.json")),
    )
    print(f"UNIFIED TREND CALIBRATION OK: {unified['sample_count']} samples")

    auction_cfg = config.get("auction_flow", {}) or {}
    store_path = Path(str(auction_cfg.get("storage_path") or "storage/auction_flow.sqlite3"))
    if args.reset_auction_store:
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(store_path) + suffix).unlink()
            except FileNotFoundError:
                pass
    store = AuctionFlowStore(store_path)
    total_ticks = _fetch_ticks(mt5, symbol, args.tick_days, store)
    print(f"AUCTION TICKS LOADED: {total_ticks}")
    auction = build_auction(
        config, rates, store,
        Path(str(auction_cfg.get("calibration_path") or "storage/model_calibration/auction_flow_v1.json")),
    )
    print(f"AUCTION FLOW CALIBRATION OK: {auction['sample_count']} samples")
    store.prune(
        second_days=int(auction_cfg.get("second_retention_days", 7) or 7),
        minute_days=int(auction_cfg.get("minute_retention_days", 90) or 90),
    )
    print("CALIBRATIONS READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
