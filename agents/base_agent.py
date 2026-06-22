"""BaseAgent: واجهة موحدة ترث منها كل وكلاء التحليل والقرار.

Every agent exposes a safe ``analyze``/``check``/``evaluate`` style method and
returns dictionaries instead of raising fatal exceptions. This keeps GitHub
Actions runs resilient even if one component fails.
"""

from __future__ import annotations

import logging
from abc import ABC
from datetime import datetime, timezone
from typing import Any, Dict

class BaseAgent(ABC):
    """قاعدة مشتركة لكل الوكلاء / Shared base class for all agents."""

    name: str = "base"

    def __init__(self, config: Dict[str, Any] | None = None, ai_service: Any = None) -> None:
        self.config = config or {}
        self.ai_service = ai_service
        self.logger = logging.getLogger(self.__class__.__name__)

    def now_iso(self) -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def neutral_result(self, reason: str = "Not enough data") -> Dict[str, Any]:
        """Return a safe neutral result used when an agent cannot decide."""
        return {
            "agent": self.name,
            "direction": "NEUTRAL",
            "confidence": 0,
            "signals": [],
            "summary": reason,
        }

    def safe_float(self, value: Any, default: float = 0.0) -> float:
        """Convert a value to float safely."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default