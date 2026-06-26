"""Validate GitHub Actions runtime configuration before running bot jobs.

This script intentionally prints only missing secret names and never prints values.
It also tests that Twelve Data API key is actually valid by making a real API call.

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
        "TWELVEDATA_API_KEY",
    ],
    "update-trades": [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "TWELVEDATA_API_KEY",
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


def _has_data_key() -> bool:
    """Return True if the market data API key is configured."""
    key = os.environ.get("TWELVEDATA_API_KEY", "").strip()
    return bool(key and not key.startswith("YOUR_"))


def _test_twelvedata_key() -> tuple[bool, str]:
    """Test that the TWELVEDATA_API_KEY can actually fetch data.

    Returns (ok, message).
    """
    import requests  # noqa: E402  — only needed when key exists

    key = os.environ.get("TWELVEDATA_API_KEY", "").strip()
    if not key:
        return False, "TWELVEDATA_API_KEY is empty"
    if key.startswith("YOUR_"):
        return False, "TWELVEDATA_API_KEY is a placeholder (YOUR_...)"

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": "XAU/USD",
        "interval": "15min",
        "outputsize": 5,
        "apikey": key,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "error":
            msg = data.get("message", "unknown error")
            if "Invalid API key" in msg or "apikey" in msg.lower():
                return False, "Twelve Data rejected the API key. Key is invalid."
            return False, f"Twelve Data error: {msg}"

        values = data.get("values", [])
        if values:
            return True, f"Twelve Data OK — received {len(values)} candles for XAU/USD"
        return False, "Twelve Data returned no data for XAU/USD"

    except requests.exceptions.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", "?")
        if status_code == 401:
            return False, "Twelve Data rejected the API key (HTTP 401)."
        return False, f"Twelve Data HTTP error {status_code}: {exc}"
    except Exception as exc:
        return False, f"Twelve Data connection failed: {exc}"


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

    # ── Live connectivity test for market data API ──────────────────
    if "TWELVEDATA_API_KEY" in required:
        if not _has_data_key():
            print("❌ TWELVEDATA_API_KEY not found!")
            print("   Get a free key: https://twelvedata.com/register (800 calls/day)")
            print("   Add in: GitHub repo → Settings → Secrets → TWELVEDATA_API_KEY")
            return 1

        print("🔑 Testing Twelve Data API key...")
        ok, msg = _test_twelvedata_key()
        if ok:
            print(f"   ✅ {msg}")
        else:
            print(f"   ❌ {msg}")
            print()
            print("Fix: Go to https://twelvedata.com/register → get a free key")
            print("     Then add it in: GitHub repo → Settings → Secrets → TWELVEDATA_API_KEY")
            return 1

    print(f"✅ Setup validation passed for mode: {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
