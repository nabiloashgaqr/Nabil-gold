"""Validate GitHub Actions runtime configuration before running bot jobs.

This script intentionally prints only missing secret names and never prints values.
It also tests that Finnhub API key is actually valid by making a real API call.

Usage:
    python scripts/validate_setup.py analyze
    python scripts/validate_setup.py update-trades
    python scripts/validate_setup.py daily-report
    python scripts/validate_setup.py test
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.helpers import load_config  # noqa: E402


REQUIRED_BY_MODE = {
    "analyze": [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "FINNHUB_API_KEY",  # Finnhub only (Twelve Data completely removed)
    ],
    "update-trades": [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "FINNHUB_API_KEY",
    ],
    "daily-report": [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "SUPABASE_URL",
        "SUPABASE_KEY",
    ],
    "telegram": [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ],
    "test": [],
}


def _missing(names: Iterable[str]) -> list[str]:
    missing: list[str] = []
    for name in names:
        value = os.environ.get(name)
        if value is None or not str(value).strip() or str(value).startswith("YOUR_"):
            missing.append(name)
    # Remove duplicates while preserving order
    seen = set()
    unique_missing = []
    for m in missing:
        if m not in seen:
            seen.add(m)
            unique_missing.append(m)
    return unique_missing


def _test_finnhub_key() -> tuple[bool, str]:
    """Test that the FINNHUB_API_KEY can actually fetch data.

    Returns (ok, message).
    """
    import requests  # noqa: E402  — only needed when key exists

    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        return False, "FINNHUB_API_KEY is empty"
    if key.startswith("YOUR_"):
        return False, "FINNHUB_API_KEY is a placeholder (YOUR_...)"

    # Use a recent 15-minute window for OANDA:XAU_USD
    end_ts = int(time.time())
    start_ts = end_ts - 3600  # last hour

    url = "https://finnhub.io/api/v1/forex/candle"
    params = {
        "symbol": "OANDA:XAU_USD",
        "resolution": "15",
        "from": start_ts,
        "to": end_ts,
        "token": key,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("s", "")
        if status == "ok" and data.get("c"):
            return True, f"Finnhub OK — received {len(data['c'])} candles for XAU/USD"
        if status == "no_data":
            # Market might be closed, but the key is valid.
            return True, "Finnhub key valid (no_data = market closed or weekend)"
        return False, f"Finnhub returned unexpected status: '{status}' — response: {str(data)[:200]}"
    except requests.exceptions.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", "?")
        if status_code == 401:
            return False, "Finnhub rejected the API key (HTTP 401 Unauthorized). Key is invalid or expired."
        if status_code == 429:
            return True, "Finnhub rate-limited (HTTP 429) — key is valid but try again later"
        return False, f"Finnhub HTTP error {status_code}: {exc}"
    except Exception as exc:
        return False, f"Finnhub connection failed: {exc}"


def main() -> int:
    mode = sys.argv[1].strip().lower() if len(sys.argv) > 1 else "analyze"
    if mode not in REQUIRED_BY_MODE:
        print(f"❌ Unknown validation mode: {mode}")
        print(f"Allowed modes: {', '.join(sorted(REQUIRED_BY_MODE))}")
        return 2

    config = load_config()
    required = list(REQUIRED_BY_MODE[mode])
    missing = _missing(required)

    warnings: list[str] = []
    if mode in {"analyze", "update-trades"}:
        data_source = config.get("data_source", {})
        allow_synth = bool(data_source.get("allow_synthetic_in_production", False))
        if allow_synth:
            warnings.append("data_source.allow_synthetic_in_production=true: production may use demo data")

    for warning in warnings:
        print(f"⚠️ {warning}")

    if missing:
        print("❌ Missing required GitHub Secrets / environment variables:")
        for name in missing:
            print(f"   - {name}")
        print("\nAdd them in: GitHub repo → Settings → Secrets and variables → Actions")
        return 1

    # ── Live connectivity test for Finnhub ──────────────────────────
    if "FINNHUB_API_KEY" in required:
        print("🔑 Testing Finnhub API key...")
        ok, msg = _test_finnhub_key()
        if ok:
            print(f"   ✅ {msg}")
        else:
            print(f"   ❌ {msg}")
            print()
            print("Fix: Go to https://finnhub.io/register → get a free key")
            print("     Then add it in: GitHub repo → Settings → Secrets → FINNHUB_API_KEY")
            return 1

    print(f"✅ Setup validation passed for mode: {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
