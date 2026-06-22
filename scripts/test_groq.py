"""Groq smoke test for GitHub Actions.

Run via: Actions → 🤖 Groq Smoke Test → Run workflow
This script never prints API key values.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _notify_telegram(title: str, details: str, success: bool) -> None:
    """Best-effort Telegram notification; does not hide the original test result."""
    try:
        config = load_config()
        telegram = TelegramService(config)
        emoji = "✅" if success else "❌"
        text = (
            f"{emoji} <b>{title}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{details}\n"
            f"⏰ Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        telegram.send_message(text, urgent=not success)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Telegram notification skipped/failed: {exc}")


def _extract_error(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    if error:
        return str(error)
    return str(payload)[:600]


def main() -> int:
    setup_logging()
    config = load_config()
    ai_config = config.get("ai_service", {})
    model = str(ai_config.get("model") or "llama-3.1-8b-instant")
    token = _env("GROQ_API_KEY")

    print("🔎 Groq smoke test diagnostics")
    print(f"- GROQ_API_KEY present: {bool(token)}")
    print(f"- configured provider: {ai_config.get('provider')}")
    print(f"- configured model: {model}")

    if not token:
        message = "GROQ_API_KEY is missing or empty in GitHub Secrets."
        print(f"❌ {message}")
        _notify_telegram("Groq Smoke Test failed", message, success=False)
        return 1

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a health-check endpoint. Return JSON only.",
            },
            {
                "role": "user",
                "content": 'Return exactly this JSON: {"status":"ok","provider":"groq"}',
            },
        ],
        "temperature": 0,
        "max_tokens": 80,
        "response_format": {"type": "json_object"},
    }

    print("📡 Calling Groq API...")
    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=35,
        )
    except Exception as exc:  # noqa: BLE001
        message = f"Groq request failed before response: {exc}"
        print(f"❌ {message}")
        _notify_telegram("Groq Smoke Test failed", message, success=False)
        return 1

    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        message = f"Groq returned non-JSON response. HTTP {response.status_code}: {response.text[:500]}"
        print(f"❌ {message}")
        _notify_telegram("Groq Smoke Test failed", message, success=False)
        return 1

    if not response.ok:
        message = f"Groq API error. HTTP {response.status_code}: {_extract_error(data)}"
        print(f"❌ {message}")
        _notify_telegram("Groq Smoke Test failed", message, success=False)
        return 1

    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        message = f"Groq responded but content was not valid JSON: {exc}; raw={data}"
        print(f"❌ {message}")
        _notify_telegram("Groq Smoke Test failed", message[:1200], success=False)
        return 1

    usage = data.get("usage", {}) or {}
    details = (
        "Groq is working.\n"
        f"Model: {model}\n"
        f"Response: {parsed}\n"
        f"Tokens: {usage.get('total_tokens', 'N/A')}"
    )
    print("✅ Groq API is working")
    print(details)
    _notify_telegram("Groq Smoke Test successful", details, success=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
