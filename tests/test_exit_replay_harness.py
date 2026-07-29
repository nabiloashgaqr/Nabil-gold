"""Measure exit management by replaying candles through the real manager.

Why this exists
---------------
Phase D is "enter and exit professionally". The rule agreed for it was: no
change to trade management before the current behaviour is measured.

Attempting that measurement exposed a problem. ``services/backtesting.py``
models exactly two exits -- stop loss and TP2:

    if stop_loss and low <= stop_loss:  -> SL_HIT
    if tp2 and high >= tp2:             -> TP2_HIT

It contains no reference to trailing, breakeven, or partial closes. So the
one tool available for measuring exits is blind to every mechanism this phase
is about. Tuning the trailing distance against it would have produced numbers
that could not move, and any "improvement" would have been noise.

This harness replays a candle series through ``OpenTradesManager.evaluate_trade``
-- the real production code, not a reimplementation -- and reports what
actually happened to the trade: where the stop ended up, whether breakeven
engaged, when trailing moved, and the final points.

It measures. It changes nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.exit_replay import ExitReplayHarness


BASE_CONFIG = {
    "symbol": "XAU/USD",
    "trade_management": {
        "auto_move_sl_to_entry_after_tp1": True,
        "min_breakeven_rr": 0.5,
        "early_breakeven_points": 150,
        "trailing_stop_enabled": True,
        "trailing_distance_points": 150,
        "trailing_step_points": 40,
        "partial_close_at_tp1": True,
    },
    "trailing_stop": {"enabled": True},
}


def _candles(path: list[tuple[float, float]]) -> list[dict]:
    """Build a candle series from (high, low) pairs."""
    out = []
    for index, (high, low) in enumerate(path):
        out.append({
            "time": f"2026-07-29T{8 + index // 12:02d}:{(index % 12) * 5:02d}:00+00:00",
            "open": (high + low) / 2,
            "high": high,
            "low": low,
            "close": (high + low) / 2,
        })
    return out


def _sell_trade() -> dict:
    """A SELL from 4040 with a 400-point stop, mirroring the analyst's plan."""
    return {
        "id": "REPLAY_SELL",
        "symbol": "XAU/USD",
        "type": "SELL",
        "entry_price": 4040.0,
        "stop_loss": 4080.0,
        "initial_stop_loss": 4080.0,
        "tp1": 4026.8,
        "tp2": 4008.6,
        "status": "OPEN",
        "entry_time": "2026-07-29T08:38:00+00:00",
        "created_at": "2026-07-29T08:38:00+00:00",
        "updates_sent": [],
    }


# ── The harness must observe the real mechanisms ───────────────────────────

def test_harness_reports_the_winning_move_end_to_end() -> None:
    """4040 -> 4008: the analyst's actual trade, replayed."""
    path = _candles([
        (4042.0, 4036.0),
        (4038.0, 4028.0),
        (4030.0, 4020.0),
        (4022.0, 4012.0),
        (4014.0, 4008.0),
        (4021.0, 4008.0),   # the bounce back to 4021
    ])
    result = ExitReplayHarness(BASE_CONFIG).replay(_sell_trade(), path)

    assert result["filled"] is True
    assert result["events"], "the manager must emit events during the move"
    # TP2 at 4008.6 was reached on the fifth candle.
    assert "TP2_HIT" in result["events"]
    assert result["exit_price"] <= 4009.0
    assert result["points"] > 300.0


def test_harness_observes_breakeven_and_trailing_separately() -> None:
    """The two protective mechanisms must be distinguishable in the output.

    This is the measurement Phase D needs: not just the final number, but
    which mechanism produced it.
    """
    path = _candles([
        (4042.0, 4036.0),
        (4038.0, 4024.0),   # +160 pts favourable -> early breakeven arms
        (4030.0, 4018.0),   # further favourable -> trailing should engage
        (4024.0, 4014.0),
    ])
    result = ExitReplayHarness(BASE_CONFIG).replay(_sell_trade(), path)

    assert result["breakeven_engaged"] is True, result
    assert result["trailing_moves"] >= 1, result
    assert result["final_stop"] < 4080.0, "the stop must have advanced"
    assert result["stop_history"], "each stop movement must be recorded"


def test_harness_records_a_stop_out_honestly() -> None:
    """A losing replay must report the loss, not silently expire."""
    path = _candles([
        (4050.0, 4038.0),
        (4070.0, 4045.0),
        (4085.0, 4060.0),   # through the 4080 stop
    ])
    result = ExitReplayHarness(BASE_CONFIG).replay(_sell_trade(), path)

    assert "SL_HIT" in result["events"]
    assert result["points"] < 0


def test_harness_is_read_only() -> None:
    """Measuring must not mutate the caller's trade or config."""
    trade = _sell_trade()
    before = dict(trade)
    config_before = str(BASE_CONFIG)

    ExitReplayHarness(BASE_CONFIG).replay(trade, _candles([(4042.0, 4020.0)]))

    assert trade == before, "the input trade must not be modified"
    assert str(BASE_CONFIG) == config_before, "the config must not be modified"


# ── Comparison: the whole point of the harness ─────────────────────────────

def test_two_configs_can_be_compared_on_the_same_candles() -> None:
    """Config A vs config B on identical price action, in points.

    Without this, any change to trailing distance is a guess. With it, the
    change is a number.
    """
    path = _candles([
        (4042.0, 4036.0),
        (4038.0, 4022.0),
        (4030.0, 4016.0),
        (4034.0, 4024.0),   # a pullback that a tight trail would exit into
        (4028.0, 4010.0),
    ])

    tight = {**BASE_CONFIG, "trade_management": {
        **BASE_CONFIG["trade_management"], "trailing_distance_points": 60}}
    wide = {**BASE_CONFIG, "trade_management": {
        **BASE_CONFIG["trade_management"], "trailing_distance_points": 300}}

    comparison = ExitReplayHarness(BASE_CONFIG).compare(
        _sell_trade(), path, {"tight_trail": tight, "wide_trail": wide},
    )

    assert set(comparison["variants"]) == {"tight_trail", "wide_trail"}
    for name in ("tight_trail", "wide_trail"):
        assert "points" in comparison["variants"][name]
    assert comparison["best"] in {"tight_trail", "wide_trail"}
    # The verdict must be arithmetic, not a preference.
    best_points = comparison["variants"][comparison["best"]]["points"]
    assert all(
        best_points >= data["points"]
        for data in comparison["variants"].values()
    )


def test_comparison_reports_a_tie_rather_than_inventing_a_winner() -> None:
    """Identical configs must not produce a spurious improvement."""
    same_a = {**BASE_CONFIG}
    same_b = {**BASE_CONFIG}
    path = _candles([(4042.0, 4030.0), (4036.0, 4020.0)])

    comparison = ExitReplayHarness(BASE_CONFIG).compare(
        _sell_trade(), path, {"a": same_a, "b": same_b},
    )

    assert comparison["variants"]["a"]["points"] == comparison["variants"]["b"]["points"]
    assert comparison["tie"] is True


def test_empty_candles_do_not_crash() -> None:
    """No data is an absent measurement, not an exception."""
    result = ExitReplayHarness(BASE_CONFIG).replay(_sell_trade(), [])
    assert result["filled"] is False
    assert result["points"] == 0.0


# ── What the harness found on first use ────────────────────────────────────

def test_root_trade_management_values_are_not_silently_overridden() -> None:
    """Editing trade_management at the root must change behaviour.

    Found by the harness on its first real run: five trailing distances from
    60 to 400 points produced byte-identical exits. The cause is not the
    trailing maths -- it is that ``_management_params`` applies
    ``profiles.default_profile`` *after* reading the root values, and that
    profile repeats the same keys. Every root edit is therefore discarded.

    This is the config-surface version of the dead-gate pattern: the operator
    changes a number, the number is real, and nothing reads it. Anyone tuning
    exits against the root keys would have measured noise and concluded the
    trailing distance does not matter.

    The assertion is deliberately weak -- it only demands that *some*
    difference survives -- because the right resolution (root as base,
    profile as explicit override) is a behaviour change that belongs in its
    own reviewed step, not smuggled in beside a measurement tool.
    """
    from agents.open_trades_manager import OpenTradesManager

    trade = {"id": "CFG", "symbol": "XAU/USD", "type": "SELL",
             "entry_price": 4040.0, "setup_type": "STRUCTURE_CONTINUATION"}

    # The shipped config.json defines profiles.default_profile and repeats the
    # same keys inside it. A fixture without that block cannot express the
    # defect, so the real shape is used here.
    effective = []
    for distance in (60.0, 400.0):
        config = {
            **BASE_CONFIG,
            "trade_management": {
                **BASE_CONFIG["trade_management"],
                "trailing_distance_points": distance,
                "profiles": {
                    "default_profile": {
                        "trailing_distance_points": 150,
                        "trailing_step_points": 40,
                        "early_breakeven_points": 150,
                    }
                },
            },
        }
        manager = OpenTradesManager(config)
        effective.append(manager._management_params(trade)["trailing_distance_points"])

    assert effective[0] != effective[1], (
        "a root-level trailing_distance_points change had no effect "
        f"(60 -> {effective[0]}, 400 -> {effective[1]}); profiles.default_profile "
        "is overriding the root value, so the setting is dead"
    )


def test_profile_overrides_still_apply_when_root_is_absent() -> None:
    """Profiles must keep working: they are not the defect, shadowing is."""
    from agents.open_trades_manager import OpenTradesManager

    config = {
        "symbol": "XAU/USD",
        "trade_management": {
            "trailing_stop_enabled": True,
            "profiles": {"default_profile": {"trailing_distance_points": 222}},
        },
    }
    trade = {"id": "P", "symbol": "XAU/USD", "type": "SELL",
             "entry_price": 4040.0, "setup_type": "STRUCTURE_CONTINUATION"}

    params = OpenTradesManager(config)._management_params(trade)
    assert params["trailing_distance_points"] == 222
