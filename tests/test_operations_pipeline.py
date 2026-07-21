from __future__ import annotations

from pathlib import Path

from services.operations_pipeline import OperationsPipeline


def _config() -> dict:
    return {"symbol": "XAU/USD"}


def test_operations_pipeline_stitches_reports(monkeypatch, tmp_path: Path) -> None:
    import services.operations_pipeline as op

    final_report = {"verdict": "PROMISING_BUT_NEEDS_TUNING", "scorecard": {"benchmark_score": 60, "overlap_score": 50, "execution_score": 70}}
    tuning = {"actions": [{"key": "x"}], "recommendations": ["do something"], "config_patch": {"a": 1}, "operator_memo": {"headline": "main area weak", "priority": "MEDIUM"}}
    readiness = {"readiness": {"decision": "APPLY_TUNING_THEN_REEVALUATE"}}

    class _Final:
        def __init__(self, *_a, **_k):
            pass
        def run(self, *a, **k):
            return final_report
        def save(self, report, path):
            Path(path).write_text("final", encoding="utf-8")
            return Path(path)

    class _Tune:
        def __init__(self, *_a, **_k):
            pass
        def build_advice(self, *_a, **_k):
            return tuning
        def format_management_brief(self, *_a, **_k):
            return "management text"
        def format_operator_memo(self, *_a, **_k):
            return "memo text"
        def save(self, report, path):
            Path(path).write_text("tuning", encoding="utf-8")
            return Path(path)

    class _Ready:
        def __init__(self, *_a, **_k):
            pass
        def build_from_reports(self, *_a, **_k):
            return readiness
        def save(self, report, path):
            Path(path).write_text("readiness", encoding="utf-8")
            return Path(path)

    monkeypatch.setattr(op, "FinalEvaluationService", _Final)
    monkeypatch.setattr(op, "TuningAdvisor", _Tune)
    monkeypatch.setattr(op, "ReleaseReadinessService", _Ready)

    pipeline = OperationsPipeline(_config(), database=object())
    bundle = pipeline.run(candles=[{"time": "2026-07-15T00:00:00Z"}])
    assert bundle["final_evaluation"] == final_report
    assert bundle["tuning_advice"] == tuning
    assert bundle["release_readiness"] == readiness
    assert bundle["management_brief_text"] == "management text"
    assert bundle["operator_memo_text"] == "memo text"

    paths = pipeline.save_bundle(bundle, root=tmp_path)
    assert Path(paths["final_evaluation"]).exists()
    assert Path(paths["tuning_advice"]).exists()
    assert Path(paths["release_readiness"]).exists()
    assert Path(paths["management_brief"]).exists()
    assert Path(paths["operator_memo"]).exists()
