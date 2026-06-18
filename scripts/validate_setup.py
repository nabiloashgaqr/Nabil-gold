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


AI_KEYS = ["OPENAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"]


REQUIRED_BY_MODE = {
    "analyze": [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "TWELVE_DATA_API_KEY",
    ],
    "update-trades": [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "TWELVE_DATA_API_KEY",
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
    return missing


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

    if mode == "analyze":
        ai_config = config.get("ai_service", {})
        ai_enabled = bool(ai_config.get("enabled", False))
        fallback = bool(ai_config.get("fallback_to_classic", True))
        if ai_enabled and not any(os.environ.get(k) for k in AI_KEYS):
            if fallback:
                warnings.append("No AI provider key found; analysis will fallback to classic if code path allows it")
            else:
                missing.append("ONE_OF_OPENAI_GROQ_ANTHROPIC_GEMINI_API_KEY")

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
