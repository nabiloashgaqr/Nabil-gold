"""Phase-3 shadow cycle (demo branch, no VPS yet).

Runs the FULL paper pipeline against the isolated book trades_demo so the
demo branch proves itself end-to-end on GitHub, then emits the daily
paper-vs-demo comparison. When the VPS arrives, switch EXECUTION_MODE to
mt5_demo and the same cycle transmits to MT5 demo.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["TRADES_TABLE"] = os.environ.get("TRADES_TABLE", "trades_demo")
os.environ.setdefault("EXECUTION_MODE", "paper")


def main() -> None:
    from scripts.run_analysis import main as analysis_main
    from scripts.run_trade_updates import main as updates_main
    from scripts.demo_compare_report import run_compare

    analysis_main()
    updates_main()
    run_compare()


if __name__ == "__main__":
    main()
