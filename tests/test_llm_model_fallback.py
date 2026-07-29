"""A refused model must not be retried, and must not silence the reviewer.

Switching the default to Flash-Lite fixed the quota problem and introduced a
new one: the live run answered HTTP 404, because model availability is granted
per key rather than per documentation page. A published, correct model id can
still be refused.

The retry loop then treated that permanent failure as a transient one and
burned 36 seconds per cycle re-asking for a model that does not exist -- the
exact fault the 429 handling was written to eliminate, in a new disguise.

So: never retry a 404, fall forward through the alternatives, and remember the
answer for the rest of the run.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import services.llm_review as llm

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

_NOT_FOUND = json.dumps({
    "error": {"code": 404, "message": "is not found for API version v1beta",
              "status": "NOT_FOUND"}
})
_OK = {"candidates": [{"content": {"parts": [{"text": json.dumps({
    "market_bias": "BEARISH", "action": "SELL", "macro_read": "NEUTRAL",
    "reason": "Buy-side sweep rejected from premium favours downside",
})}]}}]}


class _Response:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self.text = body
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


class _Session:
    """Answers 200 only for `working`; records every model asked for."""

    def __init__(self, working: str | None = None):
        self.working = working
        self.models: list[str] = []

    def post(self, url, **_kwargs):
        model = url.split("/models/")[1].split(":")[0]
        self.models.append(model)
        if model == self.working:
            return _Response(200, json.dumps(_OK))
        return _Response(404, _NOT_FOUND)


@pytest.fixture(autouse=True)
def _reset():
    llm.GeminiReviewService.reset_quota_state()
    yield
    llm.GeminiReviewService.reset_quota_state()


def _service(session, config=None):
    service = llm.GeminiReviewService(config or CONFIG)
    service.api_key = "test-key"
    service.enabled = True
    service.session = session
    return service


def _payload():
    return {"symbol": "XAU/USD", "decision": {}, "all_results": {}}


# --- a 404 is permanent --------------------------------------------------

def test_a_missing_model_is_never_retried() -> None:
    """Each candidate is asked once, not three times."""
    session = _Session(working=None)
    service = _service(session)

    started = time.time()
    result = service.analyze_market_context(_payload())
    elapsed = time.time() - started

    assert result["available"] is False
    assert len(session.models) == len(set(session.models)), "a model was re-asked"
    assert elapsed < 1.0, f"slept {elapsed:.1f}s on a permanent error"


def test_it_falls_forward_to_a_model_that_works() -> None:
    session = _Session(working="gemini-2.5-flash")
    service = _service(session)

    result = service.analyze_market_context(_payload())

    assert result["available"] is True
    assert result["market_bias"] == "BEARISH"
    assert session.models == ["gemini-2.5-flash-lite", "gemini-2.5-flash"]


def test_the_cheapest_working_model_is_preferred() -> None:
    """Flash-Lite answers, so nothing dearer is tried."""
    session = _Session(working="gemini-2.5-flash-lite")
    service = _service(session)

    assert service.analyze_market_context(_payload())["available"] is True
    assert session.models == ["gemini-2.5-flash-lite"]


def test_exhausting_every_candidate_reports_them_all() -> None:
    session = _Session(working=None)
    service = _service(session)

    result = service.analyze_market_context(_payload())

    assert "no usable Gemini model" in result["reason"]
    for candidate in llm.GeminiReviewService.DEFAULT_FALLBACK_MODELS:
        assert candidate in result["reason"]


# --- the answer is remembered -------------------------------------------

def test_later_calls_skip_a_dead_model_entirely() -> None:
    """Discovery happens once, not on all four reviews in a cycle."""
    session = _Session(working=None)
    service = _service(session)

    for call in (service.analyze_market_context, service.review_signal,
                 service.interpret_news_context, service.interpret_macro_context):
        assert call(_payload())["available"] is False

    assert len(session.models) == len(llm.GeminiReviewService.DEFAULT_FALLBACK_MODELS), (
        f"re-discovered the dead model on later calls: {session.models}"
    )


def test_a_working_model_is_reused_by_the_next_service() -> None:
    """The whole cycle should not re-pay the discovery cost."""
    first = _Session(working="gemini-2.5-flash")
    _service(first).analyze_market_context(_payload())

    second = _Session(working="gemini-2.5-flash")
    result = _service(second).review_signal(_payload())

    assert result["available"] is True
    assert second.models == ["gemini-2.5-flash"], "started from the dead model again"


# --- transient failures keep their old behaviour -------------------------

def test_a_500_is_still_retried() -> None:
    """Only 404 is permanent; server errors are worth another attempt."""
    class _Flaky:
        def __init__(self):
            self.calls = 0

        def post(self, *_a, **_k):
            self.calls += 1
            return _Response(500, "internal error")

    session = _Flaky()
    service = _service(session, {"llm_review": {"max_retries": 2, "retry_delay_seconds": 1}})

    result = service.analyze_market_context(_payload())

    assert result["available"] is False
    assert session.calls == 2


def test_the_fallback_chain_is_configurable() -> None:
    session = _Session(working="my-model")
    service = _service(session, {"llm_review": {
        "model": "first-choice",
        "fallback_models": ["first-choice", "my-model"],
    }})

    assert service.analyze_market_context(_payload())["available"] is True
    assert session.models == ["first-choice", "my-model"]


def test_config_ships_a_fallback_chain() -> None:
    chain = CONFIG["llm_review"]["fallback_models"]
    assert chain[0] == CONFIG["llm_review"]["model"], "the chain must start at the configured model"
    assert len(chain) >= 2
