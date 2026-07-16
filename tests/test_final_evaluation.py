from __future__ import annotations

from services.final_evaluation import FinalEvaluationService


def _config() -> dict:
    return {
        "symbol": "XAU/USD",
        "final_evaluation": {
            "analyst_compare_limit": 30,
            "min_match_rate_good": 55,
            "max_entry_distance_good": 60,
            "max_not_filled_ratio": 0.4,
        },
    }


class _DB:
    pass


def test_final_evaluation_recommendations_reflect_benchmark_and_overlap(monkeypatch) -> None:
    import services.final_evaluation as fe

    benchmark_payload = {
        "variants": {
            "current_engine": {"summary": {"total_trades": 8, "not_filled": 7, "total_candidates": 15, "win_rate": 48.0, "net_points": -120.0}},
            "baseline_classic_market": {"summary": {"total_trades": 10, "not_filled": 2, "total_candidates": 12, "win_rate": 55.0, "net_points": 20.0}},
        },
        "comparison": {
            "win_rate_delta": -7.0,
            "net_points_delta": -140.0,
            "profit_factor_delta": -0.8,
            "filled_trades_delta": -2,
            "not_filled_delta": 5,
        },
    }
    overlap_payload = {
        "labels_considered": 10,
        "matched_labels": 3,
        "partial_matches": 2,
        "missed_labels": 5,
        "extra_bot_setups": 4,
        "match_rate_pct": 30.0,
        "coverage_rate_pct": 50.0,
        "avg_entry_distance_points": 88.0,
        "top_missed_reasons": [{"reason_code": "MISSED_ENTRY_TOO_FAR", "count": 3}],
    }

    monkeypatch.setattr(fe, "benchmark_backtests", lambda *a, **k: benchmark_payload)

    class _Distill:
        def __init__(self, *_a, **_k):
            self.enabled = True
        def compare_recent(self, **_k):
            return overlap_payload

    monkeypatch.setattr(fe, "AnalystDistillationService", _Distill)
    service = FinalEvaluationService(_config(), database=_DB())
    report = service.run(candles=[{"time": "2026-07-15T00:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1}], max_trades=1)

    assert report["verdict"] == "REQUIRES_MORE_REFINEMENT"
    assert report["scorecard"]["benchmark_score"] < 50
    assert "governance_score" in report["scorecard"]
    assert any("entry lag" in rec.lower() or "entry distance" in rec.lower() for rec in report["recommendations"])
    text = service.format_telegram(report)
    assert "Final Evaluation Pass" in text
    assert "Δ Net" in text
    assert "Analyst Overlap" in text
    assert "governance=" in text


def test_final_evaluation_positive_case_can_recommend_trial(monkeypatch) -> None:
    import services.final_evaluation as fe

    benchmark_payload = {
        "variants": {
            "current_engine": {"summary": {"total_trades": 12, "not_filled": 2, "total_candidates": 14, "win_rate": 68.0, "net_points": 420.0}},
            "baseline_classic_market": {"summary": {"total_trades": 10, "not_filled": 3, "total_candidates": 13, "win_rate": 58.0, "net_points": 180.0}},
        },
        "comparison": {
            "win_rate_delta": 10.0,
            "net_points_delta": 240.0,
            "profit_factor_delta": 0.9,
            "filled_trades_delta": 2,
            "not_filled_delta": -1,
        },
    }
    overlap_payload = {
        "labels_considered": 10,
        "matched_labels": 7,
        "partial_matches": 2,
        "missed_labels": 1,
        "extra_bot_setups": 1,
        "match_rate_pct": 70.0,
        "coverage_rate_pct": 90.0,
        "avg_entry_distance_points": 34.0,
        "top_missed_reasons": [{"reason_code": "PARTIAL_POI_MISMATCH", "count": 1}],
    }

    monkeypatch.setattr(fe, "benchmark_backtests", lambda *a, **k: benchmark_payload)

    class _Distill:
        def __init__(self, *_a, **_k):
            self.enabled = True
        def compare_recent(self, **_k):
            return overlap_payload

    monkeypatch.setattr(fe, "AnalystDistillationService", _Distill)
    service = FinalEvaluationService(_config(), database=_DB())
    report = service.run(candles=[{"time": "2026-07-15T00:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1}], max_trades=1)

    assert report["verdict"] == "READY_FOR_STRUCTURED_TRIAL"
    assert report["scorecard"]["benchmark_score"] > 60
    assert report["recommendations"]


def test_final_evaluation_without_labels_uses_neutral_overlap(monkeypatch) -> None:
    import services.final_evaluation as fe

    benchmark_payload = {
        "variants": {
            "current_engine": {"summary": {"total_trades": 9, "not_filled": 2, "total_candidates": 11, "win_rate": 44.4, "net_points": -91.0}},
            "baseline_classic_market": {"summary": {"total_trades": 14, "not_filled": 0, "total_candidates": 14, "win_rate": 14.3, "net_points": -259.0}},
        },
        "comparison": {
            "win_rate_delta": 30.1,
            "net_points_delta": 169.0,
            "profit_factor_delta": 0.29,
            "filled_trades_delta": -5,
            "not_filled_delta": 2,
        },
    }
    overlap_payload = {"labels_considered": 0}

    monkeypatch.setattr(fe, "benchmark_backtests", lambda *a, **k: benchmark_payload)

    class _Distill:
        def __init__(self, *_a, **_k):
            self.enabled = True
        def compare_recent(self, **_k):
            return overlap_payload

    monkeypatch.setattr(fe, "AnalystDistillationService", _Distill)
    service = FinalEvaluationService(_config(), database=_DB())
    report = service.run(candles=[{"time": "2026-07-15T00:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1}], max_trades=1)

    assert report["scorecard"]["overlap_available"] is False
    assert report["scorecard"]["overlap_score"] == 50.0
    assert "Analyst overlap is unavailable" in report["recommendations"][-1]
    assert "governance_score" in report["scorecard"]
