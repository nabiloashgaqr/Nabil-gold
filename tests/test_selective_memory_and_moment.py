"""The two advantages a discretionary analyst had that the system did not.

A human recalls that *this* setup, in *this* session, failed the last three
times -- and they size differently at the London open than in a dead Asia
range. The system stored every fact needed for both judgements and consulted
neither at the moment of execution.

Both additions are deliberately one-directional:

  - memory may lower confidence on a poor pattern, never raise it on a good
    one, because a memory that inflates conviction is how overfitting reaches
    production;
  - moment quality may only shrink position size, never open or refuse a
    trade, because a sizing input with veto power becomes a second admission
    gate that nothing tests.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import scripts.run_analysis as ra
from agents.risk_management_agent import _safe_moment
from services.moment_quality import MomentQualityService
from services.setup_performance import SetupPerformanceService

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


class _Database:
    def __init__(self, trades=None):
        self._trades = trades or []

    def get_recent_trades(self, limit: int = 60):
        return self._trades[:limit]


def _trade(status, pnl, setup="LIQUIDITY_REVERSAL", session="Asia Morning"):
    return {"status": status, "final_pnl": pnl, "setup_type": setup, "session_label": session}


def _signal(setup="LIQUIDITY_REVERSAL", session="Asia Morning", confidence=80.0):
    return {
        "decision": "BUY",
        "setup_type": setup,
        "confidence": confidence,
        "session_info": {"current_session": session},
    }


def _review(trades, config=None):
    return SetupPerformanceService(_Database(trades), config or CONFIG).review(_signal())


# --- 6.1 selective memory -----------------------------------------------

def test_a_failing_pattern_costs_confidence() -> None:
    trades = [_trade("SL_HIT", -150) for _ in range(4)] + [_trade("TP2_HIT", 300)]

    review = _review(trades)

    assert review["win_rate_pct"] == 20.0
    assert review["confidence_penalty"] > 0
    assert "below the 40% bar" in review["reason"]


def test_a_winning_pattern_is_never_rewarded() -> None:
    """Only ever conservative: no penalty, and no bonus either."""
    trades = [_trade("TP2_HIT", 300) for _ in range(4)] + [_trade("SL_HIT", -150)]

    review = _review(trades)

    assert review["win_rate_pct"] == 80.0
    assert review["confidence_penalty"] == 0.0
    assert review["veto"] is False


def test_a_thin_sample_is_not_judged() -> None:
    review = _review([_trade("SL_HIT", -150), _trade("SL_HIT", -150)])

    assert review["confidence_penalty"] == 0.0
    assert "need" in review["reason"]


def test_penalty_scales_with_how_bad_the_record_is() -> None:
    """A consistently losing pattern must cost more than a merely weak one."""
    awful = _review([_trade("SL_HIT", -150) for _ in range(6)])          # 0%
    weak = _review(
        [_trade("SL_HIT", -150) for _ in range(4)] + [_trade("TP2_HIT", 300) for _ in range(2)]
    )                                                                     # 33%

    assert weak["win_rate_pct"] == 33.3
    assert awful["confidence_penalty"] > weak["confidence_penalty"] > 0


def test_a_record_exactly_on_the_bar_is_not_penalised() -> None:
    """40% is the bar, not a failure of it."""
    at_bar = _review(
        [_trade("SL_HIT", -150) for _ in range(3)] + [_trade("TP2_HIT", 300) for _ in range(2)]
    )

    assert at_bar["win_rate_pct"] == 40.0
    assert at_bar["confidence_penalty"] == 0.0


def test_a_profitable_trailed_stop_counts_as_a_win() -> None:
    """SL_HIT can be a profitable trailing exit; the label alone misreports it."""
    trades = [_trade("SL_HIT", 120) for _ in range(5)]

    assert _review(trades)["win_rate_pct"] == 100.0


def test_other_setups_and_sessions_do_not_pollute_the_sample() -> None:
    trades = (
        [_trade("SL_HIT", -150, setup="ORDER_BLOCK_PULLBACK") for _ in range(5)]
        + [_trade("TP2_HIT", 300) for _ in range(4)]
    )

    assert _review(trades)["win_rate_pct"] == 100.0


def test_it_falls_back_to_all_sessions_when_the_session_sample_is_thin() -> None:
    trades = (
        [_trade("SL_HIT", -150, session="London")] * 5
        + [_trade("TP2_HIT", 300, session="Asia Morning")]
    )

    review = _review(trades)
    assert review["scope"] == "all sessions"
    assert review["matched"] == 6


def test_a_broken_history_cannot_stop_a_trade() -> None:
    class _Broken:
        def get_recent_trades(self, **_kwargs):
            raise RuntimeError("supabase down")

    review = SetupPerformanceService(_Broken(), CONFIG).review(_signal())
    assert review["confidence_penalty"] == 0.0
    assert review["veto"] is False


def test_memory_is_consulted_before_the_signal_is_sent() -> None:
    source = inspect.getsource(ra._run_analysis_for_config)
    memory = source.find("SetupPerformanceService(")
    send = source.find("telegram.send_signal(")

    assert memory != -1, "selective memory is not wired into the cycle"
    assert memory < send, "memory must be consulted before delivery"


# --- 6.3 moment quality --------------------------------------------------

def _moment(session_quality="HIGH", news_status="SAFE", volatility="NORMAL"):
    return MomentQualityService(CONFIG).review({
        "session": {"session_quality": session_quality},
        "news": {"market_status": news_status},
        "technical": {"market_regime": {"volatility_regime": volatility}},
    })


def test_clean_conditions_leave_size_untouched() -> None:
    assert _moment()["multiplier"] == 1.0


def test_a_quiet_session_reduces_size() -> None:
    result = _moment(session_quality="LOW", volatility="LOW")

    assert result["multiplier"] < 1.0
    assert "low-quality session" in result["summary"]


def test_news_risk_reduces_size() -> None:
    assert _moment(news_status="DANGER")["multiplier"] < 1.0


def test_the_multiplier_is_bounded() -> None:
    """Stacked penalties must not collapse a position to nothing."""
    worst = _moment(session_quality="LOW", news_status="DANGER", volatility="EXTREME")

    assert worst["multiplier"] >= 0.5
    assert worst["multiplier"] < 1.0


def test_moment_quality_never_exceeds_one() -> None:
    """Good conditions must not inflate risk beyond the configured base."""
    for quality in ("HIGH", "MEDIUM", "LOW"):
        assert _moment(session_quality=quality)["multiplier"] <= 1.0


def test_a_malformed_payload_is_treated_as_neutral() -> None:
    assert MomentQualityService(CONFIG).review({})["multiplier"] == 1.0
    assert _safe_moment({"multiplier": "nonsense"}) == 1.0
    assert _safe_moment({"multiplier": 0}) == 1.0
    assert _safe_moment({"multiplier": 5.0}) == 1.0
    assert _safe_moment({"multiplier": 0.75}) == 0.75


def test_moment_quality_only_touches_sizing() -> None:
    """It must not appear in any admission or rejection path."""
    source = inspect.getsource(ra._run_analysis_for_config)
    assert "MomentQualityService" not in source, (
        "moment quality reached the decision cycle; it is a sizing input only"
    )

    risk_source = Path(ROOT / "agents" / "risk_management_agent.py").read_text(encoding="utf-8")
    usage = risk_source.find("MomentQualityService(self.config).review(")
    sizing = risk_source.find("self._position_size(")
    assert usage != -1 and usage < sizing
    assert "approved = " not in risk_source[usage:sizing], (
        "moment quality must not participate in the approval decision"
    )
