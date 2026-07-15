"""Operational pipeline runner.

Final packaging layer for day-to-day use. It runs the three end-state decision
artifacts in order:
1) final evaluation
2) tuning advisor
3) release readiness

The goal is operational clarity: one command, one candle fetch, three saved
artifacts, and one concise Telegram summary when desired.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from services.final_evaluation import FinalEvaluationService
from services.release_readiness import ReleaseReadinessService
from services.tuning_advisor import TuningAdvisor


class OperationsPipeline:
    def __init__(self, config: Dict[str, Any], database: Any | None = None) -> None:
        self.config = config or {}
        self.database = database

    def run(
        self,
        candles: List[Dict[str, Any]],
        *,
        window: int = 160,
        step: int = 12,
        horizon: int = 32,
        max_trades: int = 60,
    ) -> Dict[str, Any]:
        final_eval_service = FinalEvaluationService(self.config, database=self.database)
        final_report = final_eval_service.run(
            candles,
            window=window,
            step=step,
            horizon=horizon,
            max_trades=max_trades,
        )
        tuning_report = TuningAdvisor(self.config).build_advice(final_report)
        readiness_service = ReleaseReadinessService(self.config, database=self.database)
        readiness_report = readiness_service.build_from_reports(final_report, tuning_report)
        return {
            "symbol": self.config.get("symbol", "XAU/USD"),
            "final_evaluation": final_report,
            "tuning_advice": tuning_report,
            "release_readiness": readiness_report,
        }

    def save_bundle(
        self,
        bundle: Dict[str, Any],
        *,
        root: str | Path = "storage/ops_pipeline",
    ) -> Dict[str, str]:
        root_path = Path(root)
        root_path.mkdir(parents=True, exist_ok=True)

        final_eval_service = FinalEvaluationService(self.config, database=self.database)
        final_path = final_eval_service.save(bundle.get("final_evaluation", {}), root_path / "final_evaluation.json")

        tuning = TuningAdvisor(self.config)
        tuning_path = tuning.save(bundle.get("tuning_advice", {}), root_path / "tuning_advice.json")

        readiness_service = ReleaseReadinessService(self.config, database=self.database)
        readiness_path = readiness_service.save(bundle.get("release_readiness", {}), root_path / "release_readiness.json")

        return {
            "final_evaluation": str(final_path),
            "tuning_advice": str(tuning_path),
            "release_readiness": str(readiness_path),
        }
