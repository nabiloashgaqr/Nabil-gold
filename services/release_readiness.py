"""Release readiness / production hardening final pass.

This layer ties together:
- final evaluation (benchmark + analyst overlap)
- tuning advisor

It answers one practical question:
    "Are we ready for a structured forward trial, or should we tune first?"
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from services.final_evaluation import FinalEvaluationService
from services.tuning_advisor import TuningAdvisor


class ReleaseReadinessService:
    def __init__(self, config: Dict[str, Any], database: Any | None = None) -> None:
        self.config = config or {}
        self.database = database
        cfg = self.config.get("release_readiness", {}) or {}
        self.max_actions_for_trial = int(cfg.get("max_actions_for_trial", 2) or 2)
        self.min_benchmark_score_for_trial = float(cfg.get("min_benchmark_score_for_trial", 65) or 65)
        self.min_overlap_score_for_trial = float(cfg.get("min_overlap_score_for_trial", 45) or 45)
        self.min_execution_score_for_trial = float(cfg.get("min_execution_score_for_trial", 55) or 55)
        self.min_governance_score_for_trial = float(cfg.get("min_governance_score_for_trial", 55) or 55)
        self.min_day_map_execution_score_for_trial = float(cfg.get("min_day_map_execution_score_for_trial", 55) or 55)
        self.allow_structured_trial_when_no_overlap_labels = bool(cfg.get("allow_structured_trial_when_no_overlap_labels", True))

    def build_from_reports(self, final_eval: Dict[str, Any], tuning: Dict[str, Any]) -> Dict[str, Any]:
        readiness = self._readiness_decision(final_eval, tuning)
        return {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "symbol": self.config.get("symbol", "XAU/USD"),
            "final_evaluation": final_eval,
            "tuning_advice": tuning,
            "readiness": readiness,
        }

    def run(
        self,
        candles: List[Dict[str, Any]],
        *,
        window: int = 160,
        step: int = 12,
        horizon: int = 32,
        max_trades: int = 60,
    ) -> Dict[str, Any]:
        final_eval = FinalEvaluationService(self.config, database=self.database).run(
            candles,
            window=window,
            step=step,
            horizon=horizon,
            max_trades=max_trades,
        )
        tuning = TuningAdvisor(self.config).build_advice(final_eval)
        return self.build_from_reports(final_eval, tuning)

    def save(self, report: Dict[str, Any], path: str | Path = "storage/release_readiness.json") -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def format_telegram(self, report: Dict[str, Any]) -> str:
        readiness = report.get("readiness", {}) or {}
        scorecard = ((report.get("final_evaluation", {}) or {}).get("scorecard", {}) or {})
        recommendations = ((report.get("tuning_advice", {}) or {}).get("recommendations", []) or [])
        overlap_text = (
            f"Overlap {scorecard.get('overlap_score', 0)}/100"
            if scorecard.get('overlap_available', False)
            else "Overlap N/A"
        )
        lines = [
            "🚦 <b>Release Readiness — SmartSignal</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Decision: <b>{readiness.get('decision', 'REVIEW')}</b>",
            f"Reason: {readiness.get('reason', 'No summary')}",
            f"Benchmark {scorecard.get('benchmark_score', 0)}/100 | {overlap_text} | Execution {scorecard.get('execution_score', 0)}/100 | Governance {scorecard.get('governance_score', 0)}/100 | DayMap {scorecard.get('day_map_execution_score', 0)}/100",
            f"Actions suggested: {readiness.get('actions_count', 0)}",
        ]
        if recommendations:
            lines.append("Next actions:")
            for rec in recommendations[:4]:
                lines.append(f"• {rec}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def _readiness_decision(self, final_eval: Dict[str, Any], tuning: Dict[str, Any]) -> Dict[str, Any]:
        scorecard = final_eval.get("scorecard", {}) or {}
        benchmark_score = float(scorecard.get("benchmark_score", 0) or 0)
        overlap_score = float(scorecard.get("overlap_score", 0) or 0)
        overlap_available = bool(scorecard.get("overlap_available", False))
        execution_score = float(scorecard.get("execution_score", 0) or 0)
        governance_score = float(scorecard.get("governance_score", 0) or 0)
        governance_available = bool(scorecard.get("governance_available", False))
        day_map_execution_score = float(scorecard.get("day_map_execution_score", 0) or 0)
        day_map_execution_available = bool(scorecard.get("day_map_execution_available", False))
        actions = tuning.get("actions", []) or []
        verdict = str(final_eval.get("verdict") or "REVIEW")

        overlap_ok = overlap_score >= self.min_overlap_score_for_trial if overlap_available else self.allow_structured_trial_when_no_overlap_labels
        governance_ok = governance_score >= self.min_governance_score_for_trial if governance_available else True
        day_map_ok = day_map_execution_score >= self.min_day_map_execution_score_for_trial if day_map_execution_available else True

        if (
            verdict == "READY_FOR_STRUCTURED_TRIAL"
            and benchmark_score >= self.min_benchmark_score_for_trial
            and overlap_ok
            and execution_score >= self.min_execution_score_for_trial
            and governance_ok
            and day_map_ok
            and len(actions) <= self.max_actions_for_trial
        ):
            decision = "PROCEED_TO_STRUCTURED_TRIAL"
            reason = "Core evaluation is strong and only minor tuning remains."
        elif verdict == "PROMISING_BUT_NEEDS_TUNING" and len(actions) == 0 and benchmark_score >= self.min_benchmark_score_for_trial and execution_score >= self.min_execution_score_for_trial and governance_ok and overlap_ok and day_map_ok:
            decision = "PROCEED_TO_STRUCTURED_TRIAL"
            reason = "Engine is promising and no urgent tuning actions remain; proceed with a tightly monitored structured trial."
        elif verdict == "PROMISING_BUT_NEEDS_TUNING":
            decision = "APPLY_TUNING_THEN_REEVALUATE"
            reason = "Engine is promising, but tuning recommendations should be applied before trial expansion."
        else:
            decision = "HOLD_AND_REFINEMENT_REQUIRED"
            reason = "Current evidence is not strong enough for a structured forward trial yet."

        return {
            "decision": decision,
            "reason": reason,
            "actions_count": len(actions),
            "benchmark_score": round(benchmark_score, 1),
            "overlap_score": round(overlap_score, 1),
            "overlap_available": overlap_available,
            "execution_score": round(execution_score, 1),
            "governance_score": round(governance_score, 1),
            "governance_available": governance_available,
            "day_map_execution_score": round(day_map_execution_score, 1),
            "day_map_execution_available": day_map_execution_available,
        }
