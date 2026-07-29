"""Gemini 429 handling must distinguish a daily quota from a per-minute one.

Both arrive as HTTP 429, but they need opposite treatment. A per-minute limit
clears in seconds and is worth waiting out. A daily limit (RPD) does not clear
until midnight Pacific, so every retry — and every later call in the same run —
is guaranteed to fail. Treating them alike cost 36 seconds of a 71-second run
in sleeps that could not possibly succeed.
"""

from __future__ import annotations

import json
import time

import pytest

import services.llm_review as llm


_RPD_BODY = json.dumps({
    "error": {
        "code": 429,
        "message": "You exceeded your current quota",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{
                    "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                    "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                }],
            },
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"},
        ],
    }
})

_RPM_BODY = json.dumps({
    "error": {
        "code": 429,
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{
                    "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                }],
            },
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "2s"},
        ],
    }
})

_OK_BODY = {
    "candidates": [{"content": {"parts": [{"text": json.dumps({
        "market_bias": "BULLISH",
        "action": "BUY",
        "macro_read": "BULLISH_GOLD",
        "reason": "DXY softening into the London session supports gold",
    })}]}}]
}


class _Response:
    def __init__(self, status: int, body: str, headers: dict | None = None) -> None:
        self.status_code = status
        self.text = body
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


class _Session:
    """Replays a fixed sequence of responses and counts real calls."""

    def __init__(self, *responses: _Response) -> None:
        self._responses = list(responses)
        self.calls = 0

    def post(self, *_args, **_kwargs):
        self.calls += 1
        index = min(self.calls - 1, len(self._responses) - 1)
        return self._responses[index]


@pytest.fixture(autouse=True)
def _reset_quota_latch():
    llm.GeminiReviewService.reset_quota_state()
    yield
    llm.GeminiReviewService.reset_quota_state()


def _service(session, config=None):
    service = llm.GeminiReviewService(config or {})
    service.api_key = "test-key"
    service.enabled = True
    service.session = session
    return service


def _payload():
    return {"symbol": "XAU/USD", "decision": {}, "all_results": {}}


def test_daily_quota_gives_up_immediately() -> None:
    """RPD must not be retried: the quota cannot clear within the run."""
    session = _Session(_Response(429, _RPD_BODY, {"Retry-After": "34"}))
    service = _service(session)

    started = time.time()
    result = service.analyze_market_context(_payload())
    elapsed = time.time() - started

    assert result["available"] is False
    assert "daily quota" in result["reason"].lower()
    assert session.calls == 1, "a daily quota must not be retried"
    assert elapsed < 1.0, f"gave up slowly ({elapsed:.1f}s); it should not sleep"


def test_daily_quota_short_circuits_later_calls() -> None:
    """Once RPD is hit, the rest of the run must not touch the network."""
    session = _Session(_Response(429, _RPD_BODY))
    service = _service(session)

    service.analyze_market_context(_payload())
    assert session.calls == 1

    for call in (
        service.review_signal,
        service.interpret_news_context,
        service.interpret_macro_context,
    ):
        result = call(_payload())
        assert result["available"] is False

    assert session.calls == 1, "later calls still hit the API after RPD"


def test_per_minute_limit_is_retried_and_can_succeed() -> None:
    """RPM is transient: retry it, and do not latch the daily breaker."""
    session = _Session(
        _Response(429, _RPM_BODY, {"Retry-After": "1"}),
        _Response(200, json.dumps(_OK_BODY)),
    )
    service = _service(session)

    result = service.analyze_market_context(_payload())

    assert result["available"] is True
    assert result["market_bias"] == "BULLISH"
    assert session.calls == 2
    assert llm.GeminiReviewService._daily_quota_exhausted is False


def test_retry_after_header_is_honoured() -> None:
    """The server's advised delay must win over the configured guess."""
    session = _Session(
        _Response(429, _RPM_BODY, {"Retry-After": "2"}),
        _Response(200, json.dumps(_OK_BODY)),
    )
    service = _service(session, {"llm_review": {"retry_delay_seconds": 30}})

    started = time.time()
    service.analyze_market_context(_payload())
    elapsed = time.time() - started

    assert 1.5 <= elapsed < 5.0, (
        f"waited {elapsed:.1f}s; expected the advised 2s, not the configured 30s"
    )


def test_retry_delay_is_capped() -> None:
    """A single review must never stall a five-minute analysis cycle."""
    session = _Session(_Response(429, _RPM_BODY, {"Retry-After": "600"}))
    service = _service(
        session, {"llm_review": {"max_retries": 2, "max_retry_delay_seconds": 2}}
    )

    started = time.time()
    service.analyze_market_context(_payload())
    elapsed = time.time() - started

    assert elapsed < 5.0, f"waited {elapsed:.1f}s despite the 2s cap"


def test_long_advised_delay_is_treated_as_daily() -> None:
    """A multi-minute wait is a daily bucket in all but name."""
    body = json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}})
    session = _Session(_Response(429, body, {"Retry-After": "3600"}))
    service = _service(session)

    result = service.analyze_market_context(_payload())

    assert result["available"] is False
    assert session.calls == 1


def test_non_429_errors_keep_their_existing_retry_behaviour() -> None:
    """A 500 is transient and must still be retried, then reported."""
    session = _Session(_Response(500, "internal error"))
    service = _service(
        session, {"llm_review": {"max_retries": 2, "retry_delay_seconds": 1}}
    )

    result = service.analyze_market_context(_payload())

    assert result["available"] is False
    assert "500" in result["reason"]
    assert session.calls == 2
    assert llm.GeminiReviewService._daily_quota_exhausted is False


def test_flash_lite_is_the_default_model(monkeypatch) -> None:
    """The free-tier allowance is the whole reason 429 was permanent."""
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert llm.GeminiReviewService({}).model == "gemini-2.5-flash-lite"


def test_model_can_be_overridden(monkeypatch) -> None:
    """Config sets it; the environment variable still wins."""
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    configured = llm.GeminiReviewService({"llm_review": {"model": "gemini-2.5-flash"}})
    assert configured.model == "gemini-2.5-flash"

    monkeypatch.setenv("GEMINI_MODEL", "gemini-3-flash")
    overridden = llm.GeminiReviewService({"llm_review": {"model": "gemini-2.5-flash"}})
    assert overridden.model == "gemini-3-flash"
