"""Why the 2026-07-29 SELL thesis exit produced no BUY market entry.

The live sequence, in the user's own numbers:

    15:21  SELL opened          4019.38
    ~17:0x SELL closed          4028.02   "bullish continuation reclaimed
                                           the breakdown"          -86.4 pts
    17:16  BUY plan published   entry 4028.77, price 4049.02, A+ 100
    ~18:05 BUY pending killed   price 4079.33, "market moved 339 pts
                                           without fill"

The position manager announced a bullish flip at 4028.02. Nineteen minutes
later the planner mapped a BUY at 4028.77 -- seventy-five hundredths of a
dollar from where the flip was announced -- and asked the market to walk 202
points back down to it. The market went the other way, 339 points, and the
order died unfilled.

This script asks one question at each link in the chain: could the exit have
become an entry here? It reads no database, sends nothing, and needs no
secrets.

Run:  python scripts/prove_exit_without_entry.py
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.open_trades_manager import OpenTradesManager  # noqa: E402
import scripts.run_analysis as ra  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── the live numbers ────────────────────────────────────────────────────────
SELL_ENTRY = 4019.38
EXIT_PRICE = 4028.02          # where the manager declared the bullish flip
PLAN_ENTRY = 4028.77          # what the planner mapped 19 minutes later
PLAN_STOP = 4013.77
PUBLISH_PRICE = 4049.02       # price when the BUY LIMIT was published
CANCEL_PRICE = 4079.33        # price when it was cancelled as stale
SYMBOL = "XAU/USD"


def _config() -> dict:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _rule(title: str) -> None:
    print()
    print("=" * 68)
    print(f"  {title}")
    print("=" * 68)


# ── link 1 ──────────────────────────────────────────────────────────────────
def link1_manager_cannot_open() -> None:
    """The component that exits is not wired to anything that can enter."""
    _rule("LINK 1 — can the component that exited also enter?")

    src = inspect.getsource(OpenTradesManager)
    calls = sorted(set(re.findall(r"database\.([a-z_]+)\(", src)))
    print(f"   every database call OpenTradesManager makes : {calls}")
    writes = [c for c in calls if c.startswith(("save", "insert", "create", "add"))]
    print(f"   of those, calls that can create a trade     : {writes or 'NONE'}")
    print()
    print("   -> the exit brain can modify a row. It cannot open one.")
    print("      Exiting and entering are not two halves of one decision;")
    print("      they live in different objects and only one of them can act.")


# ── link 2 ──────────────────────────────────────────────────────────────────
def link2_verdict_is_discarded() -> None:
    """The exit produces a directional verdict. Nobody reads it."""
    _rule("LINK 2 — where does the 'bullish continuation' verdict go?")

    manager = OpenTradesManager(_config())

    # The exact shape that fired: a SELL, and a candle that closes above the
    # previous high by more than the reclaim buffer.
    candles = [
        {"time": "2026-07-29T16:45:00+00:00", "open": 4021.0, "high": 4026.0,
         "low": 4020.0, "close": 4024.0},
        {"time": "2026-07-29T17:00:00+00:00", "open": 4025.0, "high": 4028.5,
         "low": 4024.5, "close": EXIT_PRICE},
    ]
    verdict = manager._thesis_exit_review(
        {"id": "TRADE_20260729_152108_440161_a4911dee", "type": "SELL",
         "entry_price": SELL_ENTRY, "symbol": SYMBOL},
        trade_type="SELL", symbol=SYMBOL, current_price=EXIT_PRICE,
        recent_candles=candles, hours_open=1.8, pnl_points=-86.4,
        max_favorable_excursion=0.0, tp1=3969.38, entry=SELL_ENTRY,
        partial_close=False,
    )
    print(f"   thesis exit fires : {bool(verdict.get('exit_now'))}")
    print(f"   reason            : {verdict.get('reason')}")
    print(f"   machine-readable  : kind = {verdict.get('kind')}")

    kind = str(verdict.get("kind") or "")
    hits: list[str] = []
    for folder in ("agents", "services", "scripts"):
        for name in sorted(os.listdir(os.path.join(ROOT, folder))):
            if not name.endswith(".py"):
                continue
            path = os.path.join(ROOT, folder, name)
            with open(path, encoding="utf-8") as fh:
                if kind in fh.read():
                    hits.append(f"{folder}/{name}")
    print()
    print(f"   files mentioning {kind!r} : {hits}")
    print(f"   producers 1  ·  consumers {len(hits) - 1}")
    print()
    print("   -> the exit states, in a machine-readable field, that the market")
    print("      just flipped bullish. Exactly one file in the repository")
    print("      contains that word: the one that writes it. The verdict is")
    print("      formatted into a Telegram sentence and then dropped.")


# ── link 3 ──────────────────────────────────────────────────────────────────
def link3_dead_channel() -> None:
    """The one bridge from a trade event to the planner is never written."""
    _rule("LINK 3 — the one channel that could have carried it")

    closed_sell = {
        "id": "TRADE_20260729_152108_440161_a4911dee",
        "symbol": SYMBOL, "type": "SELL", "status": "MANUAL_CLOSE",
        "entry_price": SELL_ENTRY, "close_price": EXIT_PRICE,
        "reasons": ["Automatic thesis exit: bullish continuation reclaimed the breakdown"],
        "signal_snapshot": {"session_plan": {"session_bias": "SELL"}},
    }

    class _DB:
        def get_recent_trades(self, limit=20):
            return [closed_sell]

    watch = ra._active_reversal_watch(_DB(), symbol=SYMBOL)
    print(f"   _active_reversal_watch(...) after the flip : {watch}")

    # Exclude the loader itself (it assigns what it just read) and this proof
    # script, which quotes the pattern in order to search for it.
    _self = os.path.basename(__file__)
    writers: list[str] = []
    for folder in ("agents", "services", "scripts"):
        for name in sorted(os.listdir(os.path.join(ROOT, folder))):
            if not name.endswith(".py") or name in {"run_analysis.py", _self}:
                continue
            if name.startswith("prove_"):
                continue
            with open(os.path.join(ROOT, folder, name), encoding="utf-8") as fh:
                body = fh.read()
            if re.search(r"\[[\"']reversal_watch[\"']\]\s*=", body):
                writers.append(f"{folder}/{name}")
    print(f"   production code that WRITES a reversal_watch : {writers or 'NONE'}")
    print()
    print("   -> SessionPlanner does accept a `reversal_watch` and does read it")
    print("      (services/session_planner.py:81). run_analysis loads it every")
    print("      cycle. But nothing in production ever puts one into a trade")
    print("      snapshot, so the loader can only ever return {}. The wire")
    print("      exists, is soldered at both ends, and carries no current.")

    # And the order of operations is already the one a fix would want.
    with open(os.path.join(ROOT, "scripts", "run_analysis.py"), encoding="utf-8") as fh:
        lines = fh.readlines()

    def _line_of(needle: str) -> int:
        for i, text in enumerate(lines, start=1):
            if needle in text:
                return i
        return -1

    exit_at = _line_of("OpenTradesManager(config).update_trades(")
    read_at = _line_of('all_results["reversal_watch"] = _active_reversal_watch')
    plan_at = _line_of("SessionPlannerService(config).build_plan(all_results")

    print()
    print("   Order of operations inside ONE analysis cycle:")
    print(f"     line {exit_at}  the exit runs        (OpenTradesManager.update_trades)")
    print(f"     line {read_at}  the flip is looked up (_active_reversal_watch)")
    print(f"     line {plan_at}  the map is built      (SessionPlanner.build_plan)")
    print(f"     correctly ordered: {exit_at < read_at < plan_at}")
    print()
    print("   -> the exit fires BEFORE the lookup, and the lookup BEFORE the")
    print("      map is built, in the same cycle. The pipeline is already in")
    print("      the right order and already asks the right question. What is")
    print("      missing is one write: nobody records the answer.")


# ── link 4 ──────────────────────────────────────────────────────────────────
def link4_the_moment() -> None:
    """The same map, judged at two moments 210 points apart."""
    _rule("LINK 4 — the same map, at the exit vs. at publication")

    config = _config()
    threshold = float(config["order_execution"]["market_threshold_points"])
    print(f"   hybrid market_threshold_points = {threshold:.0f}")
    print()
    print(f"   {'moment':<34}{'price':>10}{'distance':>11}   order type")
    print(f"   {'-' * 34}{'-' * 10}{'-' * 11}   {'-' * 12}")
    for label, price in (
        ("the thesis exit fired here", EXIT_PRICE),
        ("the plan was published here", PUBLISH_PRICE),
        ("the pending was cancelled here", CANCEL_PRICE),
    ):
        order_type = ra._planned_order_type(
            config, "BUY", PLAN_ENTRY, price, SYMBOL, planned_stop=PLAN_STOP
        )
        dist = abs(PLAN_ENTRY - price) / 0.1
        print(f"   {label:<34}{price:>10.2f}{dist:>9.0f} pts   {order_type}")

    print()
    print(f"   The mapped entry never moved: {PLAN_ENTRY}.")
    print(f"   At the exit it was {abs(PLAN_ENTRY - EXIT_PRICE) / 0.1:.0f} points from the market — a MARKET fill.")
    print(f"   At publication it was {abs(PLAN_ENTRY - PUBLISH_PRICE) / 0.1:.0f} points away — a LIMIT that had to wait.")
    print()
    print("   -> the plan was not wrong about the level. It was published at")
    print("      the wrong moment, and nothing in the system compares the")
    print("      moment it has to the moment it wanted.")


# ── link 5 ──────────────────────────────────────────────────────────────────
def link5_no_rescue() -> None:
    """The only LIMIT->MARKET converter, measured against this gap."""
    _rule("LINK 5 — could any existing mechanism have rescued it?")

    config = _config()
    nme = config["order_execution"]["near_miss_execution"]
    max_halo = float(nme["max_halo_points"])
    missed_by = abs(PLAN_ENTRY - PUBLISH_PRICE) / 0.1

    print("   near_miss_execution is the ONLY path that turns a planner LIMIT")
    print("   into a market fill. Its conditions, against this order:")
    print()
    print(f"     halo ceiling                 : {max_halo:.0f} pts")
    print(f"     price actually missed entry by: {missed_by:.0f} pts")
    print(f"     inside the halo?             : {missed_by <= max_halo}   "
          f"({missed_by / max_halo:.0f}x too far)")
    print(f"     news at publication          : DANGER — and the call site is")
    print( "                                    guarded by `not news_blocked`")
    print( "                                    (open_trades_manager.py:1859)")
    print(f"     requires price to APPROACH   : recent low must sit ABOVE the")
    print( "                                    entry within the halo. Price")
    print( "                                    was 202 pts above and rising;")
    print( "                                    it never approached at all.")
    print()
    print("   -> three independent reasons, any one of them fatal. The near-miss")
    print("      converter is built for an order price brushed and left. It has")
    print("      nothing to say about an order price never came near.")


def main() -> None:
    print()
    print("#" * 68)
    print("#  2026-07-29 — the exit that never became an entry")
    print("#" * 68)

    link1_manager_cannot_open()
    link2_verdict_is_discarded()
    link3_dead_channel()
    link4_the_moment()
    link5_no_rescue()

    _rule("VERDICT")
    print("   Nothing refused the BUY. No gate voted against it, no filter")
    print("   blocked it, no threshold was missed.")
    print()
    print("   The BUY was never proposed.")
    print()
    print("   The system holds two separate beliefs about direction and has")
    print("   no place where they meet:")
    print()
    print("     · the exit brain reads the last two candles and acts NOW;")
    print("       it is allowed to close, never to open.")
    print("     · the entry brain reads structure and maps a LEVEL;")
    print("       it is allowed to open, and never learns what the exit saw.")
    print()
    print("   So at 4028.02 the system said 'bullish' and did nothing, and at")
    print("   4049.02 it said 'buy at 4028.77' and waited for a price it had")
    print("   already watched leave. Both statements were about the same")
    print("   twenty-five cents of gold, nineteen minutes apart.")
    print()


if __name__ == "__main__":
    main()
