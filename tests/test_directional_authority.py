from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.directional_authority import DirectionalAuthorityService


def _config() -> dict:
    return {
        "symbol": "XAU/USD",
        "directional_authority": {
            "enabled": True,
            "min_confidence_for_flip": 88,
            "min_trigger_score_for_flip": 70,
            "require_reversal_setup_for_flip": True,
            "require_rejection_confirmed_for_flip": True,
            "require_fresh_sweep_for_flip": True,
        },
    }


def _plan(direction: str = "SELL", state: str = "CONFIRMED") -> dict:
    return {
        "authority_state": state,
        "authority_direction": direction,
        "plan_ready": True,
        "scenario_id": f"SCENARIO::{direction}",
    }


def _decision(
    side: str,
    *,
    confidence: float = 92,
    setup_type: str = "STRUCTURE_CONTINUATION",
    trigger_state: str = "AT_POI_WAIT_TRIGGER",
    trigger_score: float = 58,
    sweep_side: str = "buy_side",
) -> dict:
    return {
        "decision": side,
        "symbol": "XAU/USD",
        "confidence": confidence,
        "setup_context": {
            "setup_type": setup_type,
            "trigger_state": trigger_state,
            "trigger_score": trigger_score,
            "sweep_side": sweep_side,
        },
    }


def test_directional_authority_allows_signal_when_it_matches_day_map() -> None:
    service = DirectionalAuthorityService(_config())
    review = service.review(_decision("SELL"), _plan("SELL"), [])
    assert review["action"] == "ALLOW"


def test_directional_authority_blocks_weak_local_opposite_direction() -> None:
    service = DirectionalAuthorityService(_config())
    review = service.review(
        _decision("BUY", confidence=88, setup_type="INTRADAY_ALIGNMENT", trigger_state="AT_POI_WAIT_TRIGGER", trigger_score=45, sweep_side="buy_side"),
        _plan("SELL"),
        [{"symbol": "XAU/USD", "type": "SELL", "status": "OPEN"}],
    )
    assert review["action"] == "BLOCK_OPPOSITE_LOCAL"
    assert "confirmed SELL day map" in review["reason"]


def test_directional_authority_allows_regime_flip_only_for_strong_reversal() -> None:
    service = DirectionalAuthorityService(_config())
    review = service.review(
        _decision("BUY", confidence=92, setup_type="LIQUIDITY_REVERSAL", trigger_state="REJECTION_CONFIRMED", trigger_score=78, sweep_side="sell_side"),
        _plan("SELL"),
        [{"symbol": "XAU/USD", "type": "SELL", "status": "OPEN"}],
    )
    assert review["action"] == "ALLOW_REGIME_FLIP"
