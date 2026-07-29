"""Not every hour deserves the same risk.

A discretionary trader takes the same setup differently at 03:00 in a dead
Asia range than at the London open, and pulls back ahead of a tier-one
release. The system grades the *setup* carefully and then sizes every
qualifying signal by that grade alone, as though the surrounding conditions
were identical.

This module produces one multiplier for the conditions, applied to position
size only. It deliberately cannot open or block a trade: entry remains a
question of structure and agreement, and a sizing input that could veto would
quietly become a second, untested admission gate.

Bounded below by `min_multiplier` so a cautious moment never becomes a zero
position by accident, and above by 1.0 so good conditions never inflate risk
beyond the configured base.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class MomentQualityService:
    """Scores the conditions a signal is being taken in."""

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        cfg = (self.config.get("moment_quality") or {}) if isinstance(self.config, dict) else {}
        self.enabled = bool(cfg.get("enabled", True))
        self.min_multiplier = float(cfg.get("min_multiplier", 0.5) or 0.5)
        self.low_session_factor = float(cfg.get("low_session_factor", 0.75) or 0.75)
        self.medium_session_factor = float(cfg.get("medium_session_factor", 0.9) or 0.9)
        self.news_caution_factor = float(cfg.get("news_caution_factor", 0.8) or 0.8)
        self.news_danger_factor = float(cfg.get("news_danger_factor", 0.6) or 0.6)
        self.extreme_volatility_factor = float(cfg.get("extreme_volatility_factor", 0.75) or 0.75)
        self.low_volatility_factor = float(cfg.get("low_volatility_factor", 0.85) or 0.85)

    def review(self, all_results: Dict[str, Any]) -> Dict[str, Any]:
        """Return a sizing multiplier plus the reasons behind it."""
        result = {
            "enabled": self.enabled,
            "multiplier": 1.0,
            "factors": [],
            "summary": "conditions neutral",
        }
        if not self.enabled:
            return result

        try:
            multiplier = 1.0
            factors: List[str] = []

            session = (all_results.get("session") or {}) if isinstance(all_results, dict) else {}
            quality = str(session.get("session_quality") or session.get("quality") or "").upper()
            if quality == "LOW":
                multiplier *= self.low_session_factor
                factors.append(f"low-quality session ×{self.low_session_factor:g}")
            elif quality == "MEDIUM":
                multiplier *= self.medium_session_factor
                factors.append(f"medium-quality session ×{self.medium_session_factor:g}")

            news = (all_results.get("news") or {}) if isinstance(all_results, dict) else {}
            status = str(news.get("market_status") or "").upper()
            risk_level = str(news.get("risk_level") or "").upper()
            if status in {"DANGER", "HIGH_VOLATILITY"} or risk_level in {"HIGH", "EXTREME"}:
                multiplier *= self.news_danger_factor
                factors.append(f"elevated news risk ×{self.news_danger_factor:g}")
            elif status == "CAUTION" or risk_level == "MEDIUM":
                multiplier *= self.news_caution_factor
                factors.append(f"news caution ×{self.news_caution_factor:g}")

            technical = (all_results.get("technical") or {}) if isinstance(all_results, dict) else {}
            regime = technical.get("market_regime") or (technical.get("technical") or {}).get("market_regime") or {}
            volatility = str((regime or {}).get("volatility_regime") or "").upper()
            if volatility in {"EXTREME", "VERY_HIGH"}:
                multiplier *= self.extreme_volatility_factor
                factors.append(f"extreme volatility ×{self.extreme_volatility_factor:g}")
            elif volatility in {"LOW", "VERY_LOW"}:
                multiplier *= self.low_volatility_factor
                factors.append(f"thin volatility ×{self.low_volatility_factor:g}")

            multiplier = max(self.min_multiplier, min(1.0, multiplier))
            result["multiplier"] = round(multiplier, 3)
            result["factors"] = factors
            result["summary"] = " · ".join(factors) if factors else "conditions neutral"
        except Exception as exc:  # noqa: BLE001 - sizing must never break a cycle
            logger.warning("Moment quality review failed: %s", exc)
            result["multiplier"] = 1.0
            result["summary"] = "unavailable"

        return result
