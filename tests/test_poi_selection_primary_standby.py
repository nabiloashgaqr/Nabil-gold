from __future__ import annotations

from agents.smc_agent import SMCAgent


def test_build_setup_candidates_assigns_primary_and_standby_roles() -> None:
    agent = SMCAgent(
        {
            "symbol": "XAU/USD",
            "smc_engine": {
                "selection": {
                    "max_candidates": 5,
                    "standby_min_dominance_score": 40,
                    "standby_min_relative_to_primary": 0.70,
                }
            },
        }
    )
    candidates = agent._build_setup_candidates(
        symbol="XAU/USD",
        timeframe="15m",
        direction="SELL",
        current_price=4030.0,
        atr=2.0,
        confidence=82,
        market_structure={"trend": "BEARISH", "structure_quality": "STRONG"},
        order_blocks=[
            {
                "type": "bearish",
                "zone": {"top": 4041.0, "bottom": 4036.0},
                "strength": "strong",
                "created_at": "2026-07-16T02:00:00Z",
                "mitigation_status": "FRESH",
                "displacement_atr": 2.2,
                "displacement_quality": "STRONG",
                "invalidated": False,
            },
            {
                "type": "bearish",
                "zone": {"top": 4035.5, "bottom": 4033.0},
                "strength": "medium",
                "created_at": "2026-07-16T02:15:00Z",
                "mitigation_status": "TESTED",
                "displacement_atr": 1.4,
                "displacement_quality": "MODERATE",
                "invalidated": False,
            },
        ],
        liquidity={
            "recent_sweep": {"occurred": True, "type": "buy_side", "confirmation": "STRONG", "reference_type": "previous_day_high"},
            "previous_day_levels": {"high": 4042.0, "low": 4008.0},
            "session_liquidity": {"label": "London / Europe Midday", "high": 4041.2, "low": 4020.0},
            "buy_side": [4042.0],
            "sell_side": [4018.0, 4009.0],
        },
        fvg=[
            {
                "type": "bearish",
                "zone": {"top": 4033.8, "bottom": 4032.5},
                "strength": "medium",
                "created_at": "2026-07-16T02:20:00Z",
                "partial_fill": False,
                "filled": False,
                "size": 1.3,
            }
        ],
        dealing_range={"high": 4045.0, "low": 4005.0, "midpoint": 4025.0},
        entry_suggestion={"entry": 4038.5, "sl": 4044.0, "tp": 4018.0, "reason": "Sell from strong OB"},
        candles=[
            {"open": 4037.8, "high": 4040.8, "low": 4036.9, "close": 4037.1},
            {"open": 4037.1, "high": 4038.0, "low": 4029.5, "close": 4030.0},
        ],
    )
    assert candidates
    assert candidates[0]["selection_role"] == "PRIMARY"
    assert any(c["selection_role"] == "STANDBY" for c in candidates[1:])
    assert candidates[0]["thesis_dominance_score"] >= candidates[1]["thesis_dominance_score"]


def test_return_probability_prefers_closer_reachable_zone() -> None:
    agent = SMCAgent({"symbol": "XAU/USD"})
    all_candidates = [
        {"zone": {"top": 4040.0, "bottom": 4036.0}, "mitigation_status": "FRESH", "strength": "strong"},
        {"zone": {"top": 4058.0, "bottom": 4052.0}, "mitigation_status": "FRESH", "strength": "strong"},
    ]
    close_score = agent._return_probability_score(
        poi=all_candidates[0],
        direction="SELL",
        current_price=4030.0,
        atr=2.0,
        market_structure={"trend": "BEARISH", "structure_quality": "MODERATE"},
        liquidity={"session_liquidity": {"label": "London / Europe Midday"}},
        all_candidates=all_candidates,
    )
    far_score = agent._return_probability_score(
        poi=all_candidates[1],
        direction="SELL",
        current_price=4030.0,
        atr=2.0,
        market_structure={"trend": "BEARISH", "structure_quality": "MODERATE"},
        liquidity={"session_liquidity": {"label": "London / Europe Midday"}},
        all_candidates=all_candidates,
    )
    assert close_score > far_score


def test_signal_format_can_show_selection_role_and_scores() -> None:
    from services.telegram_bot import TelegramService
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    captured = {}

    def _fake_send(text: str, urgent: bool = False, **_k):
        captured["text"] = text
        return True

    service.send_message = _fake_send  # type: ignore[assignment]
    decision = {
        "decision": "SELL",
        "symbol": "XAU/USD",
        "confidence": 82,
        "current_price": 4030.0,
        "quality": {"grade": "A", "score": 84},
        "signal": {
            "type": "SELL",
            "entry": {"price": 4040.6, "low": 4036.0, "high": 4041.0},
            "stop_loss": 4080.6,
            "tp1": 3990.6,
            "tp2": 3950.6,
            "rr_ratio": 2.25,
        },
        "setup_context": {
            "setup_type": "LIQUIDITY_REVERSAL",
            "setup_state": "ENTRY_ARMED",
            "lead_agent": "smc",
            "quality_grade": "A",
            "selection_role": "PRIMARY",
            "return_probability_score": 74.0,
            "thesis_dominance_score": 81.0,
            "expected_revisit_window": "NEAR",
        },
        "trade_id": "TRADE_PRIMARY_SHOW",
    }
    service.send_signal(decision)
    text = captured["text"]
    assert "role PRIMARY" in text
    assert "reach 74.0" in text
    assert "dominance 81.0" in text
