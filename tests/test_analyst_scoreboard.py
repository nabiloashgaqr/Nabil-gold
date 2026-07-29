"""Did we beat the analyst? Answer it in points, not impressions.

Background
----------
``AnalystDistillationService`` already compares analyst labels to bot setup
candidates, and it is genuinely useful: it reports direction overlap, setup
type overlap, POI overlap and entry proximity.

But every one of those measures *whether the system saw the same chart*. None
of them measures *who made money*. On 2026-07-29 the system saw the sweep, the
order block and the bearish structure -- its SMC candidate was arguably a good
read of the chart -- and it still published a BUY that lost 198 points while
the analyst sold 4040 down to 4009 for +310.

A scoreboard built on setup overlap would have scored that day as a partial
success. The only honest measure is captured points, side by side.

These tests pin that measure:
  - what the analyst's plan was worth, from his own entry/stop/targets
  - what the system actually captured on the same symbol and day
  - the gap between them, with no rounding in the system's favour
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.analyst_scoreboard import AnalystScoreboardService


CONFIG = {"symbol": "XAU/USD"}


# The 2026-07-29 analyst call, as drawn on his chart.
ANALYST_SELL = {
    "id": "LABEL_20260729_001",
    "symbol": "XAU/USD",
    "bias": "SELL",
    "intended_entry": 4040.0,
    "invalidation": 4047.5,
    "tp1": 4026.8,
    "tp2": 4008.6,
    "created_at": "2026-07-29T08:38:00+00:00",
    "trade_decision": "TRADE",
}

# What the market did *after* the analyst's 08:38 entry.
#
# Sequencing matters and a whole-day high/low cannot express it. His stop sits
# at 4047.5 -- the sweep high he forecast -- and that sweep happened before he
# entered, which is precisely why he entered. Scoring him against the full
# day's high would stop him out on the event his thesis was built on.
#
# So the scoreboard is given the range that came *after* the entry: the high
# is the post-rejection high, not the sweep that preceded it.
MARKET_LOW = 4008.0
MARKET_HIGH = 4042.0


def _system_buy_loss() -> dict:
    """The BUY the system published at 12:21, marked at the 4009 low."""
    return {
        "id": "TRADE_20260729_122106",
        "symbol": "XAU/USD",
        "type": "BUY",
        "entry_price": 4028.77,
        "stop_loss": 4013.77,
        "tp1": 4047.76,
        "tp2": 4054.56,
        "status": "CLOSED",
        "close_price": 4009.0,
        "created_at": "2026-07-29T12:21:06+00:00",
        "closed_at": "2026-07-29T15:46:00+00:00",
    }


# ── The measure that did not exist ─────────────────────────────────────────

def test_scoreboard_reports_points_for_both_sides() -> None:
    """The headline number: analyst points vs system points."""
    board = AnalystScoreboardService(CONFIG).score_day(
        labels=[ANALYST_SELL],
        trades=[_system_buy_loss()],
        market_high=MARKET_HIGH,
        market_low=MARKET_LOW,
        symbol="XAU/USD",
    )

    # Analyst: SELL 4040 -> TP2 4008.6 = 314 points available and reached.
    assert board["analyst_points"] == 314.0, board
    # System: BUY 4028.77 closed 4009 = -197.7 points.
    assert board["system_points"] == -197.7, board
    assert board["gap_points"] == -511.7, board
    assert board["verdict"] == "ANALYST_AHEAD"


def test_system_ahead_is_reported_honestly_too() -> None:
    """The scoreboard must be able to say we won, on the same arithmetic."""
    winning_trade = {
        "id": "TRADE_WIN", "symbol": "XAU/USD", "type": "SELL",
        "entry_price": 4040.0, "stop_loss": 4050.0,
        "status": "CLOSED", "close_price": 4000.0,
        "created_at": "2026-07-29T09:00:00+00:00",
    }
    board = AnalystScoreboardService(CONFIG).score_day(
        labels=[ANALYST_SELL], trades=[winning_trade],
        market_high=MARKET_HIGH, market_low=MARKET_LOW, symbol="XAU/USD",
    )

    assert board["system_points"] == 400.0
    assert board["verdict"] == "SYSTEM_AHEAD"
    assert board["gap_points"] == 86.0


def test_no_trade_is_scored_as_zero_not_excused() -> None:
    """Refusing to trade a winning day is a real cost, not a neutral result.

    This is the case that matters most: 98% of cycles produced no order. A
    scoreboard that skipped empty days would have reported nothing wrong
    while the system stood still through a 310-point move.
    """
    board = AnalystScoreboardService(CONFIG).score_day(
        labels=[ANALYST_SELL], trades=[],
        market_high=MARKET_HIGH, market_low=MARKET_LOW, symbol="XAU/USD",
    )

    assert board["system_points"] == 0.0
    assert board["system_traded"] is False
    assert board["verdict"] == "ANALYST_AHEAD"
    assert board["gap_points"] == -314.0
    assert "no order" in board["summary"].lower()


def test_analyst_plan_that_never_triggered_scores_zero() -> None:
    """An analyst idea price never reached earns him nothing either."""
    untouched = dict(ANALYST_SELL, intended_entry=4200.0, tp1=4180.0, tp2=4150.0,
                     invalidation=4210.0)
    board = AnalystScoreboardService(CONFIG).score_day(
        labels=[untouched], trades=[],
        market_high=MARKET_HIGH, market_low=MARKET_LOW, symbol="XAU/USD",
    )

    assert board["analyst_points"] == 0.0
    assert board["verdict"] == "TIE"


def test_analyst_plan_stopped_out_is_scored_as_a_loss() -> None:
    """The analyst is marked to the same standard: his stop counts against him."""
    # A BUY idea from 4040 with invalidation at 4030: price traded to 4008,
    # so this plan was stopped out before any target.
    stopped = dict(ANALYST_SELL, bias="BUY", intended_entry=4040.0,
                   invalidation=4030.0, tp1=4060.0, tp2=4080.0)
    board = AnalystScoreboardService(CONFIG).score_day(
        labels=[stopped], trades=[],
        market_high=MARKET_HIGH, market_low=MARKET_LOW, symbol="XAU/USD",
    )

    assert board["analyst_points"] == -100.0
    assert board["verdict"] == "SYSTEM_AHEAD"


# ── Guards ─────────────────────────────────────────────────────────────────

def test_only_the_requested_symbol_is_scored() -> None:
    """Cross-symbol contamination would silently flatter or punish the system."""
    other = dict(_system_buy_loss(), id="OTHER", symbol="EUR/USD",
                 close_price=4200.0)
    board = AnalystScoreboardService(CONFIG).score_day(
        labels=[ANALYST_SELL], trades=[other],
        market_high=MARKET_HIGH, market_low=MARKET_LOW, symbol="XAU/USD",
    )
    assert board["system_points"] == 0.0
    assert board["system_traded"] is False


def test_open_trades_are_marked_to_market_not_ignored() -> None:
    """A trade still running is scored at the live price, not skipped."""
    running = dict(_system_buy_loss(), status="OPEN", close_price=None)
    board = AnalystScoreboardService(CONFIG).score_day(
        labels=[ANALYST_SELL], trades=[running],
        market_high=MARKET_HIGH, market_low=MARKET_LOW, symbol="XAU/USD",
        current_price=4009.0,
    )
    assert board["system_traded"] is True
    assert board["system_points"] == -197.7


def test_watch_only_labels_are_not_scored_as_trades() -> None:
    """An analyst note marked WATCH is not a trade he took."""
    watch = dict(ANALYST_SELL, trade_decision="WATCH")
    board = AnalystScoreboardService(CONFIG).score_day(
        labels=[watch], trades=[],
        market_high=MARKET_HIGH, market_low=MARKET_LOW, symbol="XAU/USD",
    )
    assert board["analyst_points"] == 0.0
    assert board["analyst_traded"] is False


def test_empty_inputs_do_not_crash() -> None:
    """A day with no labels and no trades is a tie, not an exception."""
    board = AnalystScoreboardService(CONFIG).score_day(
        labels=[], trades=[], market_high=0.0, market_low=0.0, symbol="XAU/USD",
    )
    assert board["verdict"] == "TIE"
    assert board["analyst_points"] == 0.0
    assert board["system_points"] == 0.0


def test_stop_is_assumed_first_when_both_are_touched_after_entry() -> None:
    """Ambiguity resolves against the analyst, never in his favour.

    If the post-entry range covers his stop and his target, the scoreboard
    cannot know which came first. It assumes the stop. A scoreboard that
    guessed the other way would quietly inflate the bar we are measuring
    ourselves against, which is the one number that must stay honest.
    """
    board = AnalystScoreboardService(CONFIG).score_day(
        labels=[ANALYST_SELL], trades=[],
        market_high=4050.0,   # above his 4047.5 invalidation
        market_low=4008.0,    # below his 4008.6 target
        symbol="XAU/USD",
    )

    assert board["analyst_detail"][0]["outcome"] == "STOPPED"
    assert board["analyst_points"] == -75.0


# ── The wiring must be live, not just importable ───────────────────────────

def test_daily_report_calls_the_scoreboard() -> None:
    """A scoreboard nobody runs answers nothing.

    Every defect in this codebase has followed one pattern: correct logic that
    is never called. This asserts the daily report actually reaches the
    service and renders its lines.
    """
    import scripts.run_daily_report as report

    source = Path(report.__file__).read_text(encoding="utf-8")
    assert "AnalystScoreboardService(config)" in source, (
        "the daily report must construct the scoreboard"
    )
    assert "score_day(" in source, "the daily report must call score_day"
    assert "build_report_lines(" in source, (
        "the scoreboard's output must be rendered into the report"
    )


def test_post_label_range_helper_is_defined_and_safe() -> None:
    """The range helper must degrade to zeros, never raise, never guess."""
    import scripts.run_daily_report as report

    assert hasattr(report, "_post_label_range")
    assert hasattr(report, "_parse_iso")

    # No labels: nothing to score against.
    assert report._post_label_range({}, [], "XAU/USD") == {
        "high": 0.0, "low": 0.0, "last": 0.0,
    }
    # Labels with unparseable timestamps must not raise.
    assert report._post_label_range({}, [{"created_at": "not-a-date"}], "XAU/USD") == {
        "high": 0.0, "low": 0.0, "last": 0.0,
    }


def test_zero_range_scores_nothing_rather_than_guessing() -> None:
    """When the range is unknown the analyst is not credited or punished."""
    board = AnalystScoreboardService(CONFIG).score_day(
        labels=[ANALYST_SELL], trades=[],
        market_high=0.0, market_low=0.0, symbol="XAU/USD",
    )
    assert board["analyst_points"] == 0.0
    assert board["analyst_traded"] is False
