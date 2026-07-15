"""Final tuning / hardening advisor.

Reads the output of the final evaluation pass and turns it into:
1) a practical decision memo
2) a conservative config patch suggestion

The goal is not to auto-rewrite the whole config, but to surface a small,
reviewable set of next-step adjustments based on evidence.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


class TuningAdvisor:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = copy.deepcopy(config or {})

    def build_advice(self, final_report: Dict[str, Any]) -> Dict[str, Any]:
        benchmark = final_report.get("benchmark", {}) or {}
        cmp = benchmark.get("comparison", {}) or {}
        overlap = final_report.get("analyst_overlap", {}) or {}
        scorecard = final_report.get("scorecard", {}) or {}
        current = ((benchmark.get("variants", {}) or {}).get("current_engine", {}) or {}).get("summary", {})

        config_patch: Dict[str, Any] = {}
        actions: List[Dict[str, Any]] = []
        recommendations: List[str] = []

        not_filled_ratio = float(scorecard.get("not_filled_ratio", 0) or 0)
        top_reason = str(((overlap.get("top_missed_reasons") or [{}])[0]).get("reason_code") or "")
        match_rate = float(overlap.get("match_rate_pct", 0) or 0)
        coverage_rate = float(overlap.get("coverage_rate_pct", 0) or 0)
        win_rate_delta = float(cmp.get("win_rate_delta", 0) or 0)
        net_points_delta = float(cmp.get("net_points_delta", 0) or 0)

        # ── Execution / fill-rate tuning ──────────────────────────────────
        if not_filled_ratio > 0.40 or top_reason == "MISSED_ENTRY_TOO_FAR":
            current_threshold = int(((self.config.get("order_execution") or {}).get("market_threshold_points", 30)) or 30)
            new_threshold = min(60, current_threshold + 10)
            self._set(config_patch, ["order_execution", "market_threshold_points"], new_threshold)
            actions.append(
                {
                    "type": "execution",
                    "key": "order_execution.market_threshold_points",
                    "from": current_threshold,
                    "to": new_threshold,
                    "reason": "Too many valid setups are not filling or analyst overlap shows entry lag.",
                }
            )
            recommendations.append(
                f"Increase market-threshold from {current_threshold} to {new_threshold} points to reduce entry lag / not-filled setups."
            )

        # ── Setup timing / expiry tuning ──────────────────────────────────
        if top_reason == "MISSED_TIMING_WINDOW":
            current_expire = int((((self.config.get("setup_memory") or {}).get("expire_after_hours", 12)) or 12))
            new_expire = min(24, current_expire + 4)
            self._set(config_patch, ["setup_memory", "expire_after_hours"], new_expire)
            actions.append(
                {
                    "type": "timing",
                    "key": "setup_memory.expire_after_hours",
                    "from": current_expire,
                    "to": new_expire,
                    "reason": "Analyst overlap suggests the engine is expiring valid setups too early.",
                }
            )
            recommendations.append(
                f"Extend setup expiry from {current_expire}h to {new_expire}h to capture later valid mitigations."
            )

        # ── POI ranking tuning ────────────────────────────────────────────
        if top_reason in {"MISSED_POI_MISMATCH", "PARTIAL_POI_MISMATCH"}:
            current_bonus = int((((self.config.get("smc_engine") or {}).get("poi_preference", {}) or {}).get("order_block_bonus", 10)) or 10)
            new_bonus = min(18, current_bonus + 2)
            self._set(config_patch, ["smc_engine", "poi_preference", "order_block_bonus"], new_bonus)
            actions.append(
                {
                    "type": "poi_ranking",
                    "key": "smc_engine.poi_preference.order_block_bonus",
                    "from": current_bonus,
                    "to": new_bonus,
                    "reason": "Analyst overlap suggests POI ranking should favour order blocks more strongly.",
                }
            )
            recommendations.append(
                f"Increase order-block ranking bonus from {current_bonus} to {new_bonus} to align POI selection better with analyst labels."
            )

        # ── Trigger strictness tuning ─────────────────────────────────────
        if match_rate >= 55 and win_rate_delta < 0:
            current_trigger = int((((self.config.get("strategy_profiles") or {}).get("liquidity_reversal", {}) or {}).get("min_trigger_score", 70)) or 70)
            new_trigger = min(85, current_trigger + 5)
            self._set(config_patch, ["strategy_profiles", "liquidity_reversal", "min_trigger_score"], new_trigger)
            actions.append(
                {
                    "type": "trigger_strictness",
                    "key": "strategy_profiles.liquidity_reversal.min_trigger_score",
                    "from": current_trigger,
                    "to": new_trigger,
                    "reason": "Overlap is acceptable but benchmark quality lags; require stronger confirmation before reversal entries.",
                }
            )
            recommendations.append(
                f"Raise liquidity-reversal trigger minimum from {current_trigger} to {new_trigger} to improve quality over quantity."
            )
        elif match_rate < 45 and coverage_rate < 60 and not_filled_ratio <= 0.40:
            current_trigger = int((((self.config.get("strategy_profiles") or {}).get("liquidity_reversal", {}) or {}).get("min_trigger_score", 70)) or 70)
            new_trigger = max(60, current_trigger - 5)
            if new_trigger != current_trigger:
                self._set(config_patch, ["strategy_profiles", "liquidity_reversal", "min_trigger_score"], new_trigger)
                actions.append(
                    {
                        "type": "trigger_relaxation",
                        "key": "strategy_profiles.liquidity_reversal.min_trigger_score",
                        "from": current_trigger,
                        "to": new_trigger,
                        "reason": "Overlap is too low and entry distance is not the main problem; allow more reversal confirmations through.",
                    }
                )
                recommendations.append(
                    f"Lower liquidity-reversal trigger minimum from {current_trigger} to {new_trigger} to improve overlap coverage." 
                )

        # ── Contextual learning blend tuning ──────────────────────────────
        if net_points_delta < 0 and float(scorecard.get("benchmark_score", 0) or 0) < 50:
            current_blend = float((((self.config.get("learning") or {}).get("contextual_blend", 0.35)) or 0.35))
            new_blend = max(0.20, round(current_blend - 0.05, 2))
            self._set(config_patch, ["learning", "contextual_blend"], new_blend)
            actions.append(
                {
                    "type": "learning",
                    "key": "learning.contextual_blend",
                    "from": current_blend,
                    "to": new_blend,
                    "reason": "Contextual learning influence may be too strong relative to the benchmark baseline.",
                }
            )
            recommendations.append(
                f"Reduce contextual learning blend from {current_blend:.2f} to {new_blend:.2f} until benchmark outperformance is stable." 
            )

        if not recommendations:
            recommendations.append("No urgent tuning changes detected. Keep current configuration and monitor the next structured trial.")

        return {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "symbol": self.config.get("symbol", "XAU/USD"),
            "verdict": final_report.get("verdict", "REVIEW"),
            "scorecard": scorecard,
            "recommendations": recommendations,
            "actions": actions,
            "config_patch": config_patch,
        }

    def save(self, advice: Dict[str, Any], path: str | Path = "storage/tuning_advice.json") -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(advice, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    @staticmethod
    def _set(root: Dict[str, Any], path: List[str], value: Any) -> None:
        node = root
        for key in path[:-1]:
            child = node.get(key)
            if not isinstance(child, dict):
                child = {}
                node[key] = child
            node = child
        node[path[-1]] = value
