"""Analyst distillation foundation.

This service stores discretionary analyst labels and compares them against the
bot's structured setup candidates. It is the first step toward measuring how
closely the system sees what a strong manual analyst sees.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from utils.instruments import price_to_points


class AnalystDistillationService:
    def __init__(self, database, config: Dict[str, Any] | None = None) -> None:
        self.db = database
        self.config = config or {}
        cfg = self.config.get("analyst_distillation", {}) or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.entry_tolerance_points = float(cfg.get("entry_tolerance_points", 80) or 80)
        self.time_window_hours = float(cfg.get("time_window_hours", 12) or 12)
        self.match_threshold = float(cfg.get("match_threshold", 65) or 65)
        self.symbol = str(self.config.get("symbol", "XAU/USD"))

    def save_label(self, label: Dict[str, Any]) -> str:
        return self.db.save_analyst_label(label)

    def compare_recent(self, symbol: str | None = None, limit: int = 20) -> Dict[str, Any]:
        symbol = str(symbol or self.symbol)
        labels = self.db.get_analyst_labels(limit=limit, symbol=symbol)
        setups = self.db.get_recent_setup_candidates(limit=100, symbol=symbol)
        comparisons = []
        matched = 0
        missed = 0
        for label in labels:
            comparison = self.best_match_for_label(label, setups)
            comparisons.append(comparison)
            if comparison.get("classification") == "MATCHED":
                matched += 1
                try:
                    self.db.save_analyst_comparison(comparison)
                except Exception:
                    pass
            else:
                missed += 1
        return {
            "symbol": symbol,
            "labels_considered": len(labels),
            "matched_labels": matched,
            "missed_labels": missed,
            "match_rate_pct": round((matched / len(labels) * 100), 1) if labels else 0.0,
            "comparisons": comparisons,
        }

    def best_match_for_label(self, label: Dict[str, Any], setup_candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        best = None
        best_score = -1.0
        for setup in setup_candidates:
            score_payload = self._score_label_vs_setup(label, setup)
            if score_payload["score"] > best_score:
                best_score = score_payload["score"]
                best = score_payload
        if best is None:
            return {
                "id": f"COMPARE_EMPTY_{label.get('id', 'unknown')}",
                "analyst_label_id": label.get("id"),
                "setup_candidate_id": None,
                "symbol": label.get("symbol") or self.symbol,
                "match_score": 0.0,
                "classification": "MISSED",
                "summary": "No recent bot setup candidates found for this label.",
                "payload": {"label": label},
            }
        classification = "MATCHED" if best["score"] >= self.match_threshold else "MISSED"
        return {
            "id": f"COMPARE_{label.get('id', 'unknown')}_{best.get('setup_id', 'none')}",
            "analyst_label_id": label.get("id"),
            "setup_candidate_id": best.get("setup_id"),
            "symbol": label.get("symbol") or self.symbol,
            "match_score": round(best["score"], 1),
            "classification": classification,
            "summary": best.get("summary"),
            "payload": best,
        }

    def _score_label_vs_setup(self, label: Dict[str, Any], setup: Dict[str, Any]) -> Dict[str, Any]:
        label_direction = str(label.get("bias") or label.get("direction") or "WAIT").upper()
        setup_direction = str(setup.get("direction") or "WAIT").upper()
        score = 0.0
        reasons: List[str] = []

        direction_match = label_direction == setup_direction and label_direction in {"BUY", "SELL"}
        if direction_match:
            score += 35.0
            reasons.append("direction match")

        label_setup = str(label.get("setup_type") or "").upper()
        setup_type = str(setup.get("setup_type") or "").upper()
        if label_setup and setup_type and label_setup == setup_type:
            score += 20.0
            reasons.append("setup type match")

        label_poi = str(label.get("poi_type") or "").lower()
        setup_poi = str(setup.get("poi_type") or "").lower()
        if label_poi and setup_poi and label_poi == setup_poi:
            score += 10.0
            reasons.append("poi type match")

        label_sweep = str(label.get("sweep_side") or "").lower()
        setup_sweep = str(setup.get("sweep_side") or "").lower()
        if label_sweep and setup_sweep and label_sweep == setup_sweep:
            score += 10.0
            reasons.append("sweep side match")

        entry_distance_points = None
        intended_entry = self._f(label.get("intended_entry"))
        setup_entry = self._f(setup.get("entry_price"))
        if intended_entry > 0 and setup_entry > 0:
            entry_distance_points = abs(price_to_points(intended_entry - setup_entry, symbol=str(label.get("symbol") or self.symbol)))
            entry_component = max(0.0, 25.0 * (1.0 - min(entry_distance_points, self.entry_tolerance_points) / max(self.entry_tolerance_points, 1.0)))
            score += entry_component
            if entry_component > 0:
                reasons.append(f"entry proximity {entry_distance_points:.0f} pts")

        in_zone = False
        poi_low = self._f(setup.get("poi_low"))
        poi_high = self._f(setup.get("poi_high"))
        if intended_entry > 0 and poi_low > 0 and poi_high > 0:
            low = min(poi_low, poi_high)
            high = max(poi_low, poi_high)
            in_zone = low <= intended_entry <= high
            if in_zone:
                score += 8.0
                reasons.append("entry inside bot POI")

        time_alignment = self._within_time_window(label.get("created_at"), setup.get("created_at") or setup.get("first_seen_at"))
        if time_alignment:
            score += 7.0
            reasons.append("time-window aligned")

        summary = ", ".join(reasons) if reasons else "Weak or no overlap"
        return {
            "label_id": label.get("id"),
            "setup_id": setup.get("id"),
            "score": score,
            "summary": summary,
            "direction_match": direction_match,
            "setup_type_match": bool(label_setup and setup_type and label_setup == setup_type),
            "poi_type_match": bool(label_poi and setup_poi and label_poi == setup_poi),
            "sweep_side_match": bool(label_sweep and setup_sweep and label_sweep == setup_sweep),
            "entry_distance_points": round(entry_distance_points, 1) if entry_distance_points is not None else None,
            "entry_inside_poi": in_zone,
            "time_aligned": time_alignment,
        }

    def _within_time_window(self, left: Any, right: Any) -> bool:
        dt_left = self._parse_dt(left)
        dt_right = self._parse_dt(right)
        if not dt_left or not dt_right:
            return False
        delta = abs((dt_left - dt_right).total_seconds()) / 3600.0
        return delta <= self.time_window_hours

    def _parse_dt(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            text = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _f(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
