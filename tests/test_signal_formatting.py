"""Formatting guards for the Telegram trade-signal message.

These lock in the cleanup of the signal report:
  * no literal backslash-n ("\\n") leaks into the rendered text
  * sections are separated by real newlines
  * empty optional sections (RISK) are dropped, never left as blank gaps
  * agent votes render with directional markers and the external model final gate
"""

from __future__ import annotations

from typing import Any, Dict

from services.telegram_bot import TelegramService


def _capture_text(callable_name: str, *args, **kwargs) -> str:
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    captured: Dict[str, str] = {}

    def _fake_send(text: str, urgent: bool = False, **_k) -> bool:
        captured["text"] = text
        return True

    service.send_message = _fake_send  # type: ignore[assignment]
    getattr(service, callable_name)(*args, **kwargs)
    return captured["text"]


def _capture_signal(decision: Dict[str, Any]) -> str:
    return _capture_text("send_signal", decision)


def _full_decision() -> Dict[str, Any]:
    return {
        "decision": "SELL",
        "confidence": 74,
        "current_price": 4130.14,
        "session_info": {"current_session": "Early Asia to Late NY", "session_quality": "HIGH"},
        "run_source": "manual",
        "quality": {"grade": "A", "score": 87},
        "signal": {
            "type": "SELL",
            "entry": {"low": 4129.49, "high": 4130.79, "price": 4130.14},
            "stop_loss": 4150.14, "tp1": 4103.47, "tp2": 4083.47,
        },
        "risk": {
            "stop_loss": {"distance_points": 200},
            "take_profit": {"tp1": {"rr_ratio": 1.33}, "tp2": {"rr_ratio": 2.33}},
        },
        "votes": {
            "SELL": [{"agent": "classical", "confidence": 82}, {"agent": "multitimeframe", "confidence": 67}],
            "WAIT": [{"agent": "technical"}, {"agent": "smc"}, {"agent": "price_action"}],
        },
        "ai": {
            "available": True, "signal": "SELL", "confidence": 74,
            "entry_reason": "Alignment with daily bias and a bearish order block",
            "risk_notes": "Moderate-high volatility; support near 4092.16",
            "invalidation": "Price breaking above 4146.45",
        },
        "daily_bias": {"bias": "BEARISH", "confidence": 95},
        "dynamic_risk": {"level": "NORMAL"},
        "decision_mode": "5-Agent Weighted Consensus",
        "trading_mode": "paper", "paper_trading": True,
        "trade_id": "TRADE_TEST_FMT",
    }


def test_no_literal_backslash_n_in_message():
    """Regression: the old code emitted '\\n' (escaped) instead of a newline."""
    text = _capture_signal(_full_decision())
    assert "\\n" not in text, "Literal backslash-n leaked into the signal text"


def test_risk_note_and_invalidation_on_separate_lines():
    text = _capture_signal(_full_decision())
    lines = text.split("\n")
    risk_line = next((l for l in lines if "Risk note:" in l), "")
    inval_line = next((l for l in lines if "Invalidation:" in l), "")
    assert risk_line and inval_line
    # They must be different physical lines, not concatenated together.
    assert risk_line != inval_line
    assert "Invalidation:" not in risk_line


def test_footer_pieces_on_separate_lines():
    text = _capture_signal(_full_decision())
    assert "not financial advice." in text
    # The id line must not be glued onto the disclaimer line.
    disclaimer_line = next(l for l in text.split("\n") if "not financial advice." in l)
    assert "TRADE_TEST_FMT" not in disclaimer_line


def test_empty_risk_section_dropped_without_gap():
    decision = _full_decision()
    decision["ai"] = {"available": True, "signal": "SELL", "confidence": 74}
    decision["daily_bias"] = {"bias": "NEUTRAL"}
    text = _capture_signal(decision)
    assert "RISK" not in text.split("AGENT VOTES")[-1].split("━━━━━━━━━━━━━━━━━━━━━")[-1]
    # No triple blank lines anywhere.
    assert "\n\n\n" not in text


def test_agent_votes_have_direction_markers():
    decision = _full_decision()
    decision["agent_details"] = {
        "technical": {"label": "Technical", "direction": "WAIT", "confidence": 55, "signals": ["RSI neutral"]},
        "classical": {"label": "Classical", "direction": "SELL", "confidence": 82, "signals": ["Bearish pattern"]},
        "smc": {"label": "SMC", "direction": "WAIT", "confidence": 45, "signals": ["Structure bearish"]},
        "price_action": {"label": "Price Action", "direction": "SELL", "confidence": 67, "signals": ["Bearish rejection"]},
        "multitimeframe": {"label": "Multitimeframe", "direction": "SELL", "confidence": 70, "signals": ["4H bearish"]},
    }
    text = _capture_signal(decision)
    assert "AGENT VOTES" in text
    # Directional dots present: red for SELL, yellow for WAIT.
    assert "🔴" in text and "🟡" in text


def test_signal_includes_trade_management_rule():
    text = _capture_signal(_full_decision())
    assert "Management:" in text
    # Numbers must come from the profile the engine will actually apply, not a
    # hardcoded default. _full_decision() carries no setup type, so it falls
    # back to default_profile (150/40/150). Protection is wired to TP1 since
    # 2026-08-04; the profile's early trigger is named as the earlier arm.
    assert "SL → entry at TP1" in text
    assert "may arm earlier at +150 pts" in text
    assert "Trail gap 150 pts / step 40 pts" in text
    assert "check 5m" in text


def test_signal_management_text_matches_the_engine_profile():
    """A reversal setup must not advertise the default profile's numbers.

    Regression guard: the management line was hardcoded to 150/40/150 while
    the engine applied 120/30/100 for LIQUIDITY_REVERSAL, so every reversal
    signal published three wrong numbers.
    """
    decision = _full_decision()
    decision["setup_type"] = "LIQUIDITY_REVERSAL"
    decision["setup_context"] = {**(decision.get("setup_context") or {}), "setup_type": "LIQUIDITY_REVERSAL"}
    text = _capture_signal(decision)
    # 2026-08-07: trailing unified to 150/40 for ALL profiles; only the
    # early-BE trigger still differs per profile (reversal +100).
    assert "may arm earlier at +100 pts" in text
    assert "Trail gap 150 pts / step 40 pts" in text


def test_pending_order_signal_explicitly_says_not_active_yet():
    decision = _full_decision()
    decision["signal"]["entry_kind"] = "LIMIT"
    decision["signal"]["order_type"] = "SELL_LIMIT"
    decision["signal"]["entry"]["kind"] = "LIMIT"
    decision["signal"]["entry"]["order_type"] = "SELL_LIMIT"
    decision["signal"]["entry"]["current_price"] = 4029.37
    decision["signal"]["entry"]["distance_points"] = 112.3
    decision["signal"]["entry"]["price"] = 4040.60
    text = _capture_signal(decision)
    assert "Pending order — not active yet" in text
    assert "pts to activation" in text
    assert "activates only when" in text


def test_buy_uses_green_header_emoji():
    decision = _full_decision()
    decision["decision"] = "BUY"
    decision["signal"]["type"] = "BUY"
    decision["votes"] = {"BUY": [{"agent": "technical", "confidence": 70}], "WAIT": []}
    decision["ai"] = {"available": True, "signal": "BUY", "confidence": 70}
    text = _capture_signal(decision)
    assert "XAU/USD — BUY" in text and "🟢" in text


def test_signal_can_show_execution_switch_reason():
    decision = _full_decision()
    decision["setup_context"] = {"setup_type": "STRUCTURE_CONTINUATION"}
    decision["adaptive_execution"] = {
        "action": "PROMOTE_TO_MARKET",
        "reason": "market moved 100 pts without fill; promote to market while remaining RR 1.60 is still acceptable",
    }
    text = _capture_signal(decision)
    assert "Execution switch:" in text
    assert "Promote To Market" in text
    assert "Execution reason:" in text


def test_pending_cancelled_event_surfaces_specific_reason():
    trade = {
        "id": "TRADE_PENDING_X",
        "symbol": "XAU/USD",
        "type": "SELL",
        "status": "PENDING",
        "entry_price": 4040.60,
    }
    evaluation = {
        "old_status": "PENDING",
        "new_status": "CANCELLED",
        "hours_open": 0.5,
        "pending_distance_points": 12.0,
        "updates": {
            "reasons": ["Auto market conversion blocked: recent closed WIN trade in same zone; no materially new POI"],
        },
    }
    text = _capture_text("send_trade_events", trade, ["PENDING_CANCELLED"], 3992.76, 0.0, evaluation)
    assert "Cancellation reason:" in text
    assert "Auto market conversion blocked" in text
    assert "mapped execution conditions were no longer valid" in text


def test_pending_activation_can_show_delayed_touch_revalidation_reason():
    trade = {
        "id": "TRADE_PENDING_Y",
        "symbol": "XAU/USD",
        "type": "SELL",
        "status": "PENDING",
        "entry_price": 4006.00,
    }
    evaluation = {
        "old_status": "PENDING",
        "new_status": "OPEN",
        "hours_open": 4.0,
        "pending_distance_points": 0.0,
        "updates": {
            "entry_price": 4003.00,
            "activation_reason": "Delayed touch revalidated (STALE)",
        },
        "scenario_governor": {"action": "CANCELLED_SIBLINGS_ON_ACTIVATION", "cancelled_ids": ["P2"]},
        "plan_execution_context": {
            "story": "Main area filled. Secondary area is no longer needed and will be cancelled.",
            "pending_sibling_roles": ["STANDBY"],
        },
    }
    text = _capture_text("send_trade_events", trade, ["ORDER_FILLED"], 4003.00, 0.0, evaluation)
    assert "Activation:" in text
    assert "touched its trigger and is now live" in text
    assert "Activation review:" in text
    assert "Delayed touch revalidated" in text
    assert "Scenario family:" in text
    assert "1 sibling pending order(s) cancelled" in text
    assert "Execution story:" in text
    assert "Cancelled leg(s):" in text
    assert "STANDBY" in text


def test_pending_market_conversion_message_is_explicit_not_touch_fill():
    trade = {
        "id": "TRADE_PENDING_Z",
        "symbol": "XAU/USD",
        "type": "SELL",
        "status": "PENDING",
        "entry_price": 4134.79,
    }
    evaluation = {
        "old_status": "PENDING",
        "new_status": "OPEN",
        "hours_open": 3.0,
        "pending_distance_points": 0.0,
        "updates": {
            "entry_price": 4118.97,
            "activation_reason": "Auto market conversion after waiting 36 cycles without fill",
        },
    }
    text = _capture_text("send_trade_events", trade, ["ORDER_FILLED"], 4118.97, 0.0, evaluation)
    assert "converted to MARKET and is now live" in text
    assert "touched its trigger and is now live" not in text
    assert "Auto market conversion" in text


def test_planner_led_signal_shows_setup_quality_and_planner_score_separately():
    decision = {
        "decision": "BUY",
        "confidence": 79.0,
        "current_price": 4086.89,
        "symbol": "XAU/USD",
        "entry_mode": "session_plan_ladder",
        "entry_path": 3,
        "quality": {"grade": "A+", "score": 100.0},
        "planner_quality": {"grade": "C", "score": 67.7},
        "signal": {
            "type": "BUY",
            "entry": {"price": 4055.92, "low": 4055.81, "high": 4056.03, "kind": "LIMIT", "order_type": "BUY_LIMIT", "current_price": 4086.89, "distance_points": 310.0},
            "stop_loss": 4015.92,
            "tp1": 4105.92,
            "tp2": 4145.92,
            "rr_ratio": 2.25,
            "entry_kind": "LIMIT",
            "order_type": "BUY_LIMIT",
        },
        "session_plan": {
            "plan_ready": True,
            "session_bias": "BUY",
            "planner_confidence": 67.7,
            "planner_grade": "C",
            "authority_state": "CONFIRMED",
            "execution_preference": "LADDER_PENDING",
        },
        "trade_id": "TRADE_QUAL_FIX",
    }
    text = _capture_signal(decision)
    assert "Setup quality: A+ 100.0" in text
    assert "Planner score: C 67.7" in text


def test_pending_governance_can_announce_replacement_blocked():
    text = _capture_text(
        "send_pending_governance",
        {
            "action": "KEEP_EXISTING_PENDING",
            "reason": "replacement blocked: no materially new POI",
            "old_trade_id": "TRADE_OLD_12345678",
            "old_context": {"thesis_dominance_score": 61, "return_probability_score": 54},
            "new_context": {"thesis_dominance_score": 70, "return_probability_score": 63},
        },
        symbol="XAU/USD",
        side="SELL",
    )
    assert "Pending Replacement Blocked" in text
    assert "no materially new POI" in text


def test_thesis_exit_message_is_labeled_clearly() -> None:
    trade = {
        "id": "TRADE_EXIT_1",
        "symbol": "XAU/USD",
        "type": "SELL",
        "status": "OPEN",
        "entry_price": 4120.00,
    }
    evaluation = {
        "old_status": "OPEN",
        "new_status": "MANUAL_CLOSE",
        "updates": {
            "close_price": 4130.00,
            "final_pnl": -100.0,
            "reasons": ["Automatic thesis exit: bullish continuation reclaimed the breakdown"],
        },
    }
    text = _capture_text("send_trade_events", trade, ["MANUAL_CLOSE"], 4130.00, -100.0, evaluation)
    assert "Thesis Exit" in text
    assert "Exit reason:" in text
    assert "reclaimed the breakdown" in text


def test_thesis_scale_out_message_is_labeled_clearly() -> None:
    trade = {
        "id": "TRADE_EXIT_2",
        "symbol": "XAU/USD",
        "type": "SELL",
        "status": "OPEN",
        "entry_price": 4120.00,
    }
    evaluation = {
        "old_status": "OPEN",
        "new_status": "PARTIAL",
        "updates": {
            "stop_loss": 4120.00,
            "reasons": ["Automatic thesis scale-out: opposing BUY POI rejection from target_liquidity near 4087.40"],
        },
    }
    text = _capture_text("send_trade_events", trade, ["THESIS_SCALE_OUT", "MOVE_SL_TO_BE"], 4094.00, 260.0, evaluation)
    assert "Thesis Risk Scale-Out" in text
    assert "Exit reason:" in text
    assert "scale-out" in text.lower()
    assert "SL Moved to Breakeven" not in text  # title should be the scale-out event, not BE


def test_revalidation_block_message_is_clear():
    text = _capture_text(
        "send_revalidation_block",
        symbol="XAU/USD",
        side="SELL",
        entry_price=3992.76,
        reason="Post-exit revalidation blocked: recently closed WIN trade in same zone. Revalidation: no materially new POI.",
    )
    assert "Re-entry Blocked" in text
    assert "Post-exit revalidation blocked" in text
    assert "materially new thesis" in text


def test_scenario_governance_message_for_family_replacement_is_clear():
    text = _capture_text(
        "send_scenario_governance",
        {
            "action": "REPLACE_PENDING_FAMILY",
            "reason": "new session-plan family is stronger or the old family is stale",
            "old_scenario_id": "SCENARIO::OLD",
            "new_scenario_id": "SCENARIO::NEW",
            "cancelled_ids": ["P1", "P2"],
        },
        symbol="XAU/USD",
        side="SELL",
    )
    assert "Scenario Family Replaced" in text
    assert "SCENARIO::OLD" in text
    assert "SCENARIO::NEW" in text
    assert "Pending Orders Cancelled" in text


# ── Invalidation deduplication (must not just repeat the stop loss) ─────────
def test_invalidation_hidden_when_same_as_stop():
    d = _full_decision()
    d["signal"]["stop_loss"] = 4121.05
    d["ai"]["invalidation"] = "Price close above 4121.05"
    text = _capture_signal(d)
    assert "Invalidation" not in text


def test_invalidation_shown_when_different_level():
    d = _full_decision()
    d["signal"]["stop_loss"] = 4121.05
    d["ai"]["invalidation"] = "Price close above 4135.00"
    text = _capture_signal(d)
    assert "Invalidation" in text and "4135" in text


def test_invalidation_shown_when_structural_condition():
    d = _full_decision()
    d["signal"]["stop_loss"] = 4121.05
    d["ai"]["invalidation"] = "Close above the bearish order block / structure break"
    text = _capture_signal(d)
    assert "Invalidation" in text


def test_smc_liquidity_terms_are_subscriber_friendly():
    """SMC buy-side/sell-side are liquidity terms, not trade directions.

    The Telegram message should say 'sweep above highs' / 'sweep below lows'
    so subscribers do not confuse a bearish SELL setup with a BUY signal.
    """
    d = _full_decision()
    d["decision"] = "SELL"
    d["signal"]["type"] = "SELL"
    d["votes"] = {
        "SELL": [{"agent": "smc", "confidence": 82}],
        "WAIT": [],
    }
    d["agent_details"] = {
        "smc": {
            "label": "SMC",
            "direction": "SELL",
            "confidence": 82,
            "signals": [
                "Market structure is bearish",
                "Buy-side liquidity sweep detected (STRONG) - bearish after sweep",
            ],
        }
    }
    text = _capture_signal(d)
    assert "Buy-side liquidity sweep" not in text
    assert "Sell-side liquidity sweep" not in text
    assert "Sweep above recent highs detected (STRONG) - bearish reversal context" in text


def test_votes_distinguish_qualified_agents_and_opposing_macro():
    """The header claims N qualified agents; the list must show exactly N.

    A 67% vote used to render identically to an 84% one, so a message saying
    "3 qualified agents" displayed four green ticks. Macro also reports
    confidence in its own direction, which may oppose the published side.
    """
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    lines = service._votes_lines({
        "decision": "BUY",
        "agent_details": {
            "technical": {"label": "Technical", "direction": "BUY", "confidence": 92},
            "smc": {"label": "SMC", "direction": "BUY", "confidence": 76},
            "price_action": {"label": "Price Action", "direction": "BUY", "confidence": 84},
            "multitimeframe": {"label": "Multi-Timeframe", "direction": "BUY", "confidence": 67},
            "macro_fundamental": {"label": "Macro / Fundamental", "direction": "SELL", "confidence": 69},
        },
    })
    text = "\n".join(lines)
    assert text.count("✅") == 3
    assert "below 70% threshold" in text
    assert "opposes this BUY" in text


def test_votes_fall_back_to_summary_when_agent_has_no_signals():
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    lines = service._votes_lines({
        "decision": "BUY",
        "agent_details": {
            "multitimeframe": {"label": "Multi-Timeframe", "direction": "BUY", "confidence": 80,
                               "signals": [], "summary": "H1 and H4 aligned bullish"},
        },
    })
    assert any("H1 and H4 aligned bullish" in ln for ln in lines)


def test_progress_report_between_targets_shows_tp2_remaining():
    from services.telegram_bot import TelegramService
    bot = TelegramService({"telegram": {}})
    trade = {"type": "BUY", "entry_price": 4261.23, "tp1": 4300.0, "tp2": 4400.0}
    # price between TP1 and TP2 -> TP1 done, show TP2 % and pts left
    line = bot._progress_report(trade, {"current_price": 4330.0}, {})
    assert line.startswith("TP1 ✓")
    assert "TP2" in line and "pts left" in line


def test_progress_report_tp2_hit_shows_completed():
    from services.telegram_bot import TelegramService
    bot = TelegramService({"telegram": {}})
    trade = {"type": "BUY", "entry_price": 4261.23, "tp1": 4300.0, "tp2": 4400.0}
    line = bot._progress_report(trade, {"close_price": 4400.0}, {})
    assert "completed" in line and "TP1 ✓" in line and "TP2 ✓" in line


def test_progress_report_below_tp1_never_zero_when_in_profit():
    from services.telegram_bot import TelegramService
    bot = TelegramService({"telegram": {}})
    trade = {"type": "BUY", "entry_price": 4261.23, "tp1": 4300.0, "tp2": 4400.0}
    line = bot._progress_report(trade, {"close_price": 4269.71}, {})
    assert "TP1 22%" in line and "TP2 6%" in line
    assert "0%" not in line.replace("22%", "").replace("6%", "")
