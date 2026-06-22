"""Tests for services/dynamic_risk.py.

DynamicRiskManager has no prior test coverage despite gating every live
BUY/SELL decision (DecisionAgent -> should_block_signal). These tests cover
each risk level transition and the signal-blocking logic.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.dynamic_risk import DynamicRiskManager, should_block_signal


def make_config(**overrides):
    settings = {
        "enabled": True,
        "warn_after_losses": 2,
        "halt_after_losses": 3,
        "daily_loss_limit_points": 30,
        "caution_min_confidence": 75,
        "strict_min_confidence": 82,
        "caution_min_quality_score": 70,
        "strict_min_quality_score": 80,
        "normal_min_quality_score": 0,
        "recent_losses_caution": 2,
        "recent_trades_limit": 20,
        "caution_risk_multiplier": 0.75,
        "strict_risk_multiplier": 0.5,
    }
    settings.update(overrides)
    return {
        "risk_settings": {"min_confidence": 60},
        "dynamic_risk_management": settings,
    }


class FakeDatabase:
    """Minimal stand-in for DatabaseService, only the methods DynamicRiskManager calls."""

    def __init__(self, consecutive_losses=0, today_trades=None, recent_trades=None):
        self._consecutive_losses = consecutive_losses
        self._today_trades = today_trades or []
        self._recent_trades = recent_trades if recent_trades is not None else self._today_trades

    def get_consecutive_losses(self) -> int:
        return self._consecutive_losses

    def get_today_trades(self):
        return self._today_trades

    def get_recent_trades(self, limit: int = 20):
        return self._recent_trades[:limit]


def closed_trade(pnl: float, status: str = "TP1_HIT"):
    return {"status": status, "final_pnl": pnl}


class TestDynamicRiskManagerEvaluate:
    def test_disabled_returns_permissive_normal_state(self):
        manager = DynamicRiskManager(make_config(enabled=False))
        result = manager.evaluate(FakeDatabase())
        assert result["enabled"] is False
        assert result["can_trade"] is True
        assert result["level"] == "NORMAL"
        assert result["risk_multiplier"] == 1.0

    def test_no_history_is_normal_level(self):
        manager = DynamicRiskManager(make_config())
        db = FakeDatabase(consecutive_losses=0, today_trades=[], recent_trades=[])
        result = manager.evaluate(db)
        assert result["level"] == "NORMAL"
        assert result["can_trade"] is True
        assert result["min_confidence_required"] == 60
        assert result["risk_multiplier"] == 1.0

    def test_halt_after_losses_blocks_trading_completely(self):
        manager = DynamicRiskManager(make_config(halt_after_losses=3))
        db = FakeDatabase(consecutive_losses=3)
        result = manager.evaluate(db)
        assert result["level"] == "HALT"
        assert result["can_trade"] is False
        assert result["min_confidence_required"] == 100
        assert result["min_quality_score"] == 100
        assert result["risk_multiplier"] == 0.0
        assert result["warnings"]

    def test_daily_loss_limit_triggers_daily_halt(self):
        manager = DynamicRiskManager(make_config(daily_loss_limit_points=30))
        today = [closed_trade(-20, "SL_HIT"), closed_trade(-15, "SL_HIT")]
        db = FakeDatabase(consecutive_losses=0, today_trades=today, recent_trades=today)
        result = manager.evaluate(db)
        assert result["level"] == "DAILY_HALT"
        assert result["can_trade"] is False
        assert result["daily_pnl_points"] == -35.0
        assert result["risk_multiplier"] == 0.0

    def test_daily_halt_takes_priority_over_consecutive_loss_halt_threshold(self):
        # Even with consecutive losses just below halt_after, daily loss limit
        # breach should still independently trigger DAILY_HALT.
        manager = DynamicRiskManager(make_config(daily_loss_limit_points=10))
        today = [closed_trade(-12, "SL_HIT")]
        db = FakeDatabase(consecutive_losses=1, today_trades=today, recent_trades=today)
        result = manager.evaluate(db)
        assert result["level"] == "DAILY_HALT"
        assert result["can_trade"] is False

    def test_warn_after_losses_triggers_strict_mode(self):
        manager = DynamicRiskManager(make_config(warn_after_losses=2, halt_after_losses=3))
        db = FakeDatabase(consecutive_losses=2)
        result = manager.evaluate(db)
        assert result["level"] == "STRICT"
        assert result["can_trade"] is True
        assert result["min_confidence_required"] == 82
        assert result["min_quality_score"] == 80
        assert result["risk_multiplier"] == 0.5

    def test_strict_min_confidence_never_drops_below_base_min_confidence(self):
        # base min_confidence (risk_settings) is higher than strict_min_confidence
        manager = DynamicRiskManager(
            {
                "risk_settings": {"min_confidence": 90},
                "dynamic_risk_management": {
                    "enabled": True,
                    "warn_after_losses": 2,
                    "halt_after_losses": 3,
                    "strict_min_confidence": 82,
                    "strict_min_quality_score": 80,
                    "strict_risk_multiplier": 0.5,
                    "daily_loss_limit_points": 30,
                },
            }
        )
        db = FakeDatabase(consecutive_losses=2)
        result = manager.evaluate(db)
        assert result["level"] == "STRICT"
        assert result["min_confidence_required"] == 90  # base wins, not 82

    def test_recent_losses_outnumbering_wins_triggers_caution(self):
        manager = DynamicRiskManager(make_config(recent_losses_caution=2))
        recent = [
            closed_trade(-5, "SL_HIT"),
            closed_trade(-5, "SL_HIT"),
            closed_trade(3, "TP1_HIT"),
        ]
        db = FakeDatabase(consecutive_losses=0, today_trades=[], recent_trades=recent)
        result = manager.evaluate(db)
        assert result["level"] == "CAUTION"
        assert result["can_trade"] is True
        assert result["min_confidence_required"] == 75
        assert result["min_quality_score"] == 70
        assert result["risk_multiplier"] == 0.75

    def test_recent_losses_not_outnumbering_wins_stays_normal(self):
        # recent_losses >= threshold but wins are equal/greater -> should NOT be CAUTION
        manager = DynamicRiskManager(make_config(recent_losses_caution=2))
        recent = [
            closed_trade(-5, "SL_HIT"),
            closed_trade(-5, "SL_HIT"),
            closed_trade(3, "TP2_HIT"),
            closed_trade(3, "TP2_HIT"),
        ]
        db = FakeDatabase(consecutive_losses=0, today_trades=[], recent_trades=recent)
        result = manager.evaluate(db)
        assert result["level"] == "NORMAL"

    def test_open_trades_excluded_from_daily_pnl_and_recent_counts(self):
        manager = DynamicRiskManager(make_config(daily_loss_limit_points=30))
        today = [
            {"status": "OPEN", "final_pnl": -100},  # must be ignored, still open
            closed_trade(-5, "SL_HIT"),
        ]
        db = FakeDatabase(consecutive_losses=0, today_trades=today, recent_trades=today)
        result = manager.evaluate(db)
        assert result["daily_pnl_points"] == -5.0
        assert result["level"] == "NORMAL"  # -5 alone doesn't breach -30 limit


class TestShouldBlockSignal:
    def test_disabled_dynamic_risk_never_blocks(self):
        decision = {"decision": "BUY", "confidence": 10, "quality": {"score": 0}}
        dynamic_risk = {"enabled": False, "can_trade": False, "min_confidence_required": 100}
        assert should_block_signal(decision, dynamic_risk) is None

    def test_halted_state_blocks_with_reason(self):
        decision = {"decision": "BUY", "confidence": 95, "quality": {"score": 95}}
        dynamic_risk = {
            "enabled": True,
            "can_trade": False,
            "warnings": ["إيقاف مؤقت: 3 خسائر متتالية"],
        }
        reason = should_block_signal(decision, dynamic_risk)
        assert reason is not None
        assert "خسائر متتالية" in reason

    def test_wait_decision_is_never_blocked_regardless_of_thresholds(self):
        decision = {"decision": "WAIT", "confidence": 0, "quality": {"score": 0}}
        dynamic_risk = {"enabled": True, "can_trade": True, "min_confidence_required": 90}
        assert should_block_signal(decision, dynamic_risk) is None

    def test_confidence_below_requirement_blocks_signal(self):
        decision = {"decision": "BUY", "confidence": 70, "quality": {"score": 90}}
        dynamic_risk = {
            "enabled": True,
            "can_trade": True,
            "min_confidence_required": 82,
            "min_quality_score": 0,
        }
        reason = should_block_signal(decision, dynamic_risk)
        assert reason is not None
        assert "Confidence" in reason

    def test_quality_below_requirement_blocks_signal(self):
        decision = {"decision": "SELL", "confidence": 90, "quality": {"score": 50}}
        dynamic_risk = {
            "enabled": True,
            "can_trade": True,
            "min_confidence_required": 60,
            "min_quality_score": 80,
        }
        reason = should_block_signal(decision, dynamic_risk)
        assert reason is not None
        assert "quality" in reason

    def test_signal_passing_all_thresholds_is_not_blocked(self):
        decision = {"decision": "BUY", "confidence": 90, "quality": {"score": 90}}
        dynamic_risk = {
            "enabled": True,
            "can_trade": True,
            "min_confidence_required": 82,
            "min_quality_score": 80,
        }
        assert should_block_signal(decision, dynamic_risk) is None
