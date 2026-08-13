"""Phase-1 parity lock (full audit 2026-08-10): the DECISION path must be
blind to execution mode. Demo and paper may differ only in candles source
and execution venue — never in rules."""
import inspect
from pathlib import Path


def test_analysis_env_reads_are_management_gate_only():
    """EXECUTION_MODE may appear ONLY as the single-owner management gate
    (skip embedded update_trades on demo); never in decision logic, and
    TRADES_TABLE never at all."""
    src = Path("scripts/run_analysis.py").read_text(encoding="utf-8")
    assert "TRADES_TABLE" not in src
    assert src.count('EXECUTION_MODE') == 1
    assert 'EXECUTION_MODE") == "mt5_demo"' in src
    assert "DemoHandoffDB" in src  # decisions flow through the handoff only


def test_agents_env_reads_are_test_hooks_only():
    for f in ("agents/macro_fundamental_agent.py", "agents/news_risk_agent.py"):
        src = Path(f).read_text(encoding="utf-8")
        for bad in ("EXECUTION_MODE", "TRADES_TABLE", "DATA_SOURCE_PRIMARY"):
            assert bad not in src, f


def test_planned_order_type_env_independent():
    import scripts.run_analysis as ra
    import os
    sig = inspect.signature(ra._planned_order_type)
    params = set(sig.parameters)
    assert not (params & {"execution_mode", "trades_table"})
    cfg = {"order_execution": {"entry_style": "pending",
                              "pending_threshold_points": 20}}
    os.environ.pop("EXECUTION_MODE", None)
    a = ra._planned_order_type(cfg, "BUY", 4330.0, 4300.0, "XAU/USD")
    os.environ["EXECUTION_MODE"] = "mt5_demo"
    try:
        b = ra._planned_order_type(cfg, "BUY", 4330.0, 4300.0, "XAU/USD")
    finally:
        os.environ.pop("EXECUTION_MODE", None)
    # BUY mapped ABOVE market is chasing -> forced MARKET (anti-chase rule);
    # BUY mapped BELOW market (dip) -> LIMIT. Same in both envs.
    assert a == b == "BUY_MARKET"
    dip_a = ra._planned_order_type(cfg, "BUY", 4290.0, 4300.0, "XAU/USD")
    os.environ["EXECUTION_MODE"] = "mt5_demo"
    try:
        dip_b = ra._planned_order_type(cfg, "BUY", 4290.0, 4300.0, "XAU/USD")
    finally:
        os.environ.pop("EXECUTION_MODE", None)
    assert dip_a == dip_b == "BUY_LIMIT"
