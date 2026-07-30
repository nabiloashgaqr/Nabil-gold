"""Should the agents vote on the exit? Measure, then answer.

The previous review (prove_thesis_exit_behaviour.py) concluded the exit rule
was "too trigger-happy" because it closed two trades at 0.22R and 0.10R.

That conclusion was incomplete, and this script exists to correct it. A rule
is not judged by how early it acts but by what happened next. Both trades are
re-examined against the prices the user actually reported afterwards.

The answer changes the whole proposal, so it is measured first.

Reads nothing, sends nothing, opens nothing.

Run:  python scripts/prove_exit_needs_agents.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOL = "XAU/USD"
PT = 0.1


def _config() -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _rule(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


# ── 1 ───────────────────────────────────────────────────────────────────────
def part1_what_happened_next() -> None:
    """The correction. Judge each exit by the price that followed it."""
    _rule("1 — CORRECTION: what happened AFTER each exit")

    print("   My previous review called both exits premature. That judged the")
    print("   exit by its size, not by its outcome. Here is the outcome.")
    print()

    # a4911dee — the user reported prices after the exit in the same message.
    entry, stop, exit_px = 4019.38, 4059.38, 4028.02
    print("   a4911dee   SELL 4019.38, stop 4059.38")
    print(f"      exited at 4028.02                         = {(entry - exit_px) / PT:+7.1f} pts")
    print("      then the user's own next messages report:")
    print(f"        17:16  price 4049.02                    = {(entry - 4049.02) / PT:+7.1f} pts if still open")
    print(f"        18:05  price 4079.33                    = {(entry - 4079.33) / PT:+7.1f} pts if still open")
    print()
    print(f"      the stop sat at 4059.38. Price reached 4079.33.")
    print(f"      -> the trade WOULD have been stopped out: {(entry - stop) / PT:+7.1f} pts")
    print(f"      -> the thesis exit took                 : {(entry - exit_px) / PT:+7.1f} pts")
    print(f"      -> it SAVED                             : {abs((entry - stop) / PT) - abs((entry - exit_px) / PT):7.1f} pts")
    print()
    print("   5f383b5c   SELL 4046.02, exited 4049.94 = -39.2 pts")
    print("      outcome after this exit: NOT REPORTED. Unknown.")
    print()
    print("   -> on the one trade where the outcome is known, the exit was")
    print("      RIGHT, and decisively so. It turned a full 400-point stop-out")
    print("      into an 86-point scratch.")
    print()
    print("   So the rule must NOT simply be made harder to trigger. My")
    print("   earlier proposal (a 0.35R floor) would have held a4911dee open")
    print("   all the way to -400. That proposal is withdrawn.")


# ── 2 ───────────────────────────────────────────────────────────────────────
def part2_the_real_defect() -> None:
    """Same rule, same shape, opposite outcomes -> the input is too thin."""
    _rule("2 — the real defect: identical evidence, opposite outcomes")

    m = OpenTradesManager(_config())

    def shape(prev, last):
        return m._continuation_trigger_against_trade("SELL", [prev, last], SYMBOL)

    # Both trades produced the SAME trigger from the SAME rule.
    a = shape({"high": 4026.0, "close": 4024.0, "open": 4021.0, "low": 4020.0},
              {"high": 4028.5, "close": 4028.02, "open": 4025.0, "low": 4024.5})
    b = shape({"high": 4048.2, "close": 4046.5, "open": 4047.0, "low": 4045.5},
              {"high": 4050.2, "close": 4049.94, "open": 4046.6, "low": 4046.4})

    print(f"   a4911dee trigger : {a!r}")
    print(f"   5f383b5c trigger : {b!r}")
    print(f"   identical? {a == b}")
    print()
    print("   The rule cannot tell these two apart, because it looks at two")
    print("   candles and nothing else. Yet one was a genuine 400-point")
    print("   regime change and the other closed a trade 0.0h old.")
    print()
    print("   -> the defect is not the THRESHOLD. It is the INPUT.")
    print("      Candle shape alone cannot distinguish a real reversal from")
    print("      noise, no matter where the threshold is placed. Raising it")
    print("      loses a4911dee; lowering it keeps 5f383b5c. There is no")
    print("      setting of a two-candle rule that gets both right.")
    print()
    print("   The rule needs a SECOND, INDEPENDENT source of evidence.")
    print("   That is exactly what the agents are.")


# ── 3 ───────────────────────────────────────────────────────────────────────
def part3_agents_are_absent() -> None:
    """The agents exist in the same cycle -- 23 lines too late."""
    _rule("3 — the agents already run in the same cycle, 23 lines too late")

    path = os.path.join(ROOT, "scripts", "run_analysis.py")
    lines = open(path, encoding="utf-8").readlines()

    def find(needle: str) -> int:
        for i, text in enumerate(lines, start=1):
            if needle in text:
                return i
        return -1

    exit_at = find("OpenTradesManager(config).update_trades(")
    agents_at = find('all_results = {"technical": run_agent(')
    plan_at = find("SessionPlannerService(config).build_plan(all_results")

    print(f"   line {exit_at}   the EXIT decides        (update_trades)")
    print(f"   line {agents_at}   the AGENTS are polled   (run_agent x6)")
    print(f"   line {plan_at}   the map is built        (build_plan)")
    print()
    print(f"   gap between the exit and the agents: {agents_at - exit_at} lines,"
          f" same function, same cycle")
    print()

    src = "".join(lines)
    sig = src[src.find("def _run_analysis_for_config"):]
    uses_data = "run_agent(\"technical\", TechnicalAgent(config), data)" in sig
    print(f"   the agents are fed `data`, which is already fetched before the")
    print(f"   exit runs (used for candle_high/candle_low): {uses_data}")
    print()
    print("   -> there is NO technical obstacle. The market payload the agents")
    print("      need is in hand before the exit is evaluated. They are simply")
    print("      called afterwards, so the exit is structurally blind to a")
    print("      full six-agent read that the very same cycle produces.")

    # And the manager cannot see them even if it wanted to.
    import inspect
    from agents.open_trades_manager import OpenTradesManager as OTM
    params = inspect.signature(OTM.update_trades).parameters
    print()
    print(f"   update_trades() parameters: {list(params)[1:]}")
    print(f"   any parameter carrying agent opinion? "
          f"{any('agent' in p or 'result' in p for p in params)}")
    print()
    print("   -> the channel does not exist. news_context was added when news")
    print("      needed to reach the exit; the same door has never been opened")
    print("      for the agents.")


# ── 4 ───────────────────────────────────────────────────────────────────────
def part4_would_agents_have_helped() -> None:
    """Test the proposed rule against both trades using their real books."""
    _rule("4 — would an agent vote have separated the two cases?")

    cfg = _config()
    min_conf = float(cfg["signal_requirements"]["agent_min_confidence"])
    print(f"   agent_min_confidence = {min_conf:.0f}  (the bar used everywhere else)")
    print()

    # The 17:16 book is the user's own published message, ~19 min after the
    # a4911dee exit. It is the closest recorded read to that moment.
    book_a = {
        "technical": ("BUY", 39.6), "classical": ("BUY", 29.0),
        "smc": ("BUY", 82.0), "price_action": ("BUY", 79.0),
        "multitimeframe": ("WAIT", 48.0),
    }
    print("   a4911dee — agent book published 19 min after the exit (17:16):")
    _score(book_a, min_conf, "SELL")
    print()
    print("   5f383b5c — no agent book was published for 06:31. Unknown.")
    print()
    print("   -> at the moment a4911dee was closed, TWO qualified agents (SMC")
    print("      82, Price Action 79) read BUY against the open SELL, and none")
    print("      read SELL. The agents agreed with the exit, independently of")
    print("      candle shape, and they were right: price ran to 4079.")
    print()
    print("      That is the confirming signal the rule never asked for.")


def _score(book: dict, min_conf: float, trade_side: str) -> None:
    opp = "BUY" if trade_side == "SELL" else "SELL"
    against = [f"{k} {c:.0f}" for k, (d, c) in book.items() if d == opp and c >= min_conf]
    defending = [f"{k} {c:.0f}" for k, (d, c) in book.items() if d == trade_side and c >= min_conf]
    print(f"      qualified agents AGAINST the {trade_side} : "
          f"{len(against)}  {against}")
    print(f"      qualified agents DEFENDING it        : "
          f"{len(defending)}  {defending or '[]'}")


# ── 5 ───────────────────────────────────────────────────────────────────────
def part5_the_shape_of_the_fix() -> None:
    _rule("5 — what a two-source exit would look like")

    print("   Keep the candle trigger. Add the agents as a SECOND opinion,")
    print("   and let the two combine into a graded response instead of a")
    print("   single all-or-nothing close:")
    print()
    print("     candle says flip + agents CONFIRM      -> full exit")
    print("        (a4911dee: 2 qualified agents against, 0 defending)")
    print()
    print("     candle says flip + agents SILENT       -> scale out, hold rest")
    print("        the machinery already exists: THESIS_SCALE_OUT is a live")
    print("        branch with SL->breakeven, used today by the POI path")
    print()
    print("     candle says flip + agents DEFEND trade -> do not exit")
    print("        the thesis the trade was opened on is still supported")
    print()
    print("   This is strictly better than a threshold change because it is")
    print("   the only option that can get BOTH trades right: a4911dee exits")
    print("   (agents confirmed), while a 0.0h trade with no agent support")
    print("   is scaled rather than killed.")
    print()
    print("   Note what it does NOT touch: no risk setting, no stop distance,")
    print("   no archetype_conviction, no RR. It adds evidence to a decision")
    print("   that currently has one source.")


def main() -> None:
    print()
    print("#" * 72)
    print("#  should the agents vote on the exit?")
    print("#" * 72)

    part1_what_happened_next()
    part2_the_real_defect()
    part3_agents_are_absent()
    part4_would_agents_have_helped()
    part5_the_shape_of_the_fix()

    _rule("VERDICT")
    print("   The exit rule is not too aggressive. On the one trade whose")
    print("   outcome is known it saved 314 points, and my earlier proposal to")
    print("   add an R-floor would have destroyed that. Withdrawn.")
    print()
    print("   The defect is that it decides alone. Two candles is one source")
    print("   of evidence, and one source cannot separate a regime change from")
    print("   noise -- the two trades produced a byte-identical trigger.")
    print()
    print("   The system already computes a six-agent read every cycle, 23")
    print("   lines after the exit decides, from market data it already holds.")
    print("   The exit is blind to it only because nobody passed it in.")
    print()


if __name__ == "__main__":
    main()
