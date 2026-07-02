"""Unified session classification — single source of truth.

Every module that needs to classify a trade by its trading session must use
functions from this file instead of inventing its own labels or reading the
raw config session name (e.g. "Main Trading Session").

Classification is based on the local hour in Asia/Jerusalem timezone,
matching the market sessions that matter for XAU/USD trading.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_TIMEZONE: str = "Asia/Jerusalem"

SESSION_ORDER: List[str] = [
    "Asia Morning",
    "London / Europe Midday",
    "London + New York Afternoon",
    "New York Evening",
    "Late New York Night",
]

SESSION_AR: Dict[str, str] = {
    "Asia Morning": "آسيا صباحاً",
    "London / Europe Midday": "لندن / أوروبا ظهراً",
    "London + New York Afternoon": "لندن + أمريكا عصراً",
    "New York Evening": "أمريكا مساءً",
    "Late New York Night": "أمريكا متأخرة ليلاً",
}

# (start_hour_inclusive, end_hour_exclusive, label)
SESSION_RANGES: List[tuple] = [
    (3, 10, "Asia Morning"),
    (10, 15, "London / Europe Midday"),
    (15, 19, "London + New York Afternoon"),
    (19, 24, "New York Evening"),
    (0, 3, "Late New York Night"),
]

# Fast lookup set for validating stored labels
_VALID_LABELS: set = set(SESSION_ORDER)

# Legacy names that older trades may have stored — map them to the new labels
# so old data is not lost during the transition.
# IMPORTANT: We do NOT map generic names like "Main Trading Session" here
# because they span multiple sessions and must be reclassified from the
# trade's timestamp instead.
_LEGACY_MAP: Dict[str, str] = {
    # Common variants from TradingSessionAgent or manual entry
    "asian": "Asia Morning",
    "asia": "Asia Morning",
    "london-ny overlap": "London + New York Afternoon",
    "london/ny overlap": "London + New York Afternoon",
    "london + ny": "London + New York Afternoon",
    "london+ny": "London + New York Afternoon",
    "overlap": "London + New York Afternoon",
    "europe": "London / Europe Midday",
    "london": "London / Europe Midday",
    "new york": "New York Evening",
    "ny": "New York Evening",
    "late ny": "Late New York Night",
    "late new york": "Late New York Night",
}

# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def classify_session(hour_local: int) -> str:
    """Classify a local hour (0-23) into a session name.

    >>> classify_session(4)
    'Asia Morning'
    >>> classify_session(14)
    'London / Europe Midday'
    >>> classify_session(17)
    'London + New York Afternoon'
    >>> classify_session(21)
    'New York Evening'
    >>> classify_session(1)
    'Late New York Night'
    """
    for start, end, name in SESSION_RANGES:
        if start <= hour_local < end:
            return name
    return "Late New York Night"


def session_label_from_utc(dt: Optional[datetime | str]) -> str:
    """Return session name from a UTC (or tz-aware) timestamp.

    This is the **single source of truth** for session classification
    in Python code.  Use it everywhere instead of reading the raw config
    session name.

    >>> from datetime import datetime, timezone
    >>> # 06:00 UTC = 09:00 Asia/Jerusalem → Asia Morning
    >>> session_label_from_utc("2026-01-01T06:00:00Z")
    'Asia Morning'
    """
    if dt is None:
        return "Unknown"
    try:
        text = str(dt).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone(ZoneInfo(SESSION_TIMEZONE))
        return classify_session(local.hour)
    except Exception:
        return "Unknown"


def _normalize_stored(stored: str) -> Optional[str]:
    """Try to normalize a stored session label to a valid unified label.

    Returns the unified label if possible, or None if it cannot be mapped.
    """
    stripped = stored.strip()
    if not stripped or stripped.upper() in {"UNKNOWN", "NONE", "N/A"}:
        return None
    # Already a valid label
    if stripped in _VALID_LABELS:
        return stripped
    # Try case-insensitive match
    for valid in SESSION_ORDER:
        if stripped.lower() == valid.lower():
            return valid
    # Try legacy mapping
    lower = stripped.lower()
    if lower in _LEGACY_MAP:
        return _LEGACY_MAP[lower]
    # Partial match (e.g. "asian session" → "Asia Morning")
    for legacy_key, mapped in _LEGACY_MAP.items():
        if legacy_key in lower:
            return mapped
    return None


def session_label_from_trade(trade: Dict[str, Any]) -> str:
    """Return the session name for a trade dict.

    Resolution order:
    1. If ``session_label`` is already a valid unified name → use it.
    2. If ``signal_snapshot.session_info.current_session`` is valid → use it.
    3. Compute from the trade's entry/open timestamp.

    This function handles both the new unified labels and legacy stored names,
    so it works correctly with old and new data without requiring a backfill.
    """
    # 1. Direct field
    stored = str(trade.get("session_label") or "").strip()
    normalized = _normalize_stored(stored)
    if normalized is not None:
        return normalized

    # 2. Snapshot
    snap = trade.get("signal_snapshot") or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except Exception:
            snap = {}
    if not isinstance(snap, dict):
        snap = {}
    si = snap.get("session_info") or {}
    if isinstance(si, dict):
        for key in ("current_session", "session", "session_name"):
            val = str(si.get(key) or "").strip()
            normalized = _normalize_stored(val)
            if normalized is not None:
                return normalized

    # 3. Compute from timestamp
    for key in ("entry_time", "opened_at", "created_at", "updated_at"):
        ts = trade.get(key)
        if ts:
            result = session_label_from_utc(ts)
            if result != "Unknown":
                return result

    # 4. Last resort: if we have a non-empty stored value, keep it as-is
    #    (preserves whatever was stored rather than losing it)
    if stored:
        return stored

    return "Unknown"


def session_sort_key(label: str) -> int:
    """Return sort order for a session label (lower = earlier session)."""
    try:
        return SESSION_ORDER.index(label)
    except ValueError:
        return 99


def session_arabic(label: str) -> str:
    """Return the Arabic label for a session, or the English name if unknown."""
    return SESSION_AR.get(label, label)
