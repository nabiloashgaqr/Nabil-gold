from __future__ import annotations

from pathlib import Path

from services.database import DatabaseService
from services.setup_memory import SetupMemoryService


def _db(tmp_path: Path) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None}})
    db.setup_candidates_path = tmp_path / "setup_candidates.json"
    return db


def test_setup_state_progresses_and_preserves_identity(tmp_path: Path) -> None:
    db = _db(tmp_path)
    service = SetupMemoryService(
        db,
        {
            "symbol": "XAU/USD",
            "setup_memory": {
                "enabled": True,
                "arm_zone_buffer_points": 40,
                "invalidate_buffer_points": 20,
                "missing_cycles_before_expire": 6,
                "expire_after_hours": 24,
            },
        },
    )

    candidate_v1 = {
        "id": "candidate-v1",
        "state_key": "SMC_STATE::XAU/USD::15m::SELL::LIQUIDITY_REVERSAL::order_block::buy_side::4066.20:4063.40",
        "symbol": "XAU/USD",
        "timeframe": "15m",
        "direction": "SELL",
        "setup_type": "LIQUIDITY_REVERSAL",
        "setup_state": "SWEEP_CONFIRMED",
        "lead_agent": "smc",
        "setup_quality": {"grade": "A", "score": 84},
        "poi_type": "order_block",
        "poi_low": 4063.40,
        "poi_high": 4066.20,
        "stop_loss": 4073.70,
        "sweep_side": "buy_side",
    }

    processed_1 = service.process_candidates([candidate_v1], current_price=4072.0, symbol="XAU/USD")
    assert processed_1[0]["setup_state"] == "POI_MARKED"
    original_id = processed_1[0]["id"]

    candidate_v2 = dict(candidate_v1)
    candidate_v2["id"] = "candidate-v2-new-run"
    processed_2 = service.process_candidates([candidate_v2], current_price=4065.0, symbol="XAU/USD")

    assert processed_2[0]["setup_state"] == "ENTRY_ARMED"
    assert processed_2[0]["id"] == original_id  # same setup, same persisted identity

    service.mark_entry_triggered(
        setup_id=processed_2[0]["id"],
        state_key=processed_2[0]["state_key"],
        trade_id="TRADE_123",
        current_price=4065.0,
        symbol="XAU/USD",
    )
    rows = db.get_recent_setup_candidates(limit=5, symbol="XAU/USD")
    assert rows[0]["setup_state"] == "ENTRY_TRIGGERED"
    assert rows[0]["last_trade_id"] == "TRADE_123"


def test_missing_setup_expires_after_configured_cycles(tmp_path: Path) -> None:
    db = _db(tmp_path)
    service = SetupMemoryService(
        db,
        {
            "symbol": "XAU/USD",
            "setup_memory": {
                "enabled": True,
                "arm_zone_buffer_points": 40,
                "invalidate_buffer_points": 20,
                "missing_cycles_before_expire": 2,
                "expire_after_hours": 24,
            },
        },
    )

    candidate = {
        "id": "candidate-expire",
        "state_key": "SMC_STATE::XAU/USD::15m::BUY::ORDER_BLOCK_PULLBACK::order_block::sell_side::4025.00:4022.00",
        "symbol": "XAU/USD",
        "timeframe": "15m",
        "direction": "BUY",
        "setup_type": "ORDER_BLOCK_PULLBACK",
        "setup_state": "POI_MARKED",
        "lead_agent": "smc",
        "poi_type": "order_block",
        "poi_low": 4022.0,
        "poi_high": 4025.0,
        "stop_loss": 4015.0,
        "sweep_side": "sell_side",
    }

    service.process_candidates([candidate], current_price=4030.0, symbol="XAU/USD")
    service.process_candidates([], current_price=4031.0, symbol="XAU/USD")
    service.process_candidates([], current_price=4032.0, symbol="XAU/USD")

    rows = db.get_recent_setup_candidates(limit=5, symbol="XAU/USD")
    assert rows[0]["setup_state"] == "EXPIRED"
    assert rows[0]["is_active"] is False


def test_missing_setup_invalidates_if_price_breaches_invalidation(tmp_path: Path) -> None:
    db = _db(tmp_path)
    service = SetupMemoryService(
        db,
        {
            "symbol": "XAU/USD",
            "setup_memory": {
                "enabled": True,
                "arm_zone_buffer_points": 40,
                "invalidate_buffer_points": 20,
                "missing_cycles_before_expire": 6,
                "expire_after_hours": 24,
            },
        },
    )

    candidate = {
        "id": "candidate-invalidate",
        "state_key": "SMC_STATE::XAU/USD::15m::BUY::LIQUIDITY_REVERSAL::order_block::sell_side::4025.00:4022.00",
        "symbol": "XAU/USD",
        "timeframe": "15m",
        "direction": "BUY",
        "setup_type": "LIQUIDITY_REVERSAL",
        "setup_state": "ENTRY_ARMED",
        "lead_agent": "smc",
        "poi_type": "order_block",
        "poi_low": 4022.0,
        "poi_high": 4025.0,
        "stop_loss": 4015.0,
        "sweep_side": "sell_side",
    }

    service.process_candidates([candidate], current_price=4024.0, symbol="XAU/USD")
    service.process_candidates([], current_price=4012.5, symbol="XAU/USD")

    rows = db.get_recent_setup_candidates(limit=5, symbol="XAU/USD")
    assert rows[0]["setup_state"] == "INVALIDATED"
    assert rows[0]["is_active"] is False
