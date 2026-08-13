"""Post-install fail-closed verification for the unified five-agent switch."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.auction_flow_agent import AuctionFlowAgent
from agents.unified_trend_agent import UnifiedTrendAgent
from services.auction_flow_store import AuctionFlowStore
from services.thesis_consensus import VOTING_AGENTS
from utils.helpers import get_agent_weights, is_market_open, load_config

EXPECTED = {
    "unified_trend": .20,
    "classical": .25,
    "smc": .20,
    "price_action": .20,
    "auction_flow": .15,
}


def main() -> int:
    cfg = load_config()
    assert cfg.get("trading_mode") == "mt5_demo"
    assert ((cfg.get("execution") or {}).get("execution_mode")) == "mt5_demo"
    assert get_agent_weights(cfg) == EXPECTED
    assert abs(sum(EXPECTED.values()) - 1.0) < 1e-9
    assert tuple(VOTING_AGENTS) == tuple(EXPECTED)
    assert not ({"technical", "multitimeframe"} & set(get_agent_weights(cfg)))
    tf = cfg.get("all_agents_timeframes") or {}
    assert tf.get("required") == ["5m", "15m", "1H", "4H"]
    assert tf.get("require_all") is True and tf.get("require_native") is True
    assert (cfg.get("data_source") or {}).get("resample_timeframes_from_base") is False
    assert (cfg.get("signal_requirements") or {}).get("agent_min_confidence") == 67
    assert (cfg.get("signal_requirements") or {}).get("min_consensus_confidence") == 72
    assert (cfg.get("signal_requirements") or {}).get("min_agents_agree") == 3
    assert UnifiedTrendAgent(cfg).calibration is not None
    assert AuctionFlowAgent(cfg).calibration is not None
    print("5 ACTIVE AGENTS OK")
    print("NATIVE M5/M15/H1/H4 FOR EVERY AGENT OK")
    print("UNIFIED TREND CALIBRATION OK")
    print("AUCTION FLOW CALIBRATION OK")

    store = AuctionFlowStore(str((cfg.get("auction_flow") or {}).get("storage_path")))
    health = store.health()
    if is_market_open():
        assert health["last_tick_age_seconds"] <= float((cfg.get("auction_flow") or {}).get("max_tick_age_seconds", 5))
        assert health["heartbeat_age_seconds"] <= float((cfg.get("auction_flow") or {}).get("max_heartbeat_age_seconds", 10))
        assert health["unique_ticks_5m"] >= int((cfg.get("auction_flow") or {}).get("min_unique_ticks_5m", 60))
        print("AUCTION FLOW COLLECTOR OK")
    else:
        print("AUCTION FLOW COLLECTOR READY (MARKET CLOSED)")

    tg = Path("services/telegram_bot.py").read_text(encoding="utf-8")
    dash = Path("dashboard/index.html").read_text(encoding="utf-8")
    assert "unified_trend" in tg and "auction_flow" in tg
    assert "Unified Trend Agent" in dash and "Auction Flow Agent" in dash
    print("TELEGRAM AGENT NAMES OK")
    print("DASHBOARD AGENT NAMES OK")
    print("MT5 DEMO ONLY OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
