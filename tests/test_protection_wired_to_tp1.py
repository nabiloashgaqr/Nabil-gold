"""Protection is wired to TP1 — and the ladder must act accordingly.

The contract this file pins (operator decision, 2026-08-04):

  * The stop moves to entry when TP1 is hit, whatever TP1's value
    (trade_management.auto_move_sl_to_entry_after_tp1, already the manager's
    behaviour via `auto_be`, gated by min_breakeven_rr = 0.5R of travel).
  * min RR to TP1  = 0.8  (risk_settings.min_tp1_rr)
  * min RR to TP2  = 1.5  (risk_settings.min_rr_ratio)

FAULT INJECTION, part 1 (the validator): revert rule 3 in
`validate_signal_before_send` to its old text -- a distance trigger beyond TP1
"is not reachable before tp1" -- and the positive test below fails again:
the A+ 97.8% Asia Morning geometry (TP1 140.5 pts, +150-pt trigger) is
refused and no order is created. That refusal was the measured killer of
2026-08-04: the gate passed, pricing passed, and final validation killed the
leg because it judged a promise the engine no longer exclusively makes.

FAULT INJECTION, part 2 (the audit): delete the `_ladder_stop(...)` recording
calls in the order loop of `scripts/run_analysis.py` and the audit test fails
-- the refusal would again read "gate allowed; stop not recorded".
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

import scripts.run_analysis as ra
from services.database import DatabaseService
from utils.helpers import load_config


class _Telegram:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_signal(self, decision: dict) -> bool:
        self.sent.append(decision)
        return True

    def send_scenario_governance(self, *args, **kwargs) -> bool:
        return True


def _db(tmp_path: Path) -> DatabaseService:
    db = DatabaseService({"database": {"url": None, "key": None, "local_fallback_file": str(tmp_path / "trades.json")}})
    db.local_path = tmp_path / "trades.json"
    return db


def _config(**overrides) -> dict:
    cfg = load_config()
    cfg["trade_management"] = dict(cfg.get("trade_management") or {})
    cfg["trade_management"]["early_breakeven_points"] = 150
    cfg["trade_management"]["auto_move_sl_to_entry_after_tp1"] = True
    cfg["trade_management"]["min_breakeven_rr"] = 0.5
    cfg["risk_settings"] = dict(cfg.get("risk_settings") or {})
    cfg["risk_settings"].update({
        "min_sl_distance_points": 400,
        "min_rr_ratio": 1.5,
        "min_tp1_rr": 0.8,
        "dynamic_sl_floor": {"enabled": True, "structural_multiplier": 3.0,
                             "min_points": 150, "max_points": 400},
    })
    cfg["order_execution"] = {"enabled": True, "entry_style": "hybrid",
                              "market_threshold_points": 30,
                              "pending_threshold_points": 20}
    cfg["session_planner"] = dict(cfg.get("session_planner") or {})
    cfg["session_planner"]["create_pending_orders_from_plan"] = True
    cfg["split_execution"] = {"enabled": False}
    for key, value in overrides.items():
        cfg[key] = value
    return cfg


def _primary_candidate(tp1: float) -> dict:
    # The 2026-08-04 Asia Morning map, verbatim geometry.
    return {
        "id": "CAND::PRIMARY",
        "state_key": "STATE::PRIMARY",
        "direction": "BUY",
        "setup_type": "FAILED_RECLAIM_CONTINUATION",
        "setup_state": "ENTRY_ARMED",
        "lead_agent": "smc",
        "selection_role": "PRIMARY",
        "selection_rank": 1,
        "entry_price": 4060.95,
        # Structural (pre-floor) stop; the 150-point floor carries it to the
        # published invalidation 4045.95, exactly as the card printed it.
        "stop_loss": 4057.83,
        "target_price": 4090.00,
        "target_liquidity": 4090.00,
        "tp1": tp1,
        "poi_type": "order_block",
        "poi_zone": {"top": 4063.95, "bottom": 4057.95},
        "poi_low": 4057.95,
        "poi_high": 4063.95,
        "poi_quality_score": 97.8,
        "quality_score": 97.8,
        "quality_grade": "A+",
        "trigger_state": "FAILED_RECLAIM_CONFIRMED",
        "trigger_ready": True,
    }


def _decision(tp1: float) -> dict:
    return {
        # Live consensus read WAIT that morning; the gate falls back to the
        # mapped bias and must still count the agents on their own.
        "decision": "WAIT",
        "symbol": "XAU/USD",
        "current_price": 4061.0,
        "confidence": 0.0,
        "agent_details": {
            "technical": {"label": "Technical", "direction": "WAIT", "confidence": 41},
            "classical": {"label": "Classical", "direction": "BUY", "confidence": 74},
            "smc": {"label": "SMC", "direction": "BUY", "confidence": 76},
            "price_action": {"label": "Price Action", "direction": "BUY", "confidence": 84},
            "multitimeframe": {"label": "Multi-Timeframe", "direction": "BUY", "confidence": 92},
        },
        "session_plan": {
            "plan_ready": True,
            "plan_id": "PLAN::SCENARIO::XAU/USD::20260804::ASIA::BUY::FAILED_RECLAIM_CONTINUATION",
            "scenario_id": "SCENARIO::XAU/USD::20260804::ASIA::BUY::FAILED_RECLAIM_CONTINUATION",
            "symbol": "XAU/USD",
            "session_bias": "BUY",
            "scenario_type": "FAILED_RECLAIM_CONTINUATION",
            "planner_confidence": 97.8,
            "planner_grade": "A+",
            "poi_classification": "EXTREME_POI",
            "extreme_poi": True,
            "execution_preference": "LADDER_PENDING",
            "execution_readiness": {"state": "MARKET_EXECUTION_READY",
                                    "reason": "trigger FAILED_RECLAIM_CONFIRMED with 4 execution-support agents"},
            "primary_poi": _primary_candidate(tp1),
            "standby_poi": {},
        },
    }


def test_the_blocked_a_plus_map_now_trades(tmp_path: Path) -> None:
    """TP1 4075.00 sits 140.5 pts from the reference entry — inside the old
    +150-pt trigger, which is exactly why the map died on 2026-08-04. With
    protection wired to TP1, the same card becomes an order."""
    ra._LAST_LADDER_STOP.clear()
    db = _db(tmp_path)
    telegram = _Telegram()

    decision = _decision(tp1=4075.00)
    cfg = _config()

    gate = ra._planner_execution_gate(decision, cfg)
    assert gate.get("allow") is True
    assert gate.get("support_count") == 4

    created = ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, telegram, cfg)

    assert created == 1, f"expected the wired protection to unblock the map; stop={ra._LAST_LADDER_STOP}"
    assert len(telegram.sent) == 1
    sig = telegram.sent[0].get("signal", {})
    assert sig.get("tp1") == 4075.0 or abs(float(sig.get("tp1") or 0) - 4075.0) < 1.0
    assert ra._LAST_LADDER_STOP == {} or not ra._LAST_LADDER_STOP


def test_duplicate_filter_refusal_is_recorded_in_the_audit(tmp_path: Path) -> None:
    """The wiring removed the breakeven refusal, not auditing itself: when a
    later check still blocks the leg, the stop must be named. A seeded OPEN
    BUY in the zone trips the duplicate filter inside the order loop — one of
    the exits that used to return silently ("stop not recorded")."""
    import json as _json
    from datetime import datetime, timezone

    ra._LAST_LADDER_STOP.clear()
    db = _db(tmp_path)
    telegram = _Telegram()

    db.local_path.write_text(_json.dumps([{
        "id": "TRADE_20260804_055500_111111_seed0001",
        "symbol": "XAU/USD",
        "type": "BUY",
        "side": "BUY",
        "status": "OPEN",
        "entry_price": 4060.50,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "signal_snapshot": {"signal": {"entry": {"price": 4060.50}}},
    }]))

    decision = _decision(tp1=4075.00)
    cfg = _config()

    created = ra._execute_session_plan_ladder(decision, {"symbol": "XAU/USD"}, [], db, telegram, cfg)

    assert created == 0
    assert telegram.sent == []
    assert ra._LAST_LADDER_STOP, "ladder stopped silently; the audit will not name the refusal"
    reason = str(ra._LAST_LADDER_STOP.get("reason") or "")
    assert "blocked by duplicate filter" in reason, reason


def test_legacy_distance_only_protection_still_refuses_the_close_tp1() -> None:
    """auto_be off = the engine promises only the +150 trigger. Then a TP1
    nearer than 150 pts really is an unkeepable promise, and the old check
    must still fire."""
    cfg = _config()
    cfg["trade_management"]["auto_move_sl_to_entry_after_tp1"] = False
    signal = {
        "decision": "BUY",
        "symbol": "XAU/USD",
        "current_price": 4061.0,
        "signal": {"entry": {"price": 4060.95}, "stop_loss": 4045.95,
                   "tp1": 4075.00, "tp2": 4090.00},
    }
    violations = ra.validate_signal_before_send(signal, cfg, [])
    assert any("not reachable before" in v for v in violations), violations


def test_the_rr_floors_the_operator_fixed_are_enforced() -> None:
    """min RR to TP1 = 0.8 and to TP2 = 1.5 — the numbers of this round,
    pinned in config and alive in the validator."""
    cfg = load_config()
    assert float(cfg["risk_settings"]["min_tp1_rr"]) == 0.8
    assert float(cfg["risk_settings"]["min_rr_ratio"]) == 1.5

    # TP2 at 1.2R violates the 1.5 floor even with a healthy TP1.
    signal = {
        "decision": "BUY",
        "symbol": "XAU/USD",
        "current_price": 4061.0,
        "signal": {"entry": {"price": 4060.00}, "stop_loss": 4045.00,
                   "tp1": 4073.00, "tp2": 4078.00},
    }
    violations = ra.validate_signal_before_send(signal, _config(), [])
    assert any("tp2 is only" in v for v in violations), violations


def test_protection_message_names_the_wired_promise() -> None:
    """What the alert says must match the wiring: SL -> entry AT TP1, not
    'after +150 pts' when TP1 sits 140 pts away."""
    from services.telegram_bot import TelegramService

    class _Capture(TelegramService):
        def __init__(self, config):
            super().__init__(config)
            self.bot_token = None
            self.sent: list[str] = []

        def send_message(self, text: str, **_kwargs) -> bool:
            self.sent.append(text)
            return True

    cfg = load_config()
    telegram = _Capture(cfg)
    telegram.send_signal({
        "decision": "BUY",
        "symbol": "XAU/USD",
        "current_price": 4061.0,
        "confidence": 97.8,
        "signal": {"entry": {"price": 4060.95}, "stop_loss": 4045.95,
                   "tp1": 4075.00, "tp2": 4090.00, "order_type": "BUY_MARKET"},
    })
    text = telegram.sent[0]
    line = next(l for l in text.split("\n") if "Protection" in l)
    assert "SL → entry at TP1" in line, line
    assert "0.50R" in line, line
