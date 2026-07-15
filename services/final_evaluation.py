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
        scorecard = self._scorecard(benchmark, analyst_overlap)
        recommendations = self._recommendations(benchmark, analyst_overlap, scorecard)
        verdict = self._verdict(scorecard)
        return {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "symbol": self.config.get("symbol", "XAU/USD"),
            "benchmark": benchmark,
            "analyst_overlap": analyst_overlap,
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
        lines.append(
            f"Scorecard: benchmark={scorecard.get('benchmark_score', 0)}/100 | overlap={scorecard.get('overlap_score', 0)}/100 | execution={scorecard.get('execution_score', 0)}/100"
        )
        if recommendations:
            lines.append("Recommendations:")
            for rec in recommendations[:4]:
                lines.append(f"• {rec}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def _scorecard(self, benchmark: Dict[str, Any], overlap: Dict[str, Any]) -> Dict[str, Any]:
        cmp = benchmark.get("comparison", {}) or {}
        current = ((benchmark.get("variants", {}) or {}).get("current_engine", {}) or {}).get("summary", {})
        benchmark_score = 50.0
        benchmark_score += max(-20.0, min(20.0, float(cmp.get("net_points_delta", 0)) / 50.0))
        benchmark_score += max(-15.0, min(15.0, float(cmp.get("win_rate_delta", 0)) * 1.2))
        benchmark_score += max(-10.0, min(10.0, float(cmp.get("profit_factor_delta", 0)) * 5.0))
        benchmark_score = max(0.0, min(100.0, benchmark_score))

        overlap_score = 50.0
        labels = int(overlap.get("labels_considered", 0) or 0)
        if labels > 0:
            overlap_score = (
                float(overlap.get("match_rate_pct", 0) or 0) * 0.55
                + float(overlap.get("coverage_rate_pct", 0) or 0) * 0.35
                + max(0.0, 10.0 - min(float(overlap.get("avg_entry_distance_points") or 999), 100.0) / 10.0)
            )
            overlap_score = max(0.0, min(100.0, overlap_score))
        else:
            overlap_score = 0.0

        total_candidates = max(1, int(current.get("total_candidates", 0) or 0))
        not_filled = int(current.get("not_filled", 0) or 0)
        not_filled_ratio = not_filled / total_candidates
        execution_score = max(0.0, min(100.0, 100.0 - (not_filled_ratio * 100.0)))

        return {
            "benchmark_score": round(benchmark_score, 1),
            "overlap_score": round(overlap_score, 1),
            "execution_score": round(execution_score, 1),
            "not_filled_ratio": round(not_filled_ratio, 3),
        }

    def _verdict(self, scorecard: Dict[str, Any]) -> str:
        benchmark_score = float(scorecard.get("benchmark_score", 0) or 0)
        overlap_score = float(scorecard.get("overlap_score", 0) or 0)
        execution_score = float(scorecard.get("execution_score", 0) or 0)
        total = benchmark_score * 0.45 + overlap_score * 0.35 + execution_score * 0.20
        if total >= 75:
            return "READY_FOR_STRUCTURED_TRIAL"
        if total >= 60:
            return "PROMISING_BUT_NEEDS_TUNING"
        return "REQUIRES_MORE_REFINEMENT"

    def _recommendations(self, benchmark: Dict[str, Any], overlap: Dict[str, Any], scorecard: Dict[str, Any]) -> List[str]:
        cmp = benchmark.get("comparison", {}) or {}
        current = ((benchmark.get("variants", {}) or {}).get("current_engine", {}) or {}).get("summary", {})
        recommendations: List[str] = []

        if float(cmp.get("net_points_delta", 0) or 0) <= 0:
            recommendations.append("Current engine is not outperforming the baseline enough; review trigger thresholds and profile strictness.")
        if float(cmp.get("win_rate_delta", 0) or 0) < 0:
            recommendations.append("Win rate fell versus baseline; inspect whether trigger gating is filtering too late or too aggressively.")
        if float(scorecard.get("not_filled_ratio", 0) or 0) > self.max_not_filled_ratio:
            recommendations.append("Too many pending setups are not filling; consider loosening market-threshold or POI distance rules.")

        labels = int(overlap.get("labels_considered", 0) or 0)
        if labels > 0:
            if float(overlap.get("match_rate_pct", 0) or 0) < self.min_match_rate_good:
                recommendations.append("Analyst match-rate is still low; revisit POI ranking and setup-type mapping against analyst labels.")
            avg_entry = overlap.get("avg_entry_distance_points")
            if avg_entry is not None and float(avg_entry) > self.max_entry_distance_good:
                recommendations.append("Average entry distance from analyst labels is high; tighten entry timing and rejection confirmation around POI.")
            reasons = overlap.get("top_missed_reasons") or []
            if reasons:
                top = str(reasons[0].get("reason_code") or "")
                if top == "MISSED_ENTRY_TOO_FAR":
                    recommendations.append("Top miss reason is entry lag: tune hybrid MARKET/LIMIT transition and near-POI execution logic.")
                elif top == "MISSED_TIMING_WINDOW":
                    recommendations.append("Top miss reason is timing window: refine session-aware trigger logic and setup expiry handling.")
                elif top == "MISSED_POI_MISMATCH":
                    recommendations.append("Top miss reason is POI mismatch: refine order-block/FVG ranking weights and mitigation penalties.")
        else:
            recommendations.append("No analyst labels available in the evaluation window; import more labels before trusting overlap conclusions.")

        if not recommendations:
            recommendations.append("Current engine is outperforming baseline with acceptable overlap; move to a structured forward trial and monitor drift.")
        return recommendations[:6]
