"""One fetched, persisted market-data snapshot shared by every core agent."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from services.timeframe_fusion import CANONICAL_TIMEFRAMES

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "storage" / "shared_market_data.json"


def _stamp(payload: Mapping[str, Any]) -> str:
    candles = payload.get("data") or []
    if isinstance(candles, list) and candles:
        return str((candles[-1] or {}).get("time") or payload.get("last_updated") or "")
    return str(payload.get("last_updated") or "")


def publish_shared_market_data(
    market_data: Dict[str, Any],
    config: Mapping[str, Any] | None = None,
    *,
    path: str | Path | None = None,
) -> Dict[str, Any]:
    """Validate, stamp and atomically persist the single cycle input.

    MarketDataService fetches once. This function does not fetch anything; it
    stores that exact frozen book and returns one object passed to all agents.
    """
    book = market_data.get("timeframes") or {}
    if not isinstance(book, dict):
        raise ValueError("shared market-data book is missing")
    shared_cfg = ((config or {}).get("shared_market_data") or {}) if isinstance(config, Mapping) else {}
    strict = bool(shared_cfg.get("enabled", False))
    descriptors: Dict[str, Dict[str, Any]] = {}
    identity_parts = [str(market_data.get("symbol") or "")]
    requested = CANONICAL_TIMEFRAMES if strict else tuple(tf for tf in CANONICAL_TIMEFRAMES if isinstance(book.get(tf), dict))
    if not requested and isinstance(market_data.get("data"), list) and market_data.get("data"):
        requested = (str(market_data.get("timeframe") or "15m"),)
        book = {**book, requested[0]: market_data}
    for timeframe in requested:
        payload = book.get(timeframe)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload.get("data"):
            raise ValueError(f"shared market-data book missing native {timeframe}")
        if strict and payload.get("resampled_from"):
            raise ValueError(f"shared market-data {timeframe} is resampled from {payload.get('resampled_from')}")
        stamp = _stamp(payload)
        source = str(payload.get("source") or market_data.get("source") or "unknown")
        descriptors[timeframe] = {
            "source": source,
            "last_candle_time": stamp,
            "candles": len(payload.get("data") or []),
        }
        identity_parts.extend([timeframe, source, stamp, str(len(payload.get("data") or []))])
    digest = hashlib.sha256("|".join(identity_parts).encode("utf-8")).hexdigest()[:16]
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    meta = {
        "snapshot_id": f"MT5BOOK::{digest}",
        "source": "mt5_native_shared_book",
        "fetched_once": True,
        "stored": True,
        "generated_at": generated,
        "timeframes": descriptors,
    }
    frozen = deepcopy(market_data)
    frozen["shared_market_data"] = meta
    output_path = Path(path or ((config or {}).get("shared_market_data") or {}).get("storage_path") or DEFAULT_PATH)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meta["storage_path"] = str(output_path)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output_path.parent,
            prefix=f".{output_path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(frozen, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, output_path)
    finally:
        if temp_name and os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass
    return frozen
