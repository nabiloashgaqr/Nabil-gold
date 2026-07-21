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
        },
    }


class _DB:
    pass


def test_release_readiness_allows_trial_without_labels_when_other_scores_are_good(monkeypatch) -> None:
    import services.release_readiness as rr

    final_eval = {
        "verdict": "READY_FOR_STRUCTURED_TRIAL",
        "scorecard": {
            "benchmark_score": 78,
            "overlap_score": 50,
            "overlap_available": False,
            "execution_score": 84,
            "day_map_execution_score": 70,
            "day_map_execution_available": True,
        },
    }
    tuning = {"actions": [{"key": "minor_1"}], "recommendations": ["minor tune"]}

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

    report = ReleaseReadinessService(_config(), database=_DB()).run(candles=[{"time": "2026-07-15T00:00:00Z"}])
    assert report["readiness"]["decision"] == "PROCEED_TO_STRUCTURED_TRIAL"
    assert report["readiness"]["overlap_available"] is False
