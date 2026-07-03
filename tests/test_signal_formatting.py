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


def _capture_signal(decision: Dict[str, Any]) -> str:
    service = TelegramService({"telegram": {"bot_token": None, "chat_id": None}})
    captured: Dict[str, str] = {}

    def _fake_send(text: str, urgent: bool = False, **_k) -> bool:
        captured["text"] = text
        return True

    service.send_message = _fake_send  # type: ignore[assignment]
    service.send_signal(decision)
    return captured["text"]


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
    assert "SL → entry after +200 pts" in text
    assert "Trail gap 150 pts / step 40 pts" in text
    assert "check 5m" in text


def test_buy_uses_green_header_emoji():
    decision = _full_decision()
    decision["decision"] = "BUY"
    decision["signal"]["type"] = "BUY"
    decision["votes"] = {"BUY": [{"agent": "technical", "confidence": 70}], "WAIT": []}
    decision["ai"] = {"available": True, "signal": "BUY", "confidence": 70}
    text = _capture_signal(decision)
    assert "SIGNAL — BUY" in text and "🟢" in text


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
