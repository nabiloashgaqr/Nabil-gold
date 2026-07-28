"""Guards for structural POI map persistence.

The SMC score crosses its +-4 cutoff on sub-point proximity terms, and deriving
the candidate map only from that label discarded zones the market still
respected. These tests pin the intended behaviour: the map survives a NEUTRAL
score when structure has a bias, ages out, and can be switched off entirely.
"""
from __future__ import annotations

import copy

from agents.smc_agent import SMCAgent
from utils.helpers import load_config


def _config(**overrides):
    config = copy.deepcopy(load_config())
    persistence = config.setdefault("smc_engine", {}).setdefault("map_persistence", {})
    persistence.update(overrides)
    return config


def test_scored_direction_pool_signature_is_unchanged() -> None:
    # Callers rely on the static two-argument form.
    assert SMCAgent._candidate_direction_pool("SELL", "BUY") == ["SELL", "BUY"]
    assert SMCAgent._candidate_direction_pool("NEUTRAL", None) == []


def test_structural_direction_follows_trend_only() -> None:
    assert SMCAgent._structural_direction({"trend": "BEARISH"}) == "SELL"
    assert SMCAgent._structural_direction({"trend": "BULLISH"}) == "BUY"
    assert SMCAgent._structural_direction({"trend": "RANGING"}) is None
    assert SMCAgent._structural_direction({}) is None


def test_neutral_score_keeps_map_when_structure_has_bias() -> None:
    agent = SMCAgent(_config(enabled=True))
    pool = agent._persisted_direction_pool([], {"trend": "BEARISH"})
    assert pool == ["SELL"], "a bearish structure must keep the sell-side map addressable"


def test_ranging_structure_yields_no_map() -> None:
    agent = SMCAgent(_config(enabled=True))
    assert agent._persisted_direction_pool([], {"trend": "RANGING"}) == []


def test_scored_pool_is_never_overridden() -> None:
    # Persistence is a fallback: it must not add directions the score already resolved.
    agent = SMCAgent(_config(enabled=True))
    assert agent._persisted_direction_pool(["BUY"], {"trend": "BEARISH"}) == ["BUY"]


def test_disabled_flag_restores_previous_behaviour() -> None:
    agent = SMCAgent(_config(enabled=False))
    assert agent._persisted_direction_pool([], {"trend": "BEARISH"}) == []


def test_age_limit_drops_stale_zones_and_keeps_fresh_ones() -> None:
    agent = SMCAgent(_config(enabled=True, max_age_minutes=60))
    candles = [{"time": "2026-07-28T12:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1}]
    candidates = [
        {"id": "fresh", "created_at": "2026-07-28T11:30:00Z"},
        {"id": "stale", "created_at": "2026-07-28T09:00:00Z"},
    ]
    kept = {c["id"] for c in agent._enforce_map_age_limit(candidates, candles)}
    assert kept == {"fresh"}


def test_age_limit_keeps_candidates_without_a_timestamp() -> None:
    # Age cannot be proven, so the zone is not silently discarded.
    agent = SMCAgent(_config(enabled=True, max_age_minutes=60))
    candles = [{"time": "2026-07-28T12:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1}]
    candidates = [{"id": "unknown", "created_at": ""}]
    assert len(agent._enforce_map_age_limit(candidates, candles)) == 1


def test_age_limit_disabled_when_cap_is_zero() -> None:
    agent = SMCAgent(_config(enabled=True, max_age_minutes=0))
    candles = [{"time": "2026-07-28T12:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1}]
    candidates = [{"id": "ancient", "created_at": "2026-07-01T00:00:00Z"}]
    assert len(agent._enforce_map_age_limit(candidates, candles)) == 1
