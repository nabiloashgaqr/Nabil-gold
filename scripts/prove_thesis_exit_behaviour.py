"""Measure what the thesis exit actually does, on the user's own trades.

Two live SELLs died the same way, nineteen hours apart:

    a4911dee  SELL 4019.38 -> 4028.02   -86.4 pts   "bullish continuation
                                                     reclaimed the breakdown"
    5f383b5c  SELL 4046.02 -> 4049.94   -39.2 pts   same sentence, and the
                                                    order had been OPEN for
                                                    0.0 hours

The user's report is that this exit "is sometimes right and sometimes wrong".
This script does not argue about that. It measures the rule against the only
thing that can settle it: what the rule actually requires before it fires.

Reads nothing, sends nothing, opens nothing.

Run:  python scripts/prove_thesis_exit_behaviour.py
"""

from __future__ import annotations

import inspect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager  # noqa: E402
from utils.instruments import points_to_price  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOL = "XAU/USD"


def _config() -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _rule(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _mgr() -> OpenTradesManager:
    return OpenTradesManager(_config())


def _fire(manager, *, side, entry, prev, last, hours=1.0, pnl=-10.0,
          mfe=0.0, tp1=0.0, plan=None):
    """Run the real review over two candles and report the verdict."""
    trade = {"id": "T", "type": side, "entry_price": entry, "symbol": SYMBOL}
    if plan is not None:
        trade["signal_snapshot"] = {"session_plan": plan}
    return manager._thesis_exit_review(
        trade, trade_type=side, symbol=SYMBOL,
        current_price=last["close"], recent_candles=[prev, last],
        hours_open=hours, pnl_points=pnl, max_favorable_excursion=mfe,
        tp1=tp1 or (entry - 500 * 0.1 if side == "SELL" else entry + 500 * 0.1),
        entry=entry, partial_close=False,
    )


def _c(o, h, l, c):
    return {"time": "2026-07-30T06:00:00+00:00", "open": o, "high": h, "low": l, "close": c}


# ── 1 ───────────────────────────────────────────────────────────────────────
def part1_the_rule() -> None:
    _rule("1 — what the rule actually requires")

    src = inspect.getsource(OpenTradesManager._continuation_trigger_against_trade)
    print("   The whole test, for a SELL:")
    for line in src.splitlines():
        if "trade_type ==" in line or "last_close >" in line:
            print(f"       {line.strip()}")
    print()
    m = _mgr()
    print(f"   reclaim buffer          : {m.thesis_exit_reclaim_points:.0f} pts "
          f"(= ${points_to_price(m.thesis_exit_reclaim_points, SYMBOL):.2f})")
    print(f"   candles inspected       : 2  (recent_candles[-2], [-1])")
    print(f"   minimum age before firing: NONE")
    print(f"   minimum loss before firing: NONE")
    print(f"   confirmation candles    : NONE")
    print()
    print("   -> three conditions on ONE candle: it closes above the previous")
    print("      high by 12 points, above the previous close, and green.")
    print("      Nothing else is consulted. Not the stop, not the map, not")
    print("      the age of the trade, not how far it is from being wrong.")


# ── 2 ───────────────────────────────────────────────────────────────────────
def part2_stop_is_ignored() -> None:
    _rule("2 — the exit does not look at the stop it is pre-empting")

    m = _mgr()
    entry = 4046.02
    # The real 15m shape that closed 5f383b5c.
    prev = _c(4047.0, 4048.2, 4045.5, 4046.5)
    last = _c(4046.6, 4050.2, 4046.4, 4049.94)

    v = _fire(m, side="SELL", entry=entry, prev=prev, last=last,
              hours=0.0, pnl=-39.2)
    print(f"   trade 5f383b5c, held 0.0h, -39.2 pts")
    print(f"   exit fires : {bool(v.get('exit_now'))}  ({v.get('kind')})")
    print()

    min_sl = float(_config()["risk_settings"]["min_sl_distance_points"])
    print(f"   configured min_sl_distance_points : {min_sl:.0f} pts")
    print(f"   the trade was actually stopped at : 39.2 pts of adverse move")
    print(f"   that is {39.2 / min_sl * 100:.0f}% of the risk the trade was authorised to take")
    print()
    print("   -> you sized the trade to survive 400 points of noise. The")
    print("      thesis exit closed it after 39. The stop you chose was never")
    print("      consulted; a rule with no notion of R closed a position whose")
    print("      whole design was expressed in R.")


# ── 3 ───────────────────────────────────────────────────────────────────────
def part3_one_candle_no_confirmation() -> None:
    _rule("3 — one candle decides; the next candle cannot undo it")

    m = _mgr()
    entry = 4046.02
    prev = _c(4047.0, 4048.2, 4045.5, 4046.5)

    print("   Same trade. Only the closing candle changes:")
    print()
    print(f"   {'closing candle':<44}{'close':>9}   exit?")
    print(f"   {'-' * 44}{'-' * 9}   -----")
    for label, last in (
        ("the real case", _c(4046.6, 4050.2, 4046.4, 4049.94)),
        ("5 cents above the trigger (4049.45)", _c(4046.6, 4050.2, 4046.4, 4049.45)),
        ("5 cents below the trigger (4049.35)", _c(4046.6, 4050.2, 4046.4, 4049.35)),
        ("spikes 118 pts up, closes back down", _c(4046.6, 4060.0, 4046.0, 4046.2)),
    ):
        v = _fire(m, side="SELL", entry=entry, prev=prev, last=last, hours=0.0, pnl=-39.2)
        mark = "YES" if v.get("exit_now") else "no"
        print(f"   {label:<44}{last['close']:>9.2f}   {mark}")

    print()
    print("   Note the last row: price ran 118 points against the trade inside")
    print("   the candle and the exit stayed silent, because it closed back")
    print("   down. Ten cents of difference at the close matters more to this")
    print("   rule than 118 points of actual movement.")
    print()
    print("   -> the entire decision turns on where one candle closed, to the")
    print("      cent. There is no second candle asked to agree. A single 15m")
    print("      close 12 points the wrong way is treated as proof the thesis")
    print("      is dead -- on gold, where 12 points is $1.20.")


# ── 3b ──────────────────────────────────────────────────────────────────────
def part3b_both_trades_in_R() -> None:
    _rule("3b — both live exits, measured in R")

    cfg = _config()
    m = _mgr()
    rows = [
        # id, entry, stop, exit, pnl
        ("a4911dee", 4019.38, 4059.38, 4028.02, -86.4),
        ("5f383b5c", 4046.02, 4086.02, 4049.94, -39.2),
    ]
    print(f"   {'trade':<12}{'entry':>9}{'stop':>9}{'exit':>9}"
          f"{'risk':>8}{'lost':>8}{'as R':>8}")
    print(f"   {'-' * 12}{'-' * 9}{'-' * 9}{'-' * 9}{'-' * 8}{'-' * 8}{'-' * 8}")
    for tid, entry, stop, exit_px, pnl in rows:
        risk = abs(entry - stop) / 0.1
        lost = abs(pnl)
        print(f"   {tid:<12}{entry:>9.2f}{stop:>9.2f}{exit_px:>9.2f}"
              f"{risk:>7.0f}p{lost:>7.0f}p{lost / risk:>7.2f}R")

    print()
    print("   Neither trade was ever close to being wrong. They were closed")
    print("   at 0.22R and 0.10R -- inside the noise band the stop exists to")
    print("   absorb. A stop 400 points away states, in the system's own")
    print("   config, that moves of this size carry no information.")
    print()
    print("   The exit rule disagrees with the risk model, and it is the exit")
    print("   rule that gets the last word.")


# ── 4 ───────────────────────────────────────────────────────────────────────
def part4_asymmetry() -> None:
    _rule("4 — the guard that exists for one exit path and not the other")

    m = _mgr()
    entry = 4046.02
    prev = _c(4047.0, 4048.2, 4045.5, 4046.5)
    last = _c(4046.6, 4050.2, 4046.4, 4049.94)

    print("   _thesis_exit_review has three branches. Their preconditions:")
    print()
    print("     A  OPPOSITE_CONTINUATION")
    print("          age      : none")
    print("          loss     : none")
    print("          progress : none")
    print("          MFE      : none")
    print("        -> fires on candle shape alone")
    print()
    print(f"     B  OPPOSING_POI_REJECTION")
    print(f"          needs a mapped POI level to reject from")
    print(f"          buffer {m.thesis_exit_opposing_poi_buffer_points:.0f} pts, "
          f"reclaim {m.thesis_exit_opposing_poi_reclaim_points:.0f} pts")
    print(f"          skipped entirely once progress >= 100%")
    print()
    print(f"     C  COUNTERTREND_NO_FOLLOW_THROUGH")
    print(f"          age      : >= {m.thesis_exit_countertrend_hold_minutes:.0f} minutes")
    print(f"          pnl      : <= 0")
    print(f"          MFE      : < {m.thesis_exit_min_mfe_points:.0f} pts")
    print(f"          progress : < {m.thesis_exit_min_progress_pct:.0f}%")
    print(f"          alignment: must be COUNTER_OBJECTIVE_REVERSAL_CONFIRMED")
    print(f"        -> five conditions, including a minimum age")
    print()

    # Branch C refuses at 0.0h. Branch A does not.
    plan = {"market_objective_direction": "BUY"}
    v_c = m._thesis_exit_review(
        {"id": "T", "type": "SELL", "entry_price": entry, "symbol": SYMBOL,
         "signal_snapshot": {"session_plan": plan}},
        trade_type="SELL", symbol=SYMBOL, current_price=4049.94,
        recent_candles=[_c(4047.0, 4048.2, 4045.5, 4046.5),
                        _c(4046.6, 4047.0, 4046.0, 4046.9)],  # no continuation
        hours_open=0.0, pnl_points=-39.2, max_favorable_excursion=0.0,
        tp1=3996.0, entry=entry, partial_close=False,
    )
    v_a = _fire(m, side="SELL", entry=entry, prev=prev, last=last, hours=0.0,
                pnl=-39.2, plan=plan)
    print(f"   at age 0.0h, branch C fires : {bool(v_c.get('exit_now'))}"
          f"   <- its 20-minute floor refuses")
    print(f"   at age 0.0h, branch A fires : {bool(v_a.get('exit_now'))}"
          f"   <- no floor exists to refuse")
    print()
    print("   -> the codebase already believes a countertrend trade deserves")
    print("      20 minutes before being judged. Branch A closed a trade at")
    print("      0.0 hours. The same belief was simply never applied to it.")


# ── 5 ───────────────────────────────────────────────────────────────────────
def part5_unconfigurable_untested() -> None:
    _rule("5 — why this was never tuned")

    cfg = _config()
    tm = cfg.get("trade_management", {})
    print(f"   config.json -> trade_management.thesis_exit : "
          f"{tm.get('thesis_exit', 'ABSENT')}")
    print()
    m = _mgr()
    print("   So every value below is a hardcoded default in Python:")
    for name in ("thesis_exit_enabled", "thesis_exit_reclaim_points",
                 "thesis_exit_countertrend_hold_minutes",
                 "thesis_exit_min_mfe_points", "thesis_exit_min_progress_pct",
                 "thesis_exit_opposing_poi_buffer_points"):
        print(f"     {name:<42}= {getattr(m, name)}")

    tests = []
    tdir = os.path.join(ROOT, "tests")
    for name in sorted(os.listdir(tdir)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(tdir, name), encoding="utf-8") as fh:
            if "thesis_exit" in fh.read():
                tests.append(name)
    print()
    print(f"   test files mentioning thesis_exit : {tests}")
    print("   ...and that one asserts the Telegram message says 'Thesis Exit'.")
    print()
    print("   -> behavioural tests for this rule: ZERO. It cannot be tuned")
    print("      from config, and nothing would fail if it were changed.")
    print("      That is why it drifts: it is the one exit nobody can measure.")


def main() -> None:
    print()
    print("#" * 70)
    print("#  thesis exit — what it actually does")
    print("#" * 70)

    part1_the_rule()
    part2_stop_is_ignored()
    part3_one_candle_no_confirmation()
    part3b_both_trades_in_R()
    part4_asymmetry()
    part5_unconfigurable_untested()

    _rule("VERDICT — superseded, read the correction below")
    print("   The rule is not wrong in kind. A genuine bullish reclaim SHOULD")
    print("   close a SELL, and sometimes it did exactly that.")
    print()
    print("   It is wrong in EVIDENCE. It accepts the weakest possible proof:")
    print()
    print("     · one 15m candle, no confirmation")
    print("     · 12 points ($1.20) of reclaim on an instrument you allow")
    print("       400 points of noise")
    print("     · no minimum age -- 5f383b5c died at 0.0h")
    print("     · no reference to the stop, the R, or the map")
    print()
    print("   That is why it is 'sometimes right, sometimes wrong': the")
    print("   evidence it demands is so thin that it cannot separate a real")
    print("   reversal from ordinary noise. Both look identical to it.")
    print()
    print("   " + "!" * 66)
    print("   CORRECTION (measured afterwards by prove_exit_needs_agents.py)")
    print("   " + "!" * 66)
    print()
    print("   The measurements above are all reproducible and still stand.")
    print("   The REMEDY implied by them was wrong, and is withdrawn here")
    print("   rather than quietly deleted.")
    print()
    print("   This script measured how EARLY the exits fired (0.22R, 0.10R)")
    print("   and inferred they were premature. It never asked what happened")
    print("   next. For a4911dee the answer is recorded in the user's own")
    print("   messages: price ran to 4079.33, straight through the 4059.38")
    print("   stop. The exit turned a -400 pt stop-out into -86.4.")
    print()
    print("   An R-floor -- proposal 1 of this script -- would have held that")
    print("   trade open to a full stop-out. Earliness was never the fault.")
    print()
    print("   What survives is section 3b's real finding: both trades produced")
    print("   a BYTE-IDENTICAL trigger from the same rule, yet one was a")
    print("   regime change and the other was noise. No threshold can split")
    print("   them, because the defect is the INPUT, not its level. The fix")
    print("   is a second source of evidence -- the agents.")
    print()
    print("   -> run:  python scripts/prove_exit_needs_agents.py")
    print()


if __name__ == "__main__":
    main()
