"""Validate GitHub Actions runtime configuration before running bot jobs.

This script intentionally prints only missing secret names and never prints values.
Usage:
    python scripts/validate_setup.py analyze
    python scripts/validate_setup.py update-trades
    python scripts/validate_setup.py daily-report
    python scripts/validate_setup.py test
"""

from __future__ import annotations

import os
import sys
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

    print(f"✅ Setup validation passed for mode: {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
