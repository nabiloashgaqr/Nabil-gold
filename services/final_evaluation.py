"""Final evaluation pass.

Combines the benchmark backtest layer with analyst-overlap insights into one
practical decision memo so the next optimization round is guided by evidence.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from services.analyst_distillation import AnalystDistillationService
from services.day_map_metrics import summarize_day_map_execution
from services.backtesting import benchmark_backtests


class FinalEvaluationService:
    def __init__(self, config: Dict[str, Any], database: Any | None = None) -> None:
        self.config = config or {}
        self.database = database
        cfg = self.config.get("final_evaluation", {}) or {}
        self.analyst_compare_limit = int(cfg.get("analyst_compare_limit", 30) or 30)
        self.min_match_rate_good = float(cfg.get("min_match_rate_good", 55) or 55)
        self.max_entry_distance_good = float(cfg.get("max_entry_distance_good", 60) or 60)
        self.max_not_filled_ratio = float(cfg.get("max_not_filled_ratio", 0.40) or 0.40)

    def run(
        self,
        candles: List[Dict[str, Any]],
        *,
        window: int = 160,
        step: int = 12,
        horizon: int = 32,
        max_trades: int = 60,
    ) -> Dict[str, Any]:
        benchmark = benchmark_backtests(
            self.config,
            candles,
            window=window,
            step=step,
            horizon=horizon,
            max_trades=max_trades,
        )
        analyst_overlap: Dict[str, Any] = {}
        day_map_execution: Dict[str, Any] = {}
        if self.database is not None:
            try:
                distill = AnalystDistillationService(self.database, self.config)
                if distill.enabled:
                    analyst_overlap = distill.compare_recent(
                        symbol=self.config.get("symbol", "XAU/USD"),
                        limit=self.analyst_compare_limit,
                    )
            except Exception as exc:  # noqa: BLE001
                analyst_overlap = {"error": str(exc), "labels_considered": 0}
            try:
                recent = self.database.get_recent_trades(limit=max(self.analyst_compare_limit * 2, 80))
                day_map_execution = summarize_day_map_execution(list(recent or []))
            except Exception as exc:  # noqa: BLE001
                day_map_execution = {"error": str(exc), "tracked_trade_count": 0}
        scorecard = self._scorecard(benchmark, analyst_overlap, day_map_execution)
        recommendations = self._recommendations(benchmark, analyst_overlap, day_map_execution, scorecard)
        verdict = self._verdict(scorecard)
        return {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "symbol": self.config.get("symbol", "XAU/USD"),
            "benchmark": benchmark,
            "analyst_overlap": analyst_overlap,
            "day_map_execution": day_map_execution,
            "scorecard": scorecard,
            "verdict": verdict,
            "recommendations": recommendations,
        }

    def save(self, report: Dict[str, Any], path: str | Path = "storage/final_evaluation.json") -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def format_telegram(self, report: Dict[str, Any]) -> str:
        benchmark = report.get("benchmark", {}) or {}
        cmp = benchmark.get("comparison", {}) or {}
        current = ((benchmark.get("variants", {}) or {}).get("current_engine", {}) or {}).get("summary", {})
        overlap = report.get("analyst_overlap", {}) or {}
        scorecard = report.get("scorecard", {}) or {}
        day_map = report.get("day_map_execution", {}) or {}
        recommendations = report.get("recommendations", []) or []
        lines = [
            "📋 <b>Final Evaluation Pass — SmartSignal</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Verdict: <b>{report.get('verdict', 'REVIEW')}</b>",
            f"Benchmark Δ Net: {float(cmp.get('net_points_delta', 0)):+.0f} pts | Δ WR {float(cmp.get('win_rate_delta', 0)):+.2f}% | Δ PF {float(cmp.get('profit_factor_delta', 0)):+.2f}",
            f"Current Engine: {int(current.get('total_trades', 0) or 0)} filled / {int(current.get('not_filled', 0) or 0)} not-filled",
        ]
        if overlap.get("labels_considered"):
            lines.append(
                f"Analyst Overlap: Match {float(overlap.get('match_rate_pct', 0)):.1f}% | "
                f"Coverage {float(overlap.get('coverage_rate_pct', 0)):.1f}% | "
                f"Avg entry distance {overlap.get('avg_entry_distance_points', '--')} pts"
            )
        else:
            lines.append("Analyst Overlap: N/A (no analyst labels in evaluation window)")
        metrics = day_map.get("scenario_metrics") or {}
        if day_map.get("tracked_trade_count"):
            lines.append(
                f"Day-Map: main worked {int(metrics.get('main_worked_count', 0) or 0)} | add needed {int(metrics.get('add_needed_count', 0) or 0)} | starter alone {int(metrics.get('starter_survived_alone_count', 0) or 0)} | failed {int(metrics.get('day_map_failed_count', 0) or 0)}"
            )
        lines.append(
            f"Scorecard: benchmark={scorecard.get('benchmark_score', 0)}/100 | overlap={scorecard.get('overlap_score', 0)}/100 | execution={scorecard.get('execution_score', 0)}/100 | governance={scorecard.get('governance_score', 0)}/100 | planner={scorecard.get('planner_score', 0)}/100 | daymap={scorecard.get('day_map_execution_score', 0)}/100"
        )
        if recommendations:
            lines.append("Recommendations:")
            for rec in recommendations[:4]:
                lines.append(f"• {rec}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def _scorecard(self, benchmark: Dict[str, Any], overlap: Dict[str, Any], day_map_execution: Dict[str, Any]) -> Dict[str, Any]:
        cmp = benchmark.get("comparison", {}) or {}
        current = ((benchmark.get("variants", {}) or {}).get("current_engine", {}) or {}).get("summary", {})
        benchmark_score = 50.0
        benchmark_score += max(-20.0, min(20.0, float(cmp.get("net_points_delta", 0)) / 50.0))
        benchmark_score += max(-15.0, min(15.0, float(cmp.get("win_rate_delta", 0)) * 1.2))
        benchmark_score += max(-10.0, min(10.0, float(cmp.get("profit_factor_delta", 0)) * 5.0))
        benchmark_score = max(0.0, min(100.0, benchmark_score))

        labels = int(overlap.get("labels_considered", 0) or 0)
        if labels > 0:
            overlap_score = (
                float(overlap.get("match_rate_pct", 0) or 0) * 0.55
                + float(overlap.get("coverage_rate_pct", 0) or 0) * 0.35
                + max(0.0, 10.0 - min(float(overlap.get("avg_entry_distance_points") or 999), 100.0) / 10.0)
            )
            overlap_score = max(0.0, min(100.0, overlap_score))
            overlap_available = True
        else:
            overlap_score = 50.0
            overlap_available = False

        total_candidates = max(1, int(current.get("total_candidates", 0) or 0))
        not_filled = int(current.get("not_filled", 0) or 0)
        not_filled_ratio = not_filled / total_candidates
        execution_score = max(0.0, min(100.0, 100.0 - (not_filled_ratio * 100.0)))

        governance = current.get("pending_governance", {}) or {}
        primary_fill = float(current.get("primary_fill_rate_pct", 0) or 0)
        avg_dom = float(current.get("avg_thesis_dominance_score", 0) or 0)
        if governance or primary_fill > 0 or avg_dom > 0:
            governance_score = max(0.0, min(100.0, primary_fill * 0.6 + avg_dom * 0.4))
            governance_available = True
        else:
            governance_score = 50.0
            governance_available = False

        planning = current.get("planning", {}) or {}
        plan_ready_rate = float(current.get("plan_ready_rate_pct", 0) or planning.get("plan_ready_rate_pct", 0) or 0)
        standby_ready_rate = float(current.get("standby_ready_rate_pct", 0) or planning.get("standby_ready_rate_pct", 0) or 0)
        primary_overlap = float((((overlap.get("selection_role_insights") or {}).get("PRIMARY") or {}).get("coverage_rate_pct", 0)) or 0)
        if planning or plan_ready_rate > 0 or standby_ready_rate > 0:
            planner_score = max(0.0, min(100.0, plan_ready_rate * 0.55 + standby_ready_rate * 0.20 + primary_overlap * 0.25))
            planner_available = True
        else:
            planner_score = 50.0
            planner_available = False

        day_map_metrics = day_map_execution.get("scenario_metrics", {}) if isinstance(day_map_execution, dict) else {}
        tracked = int((day_map_execution or {}).get("tracked_trade_count", 0) or 0)
        if tracked > 0:
            main_worked = float(day_map_metrics.get("main_worked_count", 0) or 0)
            add_needed = float(day_map_metrics.get("add_needed_count", 0) or 0)
            starter_alone = float(day_map_metrics.get("starter_survived_alone_count", 0) or 0)
            failed = float(day_map_metrics.get("day_map_failed_count", 0) or 0)
            scenario_count = float((day_map_execution or {}).get("scenario_count", 0) or tracked or 1)
            day_map_execution_score = max(0.0, min(100.0, (main_worked / max(scenario_count, 1)) * 55.0 + (starter_alone / max(scenario_count, 1)) * 20.0 + max(0.0, 25.0 - (failed / max(scenario_count, 1)) * 40.0)))
            day_map_execution_available = True
        else:
            day_map_execution_score = 50.0
            day_map_execution_available = False

        return {
            "benchmark_score": round(benchmark_score, 1),
            "overlap_score": round(overlap_score, 1),
            "overlap_available": overlap_available,
            "execution_score": round(execution_score, 1),
            "governance_score": round(governance_score, 1),
            "governance_available": governance_available,
            "planner_score": round(planner_score, 1),
            "planner_available": planner_available,
            "day_map_execution_score": round(day_map_execution_score, 1),
            "day_map_execution_available": day_map_execution_available,
            "plan_ready_rate_pct": round(plan_ready_rate, 2),
            "standby_ready_rate_pct": round(standby_ready_rate, 2),
            "not_filled_ratio": round(not_filled_ratio, 3),
            "primary_fill_rate_pct": round(primary_fill, 2),
        }

    def _verdict(self, scorecard: Dict[str, Any]) -> str:
        benchmark_score = float(scorecard.get("benchmark_score", 0) or 0)
        overlap_score = float(scorecard.get("overlap_score", 0) or 0)
        execution_score = float(scorecard.get("execution_score", 0) or 0)
        governance_score = float(scorecard.get("governance_score", 0) or 0)
        overlap_available = bool(scorecard.get("overlap_available", False))
        governance_available = bool(scorecard.get("governance_available", False))
        if overlap_available and governance_available:
            total = benchmark_score * 0.40 + overlap_score * 0.25 + execution_score * 0.15 + governance_score * 0.20
        elif overlap_available and not governance_available:
            total = benchmark_score * 0.50 + overlap_score * 0.30 + execution_score * 0.20
        elif governance_available:
            total = benchmark_score * 0.55 + execution_score * 0.25 + governance_score * 0.20
        else:
            total = benchmark_score * 0.70 + execution_score * 0.30
        if total >= 75:
            return "READY_FOR_STRUCTURED_TRIAL"
        if total >= 60:
            return "PROMISING_BUT_NEEDS_TUNING"
        return "REQUIRES_MORE_REFINEMENT"

    def _recommendations(self, benchmark: Dict[str, Any], overlap: Dict[str, Any], day_map_execution: Dict[str, Any], scorecard: Dict[str, Any]) -> List[str]:
        cmp = benchmark.get("comparison", {}) or {}
        current = ((benchmark.get("variants", {}) or {}).get("current_engine", {}) or {}).get("summary", {})
        recommendations: List[str] = []

        if float(cmp.get("net_points_delta", 0) or 0) <= 0:
            recommendations.append("Current engine is not outperforming the baseline enough; review trigger thresholds and profile strictness.")
        if float(cmp.get("win_rate_delta", 0) or 0) < 0:
            recommendations.append("Win rate fell versus baseline; inspect whether trigger gating is filtering too late or too aggressively.")
        if float(scorecard.get("not_filled_ratio", 0) or 0) > self.max_not_filled_ratio:
            recommendations.append("Too many pending setups are not filling; consider loosening market-threshold or POI distance rules.")
        if float(scorecard.get("governance_score", 0) or 0) < 55:
            recommendations.append("Pending governance quality is weak; review PRIMARY/STANDBY selection and thesis dominance thresholds.")
        day_map_metrics = (day_map_execution.get("scenario_metrics") or {}) if isinstance(day_map_execution, dict) else {}
        if float(scorecard.get("day_map_execution_score", 0) or 0) < 55 and int((day_map_execution or {}).get("tracked_trade_count", 0) or 0) > 0:
            recommendations.append("Day-map execution quality is weak; review whether main-area ideas are failing too often or whether add legs are being forced unnecessarily.")
        if int(day_map_metrics.get("day_map_failed_count", 0) or 0) > int(day_map_metrics.get("main_worked_count", 0) or 0):
            recommendations.append("Day-map failures outnumber main-area successes; tighten invalidation logic and avoid forcing the first mapped zone.")

        labels = int(overlap.get("labels_considered", 0) or 0)
        if labels > 0:
            avg_entry = overlap.get("avg_entry_distance_points")
            if avg_entry is not None and float(avg_entry) > self.max_entry_distance_good:
                recommendations.append("Average entry distance from analyst labels is high; tighten entry timing and rejection confirmation around POI.")
            if float(overlap.get("match_rate_pct", 0) or 0) < self.min_match_rate_good:
                recommendations.append("Analyst match-rate is still low; revisit POI ranking and setup-type mapping against analyst labels.")
            reasons = overlap.get("top_missed_reasons") or []
            if reasons:
                top = str(reasons[0].get("reason_code") or "")
                if top == "MISSED_ENTRY_TOO_FAR":
                    recommendations.append("Top miss reason is entry lag: tune hybrid MARKET/LIMIT transition and near-POI execution logic.")
                elif top == "MISSED_TIMING_WINDOW":
                    recommendations.append("Top miss reason is timing window: refine session-aware trigger logic and setup expiry handling.")
                elif top == "MISSED_POI_MISMATCH":
                    recommendations.append("Top miss reason is POI mismatch: refine order-block/FVG ranking weights and mitigation penalties.")
            primary_role = ((overlap.get("selection_role_insights") or {}).get("PRIMARY") or {})
            if primary_role and float(primary_role.get("coverage_rate_pct", 0) or 0) < 50:
                recommendations.append("Primary planner zones still miss many analyst ideas; improve morning PRIMARY zone selection before relying on standby ladders.")
            if bool(scorecard.get("planner_available", False)) and float(scorecard.get("planner_score", 0) or 0) < 55:
                recommendations.append("Session planner quality is weak; improve morning plan readiness, standby coverage, and early-session scenario mapping.")
        else:
            if bool(scorecard.get("planner_available", False)) and float(scorecard.get("planner_score", 0) or 0) < 55:
                recommendations.append("Session planner quality is weak; improve morning plan readiness, standby coverage, and early-session scenario mapping.")
            recommendations.append("Analyst overlap is unavailable in this window; rely on benchmark + execution for now, and add labels later if you want bot-vs-analyst comparison.")

        if not recommendations:
            recommendations.append("Current engine is outperforming baseline with acceptable overlap; move to a structured forward trial and monitor drift.")
        return recommendations[:6]
