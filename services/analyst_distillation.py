"""Analyst distillation foundation.

This service stores discretionary analyst labels and compares them against the
bot's structured setup candidates. It is the first step toward measuring how
closely the system sees what a strong manual analyst sees.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
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
        self.partial_match_threshold = float(cfg.get("partial_match_threshold", 45) or 45)
        self.symbol = str(self.config.get("symbol", "XAU/USD"))

    def save_label(self, label: Dict[str, Any]) -> str:
        return self.db.save_analyst_label(label)

    def import_labels_from_file(
        self,
        file_path: str | Path,
        *,
        default_symbol: str | None = None,
        analyst_name: str | None = None,
    ) -> Dict[str, Any]:
        """Import analyst labels from JSON or CSV and persist them.

        JSON accepts either a list of labels or an object with a top-level
        ``labels`` list. CSV uses the column names as label keys.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(path)
        suffix = path.suffix.lower()
        rows: List[Dict[str, Any]] = []
        if suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                raw = payload.get("labels") or []
            elif isinstance(payload, list):
                raw = payload
            else:
                raw = []
            rows = [dict(item) for item in raw if isinstance(item, dict)]
        elif suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                rows = [dict(row) for row in reader]
        else:
            raise ValueError(f"Unsupported analyst labels format: {suffix}")

        imported_ids: List[str] = []
        for row in rows:
            normalized = self._normalize_label(row, default_symbol=default_symbol, analyst_name=analyst_name)
            imported_ids.append(self.save_label(normalized))
        return {
            "file": str(path),
            "count": len(imported_ids),
            "ids": imported_ids,
            "symbol": default_symbol or self.symbol,
        }

    def compare_recent(self, symbol: str | None = None, limit: int = 20) -> Dict[str, Any]:
        symbol = str(symbol or self.symbol)
        labels = self.db.get_analyst_labels(limit=limit, symbol=symbol)
        setups = self.db.get_recent_setup_candidates(limit=max(100, limit * 5), symbol=symbol)
        return self.compare_labels_and_setups(labels, setups, symbol=symbol, save=True)

    def compare_labels_and_setups(
        self,
        labels: List[Dict[str, Any]],
        setups: List[Dict[str, Any]],
        *,
        symbol: str | None = None,
        save: bool = True,
    ) -> Dict[str, Any]:
        symbol = str(symbol or self.symbol)
        comparisons: List[Dict[str, Any]] = []
        matched = 0
        partial = 0
        missed = 0
        matched_setup_ids: set[str] = set()
        direction_matches = 0
        setup_type_matches = 0
        poi_matches = 0
        entry_distances: List[float] = []

        for label in labels:
            comparison = self.best_match_for_label(label, setups)
            comparisons.append(comparison)
            classification = str(comparison.get("classification") or "MISSED_BY_BOT")
            payload = comparison.get("payload") or {}
            if classification == "MATCHED":
                matched += 1
            elif classification == "PARTIAL_MATCH":
                partial += 1
            else:
                missed += 1
            if classification in {"MATCHED", "PARTIAL_MATCH"} and comparison.get("setup_candidate_id"):
                matched_setup_ids.add(str(comparison.get("setup_candidate_id")))
            if payload.get("direction_match"):
                direction_matches += 1
            if payload.get("setup_type_match"):
                setup_type_matches += 1
            if payload.get("poi_type_match"):
                poi_matches += 1
            if payload.get("entry_distance_points") is not None:
                entry_distances.append(float(payload.get("entry_distance_points") or 0))
            if save:
                try:
                    self.db.save_analyst_comparison(comparison)
                except Exception:
                    pass

        extra_setups: List[Dict[str, Any]] = []
        for setup in setups:
            sid = str(setup.get("id") or "")
            if not sid or sid in matched_setup_ids:
                continue
            extra = self._extra_setup_record(setup)
            extra_setups.append(extra)
            if save:
                try:
                    self.db.save_analyst_comparison(extra)
                except Exception:
                    pass

        considered = len(labels)
        overlap = matched + partial
        reason_counts = self._reason_breakdown(comparisons + extra_setups)
        setup_insights = self._setup_type_insights(comparisons)
        insight_summary = self._insight_summary(reason_counts)
        return {
            "symbol": symbol,
            "labels_considered": considered,
            "matched_labels": matched,
            "partial_matches": partial,
            "missed_labels": missed,
            "extra_bot_setups": len(extra_setups),
            "match_rate_pct": round((matched / considered * 100), 1) if considered else 0.0,
            "coverage_rate_pct": round((overlap / considered * 100), 1) if considered else 0.0,
            "direction_match_rate_pct": round((direction_matches / considered * 100), 1) if considered else 0.0,
            "setup_type_match_rate_pct": round((setup_type_matches / considered * 100), 1) if considered else 0.0,
            "poi_type_match_rate_pct": round((poi_matches / considered * 100), 1) if considered else 0.0,
            "avg_entry_distance_points": round(sum(entry_distances) / len(entry_distances), 1) if entry_distances else None,
            "insight_breakdown": reason_counts,
            "top_missed_reasons": insight_summary,
            "setup_type_insights": setup_insights,
            "comparisons": comparisons,
            "extra_setup_records": extra_setups,
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
                "classification": "MISSED_BY_BOT",
                "reason_code": "NO_BOT_SETUP_FOUND",
                "summary": "No recent bot setup candidates found for this label.",
                "payload": {"label": label},
            }
        if best["score"] >= self.match_threshold:
            classification = "MATCHED"
        elif best["score"] >= self.partial_match_threshold:
            classification = "PARTIAL_MATCH"
        else:
            classification = "MISSED_BY_BOT"
        reason_code = self._comparison_reason_code(best, classification)
        summary = self._comparison_summary(best, classification, reason_code)
        return {
            "id": f"COMPARE_{label.get('id', 'unknown')}_{best.get('setup_id', 'none')}",
            "analyst_label_id": label.get("id"),
            "setup_candidate_id": best.get("setup_id"),
            "symbol": label.get("symbol") or self.symbol,
            "match_score": round(best["score"], 1),
            "classification": classification,
            "reason_code": reason_code,
            "summary": summary,
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
            "label_setup_type": label_setup,
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

    def _extra_setup_record(self, setup: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": f"EXTRA_{setup.get('id', 'unknown')}",
            "analyst_label_id": None,
            "setup_candidate_id": setup.get("id"),
            "symbol": setup.get("symbol") or self.symbol,
            "match_score": 0.0,
            "classification": "EXTRA_BOT_SETUP",
            "reason_code": "NO_ANALYST_LABEL",
            "summary": "Bot saw a setup without a matching analyst label in the comparison window.",
            "payload": {
                "setup_type": setup.get("setup_type"),
                "direction": setup.get("direction"),
                "lead_agent": setup.get("lead_agent"),
                "entry_price": setup.get("entry_price"),
            },
        }

    def _comparison_reason_code(self, payload: Dict[str, Any], classification: str) -> str:
        if classification == "MATCHED":
            if payload.get("entry_distance_points") is not None and float(payload.get("entry_distance_points") or 0) > self.entry_tolerance_points * 0.5:
                return "MATCHED_WITH_ENTRY_LAG"
            return "FULL_ALIGNMENT"
        if classification == "PARTIAL_MATCH":
            if not payload.get("direction_match"):
                return "PARTIAL_DIRECTION_CONFLICT"
            if not payload.get("setup_type_match"):
                return "PARTIAL_SETUP_TYPE_MISMATCH"
            if not payload.get("poi_type_match"):
                return "PARTIAL_POI_MISMATCH"
            if not payload.get("time_aligned"):
                return "PARTIAL_TIMING_MISMATCH"
            if payload.get("entry_distance_points") is not None and float(payload.get("entry_distance_points") or 0) > self.entry_tolerance_points:
                return "PARTIAL_ENTRY_LAG"
            return "PARTIAL_GENERIC"
        # MISSED_BY_BOT
        if not payload.get("direction_match"):
            return "MISSED_DIRECTION_MISMATCH"
        if not payload.get("setup_type_match"):
            return "MISSED_SETUP_TYPE_MISMATCH"
        if not payload.get("poi_type_match"):
            return "MISSED_POI_MISMATCH"
        if not payload.get("time_aligned"):
            return "MISSED_TIMING_WINDOW"
        if payload.get("entry_distance_points") is not None and float(payload.get("entry_distance_points") or 0) > self.entry_tolerance_points:
            return "MISSED_ENTRY_TOO_FAR"
        return "MISSED_GENERIC"

    def _comparison_summary(self, payload: Dict[str, Any], classification: str, reason_code: str) -> str:
        base = str(payload.get("summary") or "Weak or no overlap")
        if classification == "MATCHED":
            return f"Matched: {base}"
        if classification == "PARTIAL_MATCH":
            return f"Partial match ({reason_code}): {base}"
        return f"Missed by bot ({reason_code}): {base}"

    def _reason_breakdown(self, records: List[Dict[str, Any]]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in records:
            reason = str(record.get("reason_code") or "UNKNOWN_REASON")
            counts[reason] = counts.get(reason, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    def _setup_type_insights(self, comparisons: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        buckets: Dict[str, Dict[str, Any]] = {}
        for item in comparisons:
            payload = item.get("payload") or {}
            setup_type = str(payload.get("label_setup_type") or "UNKNOWN")
            bucket = buckets.setdefault(setup_type, {"count": 0, "matched": 0, "partial": 0, "missed": 0})
            bucket["count"] += 1
            cls = str(item.get("classification") or "MISSED_BY_BOT")
            if cls == "MATCHED":
                bucket["matched"] += 1
            elif cls == "PARTIAL_MATCH":
                bucket["partial"] += 1
            else:
                bucket["missed"] += 1
        for bucket in buckets.values():
            count = max(int(bucket.get("count", 0)), 1)
            bucket["coverage_rate_pct"] = round(((bucket.get("matched", 0) + bucket.get("partial", 0)) / count) * 100, 1)
        return buckets

    def _insight_summary(self, reason_counts: Dict[str, int]) -> List[Dict[str, Any]]:
        return [
            {"reason_code": reason, "count": count}
            for reason, count in list(reason_counts.items())[:5]
        ]

    def build_insight_lines(self, summary: Dict[str, Any]) -> List[str]:
        lines: List[str] = []
        if summary.get("labels_considered"):
            lines.append(
                f"Analyst overlap: {summary.get('matched_labels', 0)} matched, {summary.get('partial_matches', 0)} partial, "
                f"{summary.get('missed_labels', 0)} missed, {summary.get('extra_bot_setups', 0)} extra bot setups"
            )
        if summary.get("avg_entry_distance_points") is not None:
            lines.append(f"Average entry distance: {summary.get('avg_entry_distance_points')} pts")
        reasons = summary.get("top_missed_reasons") or []
        if reasons:
            compact = ", ".join(f"{item.get('reason_code')} ({item.get('count')})" for item in reasons[:3])
            lines.append(f"Top reasons: {compact}")
        return lines[:3]

    def _normalize_label(
        self,
        label: Dict[str, Any],
        *,
        default_symbol: str | None = None,
        analyst_name: str | None = None,
    ) -> Dict[str, Any]:
        payload = dict(label)
        payload["symbol"] = str(payload.get("symbol") or default_symbol or self.symbol)
        payload["timeframe"] = str(payload.get("timeframe") or self.config.get("entry_timeframe") or "15m")
        payload["analyst_name"] = str(payload.get("analyst_name") or analyst_name or "manual")
        payload["bias"] = str(payload.get("bias") or payload.get("direction") or "WAIT").upper()
        if payload.get("created_at") is None:
            payload["created_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        return payload

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
