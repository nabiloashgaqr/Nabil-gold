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
        day_map = final_report.get("day_map_execution", {}) or {}
        day_metrics = day_map.get("scenario_metrics", {}) or {}

        config_patch: Dict[str, Any] = {}
        actions: List[Dict[str, Any]] = []
        recommendations: List[str] = []

        not_filled_ratio = float(scorecard.get("not_filled_ratio", 0) or 0)
        top_reason = str(((overlap.get("top_missed_reasons") or [{}])[0]).get("reason_code") or "")
        match_rate = float(overlap.get("match_rate_pct", 0) or 0)
        coverage_rate = float(overlap.get("coverage_rate_pct", 0) or 0)
        win_rate_delta = float(cmp.get("win_rate_delta", 0) or 0)
        net_points_delta = float(cmp.get("net_points_delta", 0) or 0)
        day_map_score = float(scorecard.get("day_map_execution_score", 0) or 0)
        day_map_tracked = int(day_map.get("tracked_trade_count", 0) or 0)
        main_worked = int(day_metrics.get("main_worked_count", 0) or 0)
        add_needed = int(day_metrics.get("add_needed_count", 0) or 0)
        starter_alone = int(day_metrics.get("starter_survived_alone_count", 0) or 0)
        day_map_failed = int(day_metrics.get("day_map_failed_count", 0) or 0)
        map_changed_cancelled = int(day_metrics.get("map_changed_cancelled_count", 0) or 0)

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

        # ── Day-map hierarchy tuning ─────────────────────────────────────
        if day_map_tracked >= 3 and add_needed > max(1, main_worked):
            current_dom = float((((self.config.get("session_planner") or {}).get("min_primary_dominance", 50)) or 50))
            new_dom = min(70.0, current_dom + 4.0)
            self._set(config_patch, ["session_planner", "min_primary_dominance"], round(new_dom, 1))
            actions.append(
                {
                    "type": "day_map_main_quality",
                    "key": "session_planner.min_primary_dominance",
                    "from": round(current_dom, 1),
                    "to": round(new_dom, 1),
                    "reason": "Add-area activations are too frequent relative to main-area success; make the primary zone earn READY status more convincingly.",
                }
            )
            recommendations.append(
                f"Raise session-planner primary dominance floor from {current_dom:.1f} to {new_dom:.1f} because add areas are being needed too often versus main-area success."
            )

        if day_map_tracked >= 3 and starter_alone >= max(2, add_needed + 1):
            current_starter = float((((self.config.get("split_execution") or {}).get("starter_risk_share", 0.4)) or 0.4))
            current_add_on = float((((self.config.get("split_execution") or {}).get("add_on_risk_share", 0.6)) or 0.6))
            new_starter = min(0.6, round(current_starter + 0.1, 2))
            new_add_on = max(0.4, round(current_add_on - 0.1, 2))
            self._set(config_patch, ["split_execution", "starter_risk_share"], new_starter)
            self._set(config_patch, ["split_execution", "add_on_risk_share"], new_add_on)
            actions.append(
                {
                    "type": "day_map_add_aggressiveness",
                    "key": "split_execution.add_on_risk_share",
                    "from": current_add_on,
                    "to": new_add_on,
                    "reason": "Starter legs are often sufficient on their own; reduce add-on aggressiveness so the system does not over-lean on secondary execution.",
                }
            )
            recommendations.append(
                f"Shift split-execution risk from add-on to starter (starter {current_starter:.2f}→{new_starter:.2f}, add-on {current_add_on:.2f}→{new_add_on:.2f}) because starter legs are surviving without the deeper add too often."
            )

        if day_map_tracked >= 3 and day_map_failed >= max(2, main_worked):
            current_plan_score = float((((self.config.get("session_planner") or {}).get("min_plan_score", 62)) or 62))
            new_plan_score = min(75.0, current_plan_score + 4.0)
            current_trigger = float((((self.config.get("session_planner") or {}).get("min_trigger_score", 40)) or 40))
            new_trigger = min(60.0, current_trigger + 5.0)
            self._set(config_patch, ["session_planner", "min_plan_score"], round(new_plan_score, 1))
            self._set(config_patch, ["session_planner", "min_trigger_score"], round(new_trigger, 1))
            actions.append(
                {
                    "type": "day_map_ready_strictness",
                    "key": "session_planner.min_plan_score",
                    "from": round(current_plan_score, 1),
                    "to": round(new_plan_score, 1),
                    "reason": "Day-map failures are too frequent; demand stronger authority/timing before announcing a ready plan.",
                }
            )
            recommendations.append(
                f"Tighten day-map READY criteria (plan score {current_plan_score:.1f}→{new_plan_score:.1f}, trigger score {current_trigger:.1f}→{new_trigger:.1f}) because mapped executions are failing too often."
            )

        if day_map_tracked >= 3 and map_changed_cancelled >= 2 and day_map_score < 60:
            current_align = int((((self.config.get("session_planner") or {}).get("min_authority_alignment_count", 2)) or 2))
            new_align = min(3, current_align + 1)
            if new_align != current_align:
                self._set(config_patch, ["session_planner", "min_authority_alignment_count"], new_align)
                actions.append(
                    {
                        "type": "day_map_authority",
                        "key": "session_planner.min_authority_alignment_count",
                        "from": current_align,
                        "to": new_align,
                        "reason": "Maps are changing/cancelling too often after publication; require stronger authority alignment before broadcasting the day map.",
                    }
                )
                recommendations.append(
                    f"Increase authority alignment requirement from {current_align} to {new_align} because published maps are being cancelled / rebuilt too often."
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

        operator_memo = self._build_operator_memo(final_report, actions, recommendations)

        return {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "symbol": self.config.get("symbol", "XAU/USD"),
            "verdict": final_report.get("verdict", "REVIEW"),
            "scorecard": scorecard,
            "recommendations": recommendations,
            "actions": actions,
            "config_patch": config_patch,
            "operator_memo": operator_memo,
        }


    def build_live_operator_memo(
        self,
        *,
        day_map_execution: Dict[str, Any],
        analyst_overlap: Dict[str, Any] | None = None,
        not_filled_ratio: float = 0.0,
        recommendations: List[str] | None = None,
    ) -> Dict[str, Any]:
        scorecard = {
            "not_filled_ratio": float(not_filled_ratio or 0.0),
            "day_map_execution_score": self._estimate_day_map_score(day_map_execution),
        }
        final_report = {
            "scorecard": scorecard,
            "analyst_overlap": analyst_overlap or {},
            "day_map_execution": day_map_execution or {},
        }
        return self._build_operator_memo(final_report, [], recommendations or [])

    def _coerce_memo(self, advice_or_memo: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(advice_or_memo, dict):
            return {}
        if isinstance(advice_or_memo.get("operator_memo"), dict):
            return dict(advice_or_memo.get("operator_memo") or {})
        return dict(advice_or_memo)

    def format_management_brief(self, advice_or_memo: Dict[str, Any]) -> str:
        memo = self._coerce_memo(advice_or_memo)
        metrics = memo.get("metrics", {}) or {}
        lines = [
            "📌 <b>Management Brief — SmartSignal</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Priority: <b>{memo.get('priority', 'NORMAL')}</b>",
            f"Headline: {memo.get('headline', 'No headline')}",
        ]
        findings = memo.get("findings", []) or []
        if findings:
            lines.append(f"• Key issue: {findings[0]}")
        next_round = memo.get("next_round_focus", []) or []
        if next_round:
            lines.append(f"• Next move: {next_round[0]}")
        if metrics:
            lines.append(
                f"• Day-map: main {metrics.get('main_worked_count', 0)} | add {metrics.get('add_needed_count', 0)} | failed {metrics.get('day_map_failed_count', 0)}"
            )
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def format_operator_memo(self, advice_or_memo: Dict[str, Any]) -> str:
        memo = self._coerce_memo(advice_or_memo)
        findings = memo.get("findings", []) or []
        next_round = memo.get("next_round_focus", []) or []
        actions = memo.get("suggested_config_changes", []) or []
        metrics = memo.get("metrics", {}) or {}
        lines = [
            "🧭 <b>Operator Memo — SmartSignal</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Priority: <b>{memo.get('priority', 'NORMAL')}</b>",
            f"Headline: {memo.get('headline', 'No operator headline')}",
        ]
        if metrics:
            lines.append(
                f"Map score {metrics.get('day_map_execution_score', '--')} | Main {metrics.get('main_worked_count', 0)} | Add {metrics.get('add_needed_count', 0)} | Starter-alone {metrics.get('starter_survived_alone_count', 0)} | Failed {metrics.get('day_map_failed_count', 0)}"
            )
        if findings:
            lines.append("Findings:")
            for item in findings[:4]:
                lines.append(f"• {item}")
        if next_round:
            lines.append("Next round focus:")
            for item in next_round[:4]:
                lines.append(f"• {item}")
        if actions:
            lines.append("Suggested config changes:")
            for item in actions[:4]:
                lines.append(f"• {item}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def _estimate_day_map_score(self, day_map_execution: Dict[str, Any]) -> float:
        metrics = (day_map_execution.get("scenario_metrics") or {}) if isinstance(day_map_execution, dict) else {}
        tracked = int((day_map_execution or {}).get("tracked_trade_count", 0) or 0)
        if tracked <= 0:
            return 50.0
        main_worked = float(metrics.get("main_worked_count", 0) or 0)
        starter_alone = float(metrics.get("starter_survived_alone_count", 0) or 0)
        failed = float(metrics.get("day_map_failed_count", 0) or 0)
        scenario_count = float((day_map_execution or {}).get("scenario_count", 0) or tracked or 1)
        return round(max(0.0, min(100.0, (main_worked / max(scenario_count, 1)) * 55.0 + (starter_alone / max(scenario_count, 1)) * 20.0 + max(0.0, 25.0 - (failed / max(scenario_count, 1)) * 40.0))), 1)

    def _build_operator_memo(self, final_report: Dict[str, Any], actions: List[Dict[str, Any]], recommendations: List[str]) -> Dict[str, Any]:
        scorecard = final_report.get("scorecard", {}) or {}
        overlap = final_report.get("analyst_overlap", {}) or {}
        day_map = final_report.get("day_map_execution", {}) or {}
        metrics = day_map.get("scenario_metrics", {}) or {}
        not_filled_ratio = float(scorecard.get("not_filled_ratio", 0) or 0)
        day_map_score = float(scorecard.get("day_map_execution_score", 0) or 0)
        match_rate = float(overlap.get("match_rate_pct", 0) or 0)
        main_worked = int(metrics.get("main_worked_count", 0) or 0)
        add_needed = int(metrics.get("add_needed_count", 0) or 0)
        starter_alone = int(metrics.get("starter_survived_alone_count", 0) or 0)
        failed = int(metrics.get("day_map_failed_count", 0) or 0)
        map_changed = int(metrics.get("map_changed_cancelled_count", 0) or 0)

        findings: List[str] = []
        next_round_focus: List[str] = []
        if add_needed > max(1, main_worked):
            findings.append("Main mapped area is underperforming relative to the add area; the first zone is not doing enough heavy lifting.")
            next_round_focus.append("Tighten main-area quality so the day map does not rely on the backup leg too often.")
        if starter_alone >= max(2, add_needed + 1):
            findings.append("Starter legs are surviving on their own too often; add-on execution may be too aggressive for current conditions.")
            next_round_focus.append("Reduce add-on aggressiveness and let the starter prove whether deeper execution is really needed.")
        if failed >= max(2, main_worked):
            findings.append("Day-map failures are too frequent versus main-area success; the system is still declaring READY too easily.")
            next_round_focus.append("Demand stronger authority / trigger quality before publishing a ready session map.")
        if map_changed >= 2:
            findings.append("Published maps are being cancelled / rebuilt too often after release; authority is still not stable enough.")
            next_round_focus.append("Raise authority discipline so the map changes less after publication.")
        if not_filled_ratio > 0.40:
            findings.append("Entry lag is still present; too many valid ideas are not being filled in time.")
            next_round_focus.append("Review market/pending transition so the system reaches the move earlier without chasing noise.")
        if match_rate and match_rate < 55:
            findings.append("Analyst overlap is still below target; the bot is not matching the discretionary map closely enough yet.")
            next_round_focus.append("Keep comparing against manual analyst labels until the mapped areas line up more consistently.")
        if not findings:
            findings.append("No urgent operator pain-point stands out from the latest loop; continue controlled monitoring.")
            next_round_focus.append("Keep current map discipline and verify the next sample before changing thresholds again.")

        suggested_config_changes = [
            f"{a.get('key')}: {a.get('from')} → {a.get('to')}" for a in actions[:5] if a.get('key')
        ]

        if day_map_score < 45 or failed >= max(2, main_worked):
            priority = "HIGH"
        elif add_needed > max(1, main_worked) or starter_alone >= max(2, add_needed + 1) or not_filled_ratio > 0.40:
            priority = "MEDIUM"
        else:
            priority = "NORMAL"

        headline_parts: List[str] = []
        if add_needed > max(1, main_worked):
            headline_parts.append("main area weak")
        if starter_alone >= max(2, add_needed + 1):
            headline_parts.append("add-on too aggressive")
        if map_changed >= 2:
            headline_parts.append("authority not stable enough")
        if failed >= max(2, main_worked):
            headline_parts.append("ready-state too loose")
        headline = "; ".join(headline_parts) if headline_parts else "day-map loop stable enough for controlled monitoring"

        return {
            "headline": headline,
            "priority": priority,
            "findings": findings[:5],
            "next_round_focus": next_round_focus[:5],
            "suggested_config_changes": suggested_config_changes,
            "metrics": {
                "day_map_execution_score": round(day_map_score, 1),
                "main_worked_count": main_worked,
                "add_needed_count": add_needed,
                "starter_survived_alone_count": starter_alone,
                "day_map_failed_count": failed,
                "map_changed_cancelled_count": map_changed,
                "not_filled_ratio": round(not_filled_ratio, 3),
                "match_rate_pct": round(match_rate, 1),
            },
            "recommendation_excerpt": recommendations[:4],
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
