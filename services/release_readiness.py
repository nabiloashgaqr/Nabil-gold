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
        lines = [
            "🚦 <b>Release Readiness — SmartSignal</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Decision: <b>{readiness.get('decision', 'REVIEW')}</b>",
            f"Reason: {readiness.get('reason', 'No summary')}",
            f"Benchmark {scorecard.get('benchmark_score', 0)}/100 | Overlap {scorecard.get('overlap_score', 0)}/100 | Execution {scorecard.get('execution_score', 0)}/100",
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
        actions = tuning.get("actions", []) or []
        verdict = str(final_eval.get("verdict") or "REVIEW")

        overlap_ok = overlap_score >= self.min_overlap_score_for_trial if overlap_available else True

        if (
            verdict == "READY_FOR_STRUCTURED_TRIAL"
            and benchmark_score >= self.min_benchmark_score_for_trial
            and overlap_ok
            and execution_score >= self.min_execution_score_for_trial
            and len(actions) <= self.max_actions_for_trial
        ):
            decision = "PROCEED_TO_STRUCTURED_TRIAL"
            reason = "Core evaluation is strong and only minor tuning remains."
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
        }
