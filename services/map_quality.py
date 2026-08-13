"""Bounded, transparent map-display quality (not win probability)."""
from __future__ import annotations

from typing import Any, Dict, Mapping


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _grade(score: float) -> str:
    if score >= 88:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def bounded_map_display_quality(plan: Mapping[str, Any], config: Mapping[str, Any]) -> Dict[str, Any]:
    """Compute a non-saturating map score from four independent components.

    Context bonuses no longer pile onto the displayed score until it clips at
    100. The existing planner score/threshold remains untouched for strategy
    compatibility; this score exists to tell the operator what the map itself
    contains, and is explicitly not a probability.
    """
    primary = plan.get("primary_poi") or {}
    if not isinstance(primary, Mapping):
        primary = {}
    quality_obj = primary.get("setup_quality") or {}
    if not isinstance(quality_obj, Mapping):
        quality_obj = {}
    components = {
        "thesis_dominance": max(0.0, min(100.0, _f(primary.get("thesis_dominance_score")))),
        "return_probability": max(0.0, min(100.0, _f(primary.get("return_probability_score")))),
        "setup_quality": max(0.0, min(100.0, _f(primary.get("quality_score"), _f(quality_obj.get("score"))))),
        "trigger_quality": max(0.0, min(100.0, _f(primary.get("trigger_score")))),
    }
    cfg = ((config.get("session_planner") or {}).get("map_display_quality") or {}) if isinstance(config, Mapping) else {}
    weights = cfg.get("weights") or {
        "thesis_dominance": 0.30,
        "return_probability": 0.25,
        "setup_quality": 0.25,
        "trigger_quality": 0.20,
    }
    clean_weights = {key: max(0.0, _f(weights.get(key))) for key in components}
    total = sum(clean_weights.values()) or 1.0
    clean_weights = {key: value / total for key, value in clean_weights.items()}
    raw = sum(components[key] * clean_weights[key] for key in components)
    ceiling = max(1.0, min(99.0, _f(cfg.get("ceiling"), 95.0)))
    score = min(ceiling, raw)
    return {
        "score": round(score, 1),
        "grade": _grade(score),
        "ceiling": ceiling,
        "not_probability": True,
        "components": {key: round(value, 1) for key, value in components.items()},
        "weights": {key: round(value, 4) for key, value in clean_weights.items()},
        "legacy_planner_score": round(_f(plan.get("planner_confidence")), 1),
    }
