"""Thesis exit and opposite-entry guard mirror the three entry admissions."""
from __future__ import annotations

import json
import types
from pathlib import Path

from agents.open_trades_manager import OpenTradesManager
from scripts.run_analysis import _queue_opposite_exposure_exit
from scripts.run_tick_manager import TickManager
from services.mt5_executor import magic_for
from services.thesis_consensus import evaluate_directional_admission

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def _book(**agents):
    return {name: {"direction": side, "confidence": conf}
            for name, (side, conf) in agents.items()}


def test_three_agents_admit_opposite_exit() -> None:
    result = evaluate_directional_admission("SELL", _book(
        technical=("SELL", 90), classical=("SELL", 82), smc=("SELL", 78),
    ), CONFIG)
    assert result["allow"] is True
    assert result["path"] == "THREE_AGENT_CONSENSUS"


def test_two_agents_plus_macro_admit_opposite_exit() -> None:
    result = evaluate_directional_admission("SELL", _book(
        technical=("SELL", 92), smc=("SELL", 84),
        macro_fundamental=("SELL", 70),
    ), CONFIG)
    assert result["allow"] is True
    assert result["path"] == "TWO_AGENT_MACRO"


def test_two_agents_plus_gemini_admit_opposite_exit() -> None:
    details = _book(technical=("BUY", 92), multitimeframe=("BUY", 86))
    details["gemini"] = {"direction": "BUY", "confidence": 76, "available": True}
    result = evaluate_directional_admission("BUY", details, CONFIG)
    assert result["allow"] is True
    assert result["path"] == "TWO_AGENT_GEMINI"


def test_two_agents_without_external_confirmation_do_not_exit() -> None:
    result = evaluate_directional_admission("SELL", _book(
        technical=("SELL", 92), smc=("SELL", 84),
        macro_fundamental=("BUY", 90),
    ), CONFIG)
    assert result["allow"] is False


def test_thesis_exit_needs_no_candle_when_opposite_admission_is_entry_grade() -> None:
    manager = OpenTradesManager(CONFIG)
    verdict = manager._thesis_exit_review(
        {"id": "BUY1", "type": "BUY", "symbol": "XAU/USD"},
        trade_type="BUY", symbol="XAU/USD", current_price=4390,
        recent_candles=[], hours_open=0.1, pnl_points=-20,
        max_favorable_excursion=0, tp1=4450, entry=4392,
        partial_close=False,
        agent_details=_book(
            technical=("SELL", 90), classical=("SELL", 82), smc=("SELL", 78),
        ),
    )
    assert verdict["exit_now"] is True
    assert verdict["kind"] == "ENTRY_GRADE_OPPOSITE_THESIS"


def test_weak_candle_or_poi_evidence_cannot_exit_without_entry_admission() -> None:
    manager = OpenTradesManager(CONFIG)
    verdict = manager._thesis_exit_review(
        {"id": "BUY1", "type": "BUY", "symbol": "XAU/USD"},
        trade_type="BUY", symbol="XAU/USD", current_price=4390,
        recent_candles=[
            {"high": 4400, "low": 4385, "open": 4398, "close": 4395},
            {"high": 4396, "low": 4370, "open": 4394, "close": 4372},
        ], hours_open=2, pnl_points=-200, max_favorable_excursion=0,
        tp1=4450, entry=4392, partial_close=False,
        agent_details=_book(technical=("SELL", 92)),
    )
    assert verdict["exit_now"] is False
    assert verdict["kind"] == "THESIS_HELD_NO_OPPOSITE_ADMISSION"


class _DB:
    def __init__(self):
        self.updates = []

    def update_trade(self, tid, updates):
        self.updates.append((tid, dict(updates)))


def _trade(tid, side, status):
    return {"id": tid, "symbol": "XAU/USD", "type": side, "status": status}


def test_accepted_opposite_entry_queues_live_exit_and_cancels_old_pending() -> None:
    db = _DB()
    decision = {"trade_id": "NEWBUY", "decision": "BUY", "symbol": "XAU/USD", "reasons": []}
    ids = _queue_opposite_exposure_exit(decision, [
        _trade("LIVESELL", "SELL", "OPEN"),
        _trade("PENDSELL", "SELL", "PENDING"),
        _trade("LIVEBUY", "BUY", "OPEN"),
    ], db, CONFIG)
    assert ids == ["LIVESELL"]
    assert decision["opposite_exit_required"] is True
    assert decision["opposite_trade_ids"] == ["LIVESELL"]
    by_id = {tid: upd for tid, upd in db.updates}
    assert by_id["LIVESELL"]["requested_exit_reason"] == "OPPOSITE_ENTRY_FLIP"
    assert by_id["PENDSELL"]["status"] == "CANCELLED"
    assert "LIVEBUY" not in by_id


def test_tick_manager_blocks_new_entry_until_old_broker_position_is_gone() -> None:
    db = _DB()
    tm = TickManager(CONFIG, database=db)
    old_magic = magic_for("OLDSELL")

    class Executor:
        alive = True

        def __init__(self):
            self.old_open = True

        def _position_by_magic(self, magic):
            return object() if self.old_open and magic == old_magic else None

    ex = Executor()
    row = {"id": "NEWBUY", "opposite_exit_required": True,
           "opposite_trade_ids": ["OLDSELL"]}
    assert tm._flip_dependencies_open(row, ex) == ["OLDSELL"]
    assert db.updates == []
    ex.old_open = False
    assert tm._flip_dependencies_open(row, ex) == []
    assert row["opposite_exit_completed"] is True
    assert db.updates[-1][1]["opposite_exit_required"] is False


def test_legacy_live_hedge_reconciliation_closes_older_side() -> None:
    db = _DB()
    tm = TickManager(CONFIG, database=db)
    messages = []
    tm._notify = messages.append
    buy = {"id": "OLDBUY", "symbol": "XAU/USD", "type": "BUY", "status": "OPEN",
           "entry_price": 4390.0}
    sell = {"id": "NEWSELL", "symbol": "XAU/USD", "type": "SELL", "status": "OPEN",
            "entry_price": 4388.0}
    positions = {
        magic_for("OLDBUY"): types.SimpleNamespace(time=100, ticket=1),
        magic_for("NEWSELL"): types.SimpleNamespace(time=200, ticket=2),
    }

    class Executor:
        def __init__(self):
            self.closed = []
            self.last_close = {}

        def _position_by_magic(self, magic):
            return positions.get(magic)

        def close_position(self, tid, symbol):
            self.closed.append(tid)
            self.last_close = {"price": 4385.0, "volume": 0.1, "ticket": 1}
            positions.pop(magic_for(tid), None)
            return True

    ex = Executor()
    tm._reconcile_opposite_exposure([buy, sell], ex)
    assert ex.closed == ["OLDBUY"]
    assert buy["status"] == "THESIS_EXIT"
    assert sell["status"] == "OPEN"
    assert "kept newer SELL" in messages[0]


def test_shipped_profiles_reserve_two_agent_entries_for_macro_or_gemini() -> None:
    for profile in CONFIG["strategy_profiles"].values():
        assert profile["min_agents_agree"] == 3
        assert profile["min_consensus_confidence"] == 72
    assert CONFIG["trade_management"]["thesis_exit"]["agent_vote"]["mirror_entry_admission"] is True
    assert CONFIG["opposite_entry_guard"]["close_before_new_entry"] is True
