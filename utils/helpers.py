"""Shared helper utilities for configuration, storage, sessions and formatting."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from utils.instruments import point_size, price_decimals


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"
DEFAULT_TRADES_PATH = PROJECT_ROOT / "storage" / "trades.json"


_PROMPT_INJECTION_MARKERS = ["SYSTEM:", "Ignore previous", "###", "<|", "PROMPT:", "ASSISTANT:"]


def sanitize_prompt_text(text: Any, max_len: int = 240) -> str:
    """Strip characters/phrases commonly used for prompt injection before any
    semi-external text (news event titles, memory rules, AI-generated
    reasoning, etc.) is embedded into an AI prompt.

    This is a defensive measure, not a guarantee: it removes a known set of
    injection markers and structural characters (backticks, braces) and caps
    length, but cannot catch every possible injection phrasing.
    """
    if not text:
        return ""
    s = str(text).replace("`", "'").replace("{", "(").replace("}", " )")
    for marker in _PROMPT_INJECTION_MARKERS:
        s = s.replace(marker, "")
    s = " ".join(s.split())
    return s[:max_len]


def setup_logging(level: int = logging.INFO) -> None:
    """Configure console logging for GitHub Actions."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def load_config(path: str | Path | None = None) -> Dict[str, Any]:
    """Load config.json and resolve ENV: placeholders when useful."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as file:
        config = json.load(file)
    return config


def get_env_or_config(config: Dict[str, Any], dotted_path: str, env_name: str | None = None, default: Any = None) -> Any:
    """Read value from environment first, then from nested config."""
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    current: Any = config
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    if isinstance(current, str) and current.startswith("ENV:"):
        return os.environ.get(current.replace("ENV:", "", 1), default)
    return current


def format_price(price: float | int | str | None, symbol: str | None = None) -> str:
    """Format a price using instrument-specific decimals."""
    decimals = price_decimals(symbol) if symbol else 2
    try:
        return f"{float(price):.{decimals}f}"
    except (TypeError, ValueError):
        return f"{0:.{decimals}f}"


def calculate_pips(entry: float, exit_price: float, trade_type: str = "BUY", symbol: str | None = None) -> float:
    """Calculate broker-style points for any configured instrument."""
    ps = point_size(symbol)
    if trade_type.upper() == "SELL":
        return round((entry - exit_price) / ps, 1)
    return round((exit_price - entry) / ps, 1)


def now_utc() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)



def canonical_session_label(dt: datetime | None = None, tz_name: str = "Asia/Jerusalem") -> str:
    """Return the canonical session label used across dashboard and Telegram.

    Labels are based on local Asia/Jerusalem time:
    - Asia Morning: 03:00-09:59
    - London / Europe Midday: 10:00-14:59
    - London + New York Afternoon: 15:00-18:59
    - New York Evening: 19:00-23:59
    - Late New York Night: 00:00-02:59
    """
    from zoneinfo import ZoneInfo

    dt = dt or now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        local = dt.astimezone(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001
        local = dt.astimezone(timezone.utc)
    hour = local.hour
    if 3 <= hour < 10:
        return "Asia Morning"
    if 10 <= hour < 15:
        return "London / Europe Midday"
    if 15 <= hour < 19:
        return "London + New York Afternoon"
    if 19 <= hour < 24:
        return "New York Evening"
    return "Late New York Night"


def get_current_session(dt: datetime | None = None) -> str:
    """Return a clear FX session label with time range in UTC."""
    dt = dt or now_utc()
    hour = dt.hour
    if 0 <= hour < 7:
        return "Asian Session (00:00-07:00 UTC)"
    if 7 <= hour < 12:
        return "London Session (07:00-12:00 UTC)"
    if 12 <= hour < 16:
        return "London-NY Overlap (12:00-16:00 UTC)"
    if 16 <= hour < 21:
        return "New York Session (16:00-21:00 UTC)"
    return "Late NY Session (21:00-00:00 UTC)"


def is_market_open(dt: datetime | None = None) -> bool:
    """Approximate FX market open state in UTC."""
    dt = dt or now_utc()
    weekday = dt.weekday()  # Monday=0
    if weekday == 5:  # Saturday
        return False
    if weekday == 6 and dt.hour < 22:  # Sunday before open
        return False
    if weekday == 4 and dt.hour >= 22:  # Friday after close
        return False
    return True


def load_trades(path: str | Path | None = None) -> List[Dict[str, Any]]:
    """Load local trades fallback JSON."""
    trades_path = Path(path) if path else DEFAULT_TRADES_PATH
    if not trades_path.exists():
        return []
    try:
        with trades_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def save_trades(trades: List[Dict[str, Any]], path: str | Path | None = None) -> None:
    """Persist local trades fallback JSON."""
    trades_path = Path(path) if path else DEFAULT_TRADES_PATH
    trades_path.parent.mkdir(parents=True, exist_ok=True)
    with trades_path.open("w", encoding="utf-8") as file:
        json.dump(trades, file, ensure_ascii=False, indent=2)


def save_trade(trade: Dict[str, Any], path: str | Path | None = None) -> None:
    """Append a trade to local fallback storage."""
    trades = load_trades(path)
    trades.append(trade)
    save_trades(trades, path)


def get_today_trades(path: str | Path | None = None) -> List[Dict[str, Any]]:
    """Return local fallback trades created today UTC."""
    today = now_utc().date().isoformat()
    results = []
    for trade in load_trades(path):
        created_at = str(trade.get("created_at", ""))
        if created_at.startswith(today):
            results.append(trade)
    return results


def get_agent_weights(config: Dict[str, Any]) -> Dict[str, float]:
    """Return the canonical agent weights from config with safe fallbacks.

    This is the SINGLE SOURCE OF TRUTH for agent weights across the codebase.
    Any module that needs agent weights should call this function instead of
    hard-coding defaults locally.

    Keys starting with '_' (e.g. _description) are silently ignored.
    Non-numeric values are skipped safely.
    """
    config_weights = config.get("agent_weights", {}) or {}
    if config_weights:
        weights = {}
        for k, v in config_weights.items():
            if k.startswith("_"):
                continue
            try:
                weights[k] = float(v)
            except (TypeError, ValueError):
                continue
        total = sum(weights.values())
        # Normalize if the sum is materially off 1.0 (protects against bad configs/tests)
        if total > 0 and abs(total - 1.0) > 0.01:
            weights = {k: v / total for k, v in weights.items()}
        if weights:
            return weights
    return {
        "technical": 0.20,
        "classical": 0.25,
        "smc": 0.20,
        "price_action": 0.20,
        "multitimeframe": 0.15,
    }
