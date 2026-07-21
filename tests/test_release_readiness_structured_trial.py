from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.release_readiness import ReleaseReadinessService


def _config() -> dict:
    return {
        "symbol": "XAU/USD",
        "release_readiness": {
            "max_actions_for_trial": 2,
            "min_benchmark_score_for_trial": 65,
            "min_overlap_score_for_trial": 45,
            "min_execution_score_for_trial": 55,
            "min_governance_score_for_trial": 55,
            "min_day_map_execution_score_for_trial": 55,
            "allow_structured_trial_when_no_overlap_labels": True,
        },
    }


class _DB:
    pass


def test_release_readiness_promising_without_actions_can_proceed(monkeypatch) -> None:
    import services.release_readiness as rr

    final_eval = {
        "verdict": "PROMISING_BUT_NEEDS_TUNING",
        "scorecard": {
            "benchmark_score": 68.5,
            "overlap_score": 50.0,
            "overlap_available": False,
            "execution_score": 81.8,
            "governance_score": 62.0,
            "governance_available": True,
            "day_map_execution_score": 71.0,
            "day_map_execution_available": True,
        },
    }
    tuning = {"actions": [], "recommendations": ["No urgent tuning changes detected."]}

    class _Final:
        def __init__(self, *_a, **_k):
            pass
        def run(self, *a, **k):
            return final_eval

    class _Tune:
        def __init__(self, *_a, **_k):
            pass
        def build_advice(self, *_a, **_k):
            return tuning

    monkeypatch.setattr(rr, "FinalEvaluationService", _Final)
    monkeypatch.setattr(rr, "TuningAdvisor", _Tune)

    report = ReleaseReadinessService(_config(), database=_DB()).run(candles=[{"time": "2026-07-16T00:00:00Z"}])
    assert report["readiness"]["decision"] == "PROCEED_TO_STRUCTURED_TRIAL"
    assert "tightly monitored structured trial" in report["readiness"]["reason"].lower()
