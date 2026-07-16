from __future__ import annotations

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


def test_release_readiness_can_proceed_to_structured_trial(monkeypatch) -> None:
    import services.release_readiness as rr

    final_eval = {
        "verdict": "READY_FOR_STRUCTURED_TRIAL",
        "scorecard": {"benchmark_score": 78, "overlap_score": 64, "overlap_available": True, "execution_score": 84, "governance_score": 68, "governance_available": True},
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


def test_release_readiness_can_require_tuning(monkeypatch) -> None:
    import services.release_readiness as rr

    final_eval = {
        "verdict": "PROMISING_BUT_NEEDS_TUNING",
        "scorecard": {"benchmark_score": 62, "overlap_score": 48, "overlap_available": True, "execution_score": 70, "governance_score": 60, "governance_available": True},
    }
    tuning = {"actions": [{"key": "a"}, {"key": "b"}, {"key": "c"}], "recommendations": ["fix things"]}

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
    assert report["readiness"]["decision"] == "APPLY_TUNING_THEN_REEVALUATE"


def test_release_readiness_can_hold_for_more_refinement(monkeypatch) -> None:
    import services.release_readiness as rr

    final_eval = {
        "verdict": "REQUIRES_MORE_REFINEMENT",
        "scorecard": {"benchmark_score": 35, "overlap_score": 20, "overlap_available": True, "execution_score": 30, "governance_score": 20, "governance_available": True},
    }
    tuning = {"actions": [{"key": "a"}], "recommendations": ["more work"]}

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

    svc = ReleaseReadinessService(_config(), database=_DB())
    report = svc.run(candles=[{"time": "2026-07-15T00:00:00Z"}])
    assert report["readiness"]["decision"] == "HOLD_AND_REFINEMENT_REQUIRED"
    text = svc.format_telegram(report)
    assert "Release Readiness" in text
