"""Shared pytest fixtures.

NewsRiskAgent transparently pulls a free ForexFactory calendar over the network
as a fallback news source. That makes any test touching NewsRiskAgent flaky and
non-deterministic: when a real high-impact event (e.g. an FOMC speech) happens
to be near "now", tests expecting SAFE/CAUTION suddenly see DANGER.

The autouse fixture below stubs that network call to return no events by
default, so news tests exercise only their own seeded data. Tests that want to
verify the ForexFactory path explicitly still override it with their own
``monkeypatch.setattr(...)`` inside the test, which takes precedence.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest


@pytest.fixture(autouse=True)
def _stub_forexfactory_feed(monkeypatch):
    """Disable the live ForexFactory network fetch for all tests by default."""
    try:
        import services.news_feed_forexfactory as ff
    except Exception:  # noqa: BLE001 - module may not import in some envs
        return
    monkeypatch.setattr(ff, "fetch_forexfactory_events", lambda *a, **k: [], raising=False)


def pytest_pyfunc_call(pyfuncitem):
    """Run ``async def`` tests even when pytest-asyncio is not installed.

    The project declares ``pytest-asyncio`` as a test dependency and uses
    ``@pytest.mark.asyncio``/``async def`` tests.  Some lightweight CI or review
    environments install only pytest itself, which otherwise fails with
    "async def functions are not natively supported" before the application code
    is exercised.  This minimal hook keeps the suite runnable in those
    environments while remaining compatible with pytest-asyncio: if the real
    plugin is present it can still provide its richer fixture support.
    """
    test_func = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_func):
        return None
    fixture_names = pyfuncitem._fixtureinfo.argnames
    kwargs = {name: pyfuncitem.funcargs[name] for name in fixture_names}
    asyncio.run(test_func(**kwargs))
    return True
