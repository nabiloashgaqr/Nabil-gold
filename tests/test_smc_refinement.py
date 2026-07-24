from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from agents.smc_agent import SMCAgent
from utils.indicators import detect_swing_points


def _candle(ts: datetime, o: float, h: float, l: float, c: float) -> dict:
    return {
        "time": ts.isoformat().replace("+00:00", "Z"),
        "open": round(o, 2),
        "high": round(h, 2),
        "low": round(l, 2),
        "close": round(c, 2),
        "volume": 1000,
    }


def test_previous_day_levels_and_session_liquidity() -> None:
    agent = SMCAgent({"symbol": "XAU/USD"})
    start = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
    candles = [
        _candle(start + timedelta(hours=0), 4040, 4046, 4038, 4044),
        _candle(start + timedelta(hours=2), 4044, 4050, 4041, 4047),
        _candle(start + timedelta(hours=4), 4047, 4049, 4040, 4042),
        _candle(start + timedelta(days=1, hours=1), 4042, 4048, 4041, 4047),
        _candle(start + timedelta(days=1, hours=4), 4047, 4049, 4043, 4044),
        _candle(start + timedelta(days=1, hours=5), 4044, 4046, 4040, 4041),
    ]
    prev = agent._previous_day_levels(candles)
    session = agent._session_liquidity(candles)
    assert prev["high"] == 4050.0
    assert prev["low"] == 4038.0
    assert session["label"] in {"London + New York Afternoon", "London / Europe Midday", "New York Evening", "Asia Morning", "Late New York Night"}
    assert session["high"] is not None and session["low"] is not None


def test_recent_sweep_detects_previous_day_high_reference() -> None:
    agent = SMCAgent({"symbol": "XAU/USD"})
    start = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
    candles = []
    # Previous day establishes a higher liquidity pool at 4050.
    candles.append(_candle(start, 4040, 4050, 4038, 4046))
    # Current day candles remain below 4048 until the sweep candle.
    current_start = datetime(2026, 7, 15, 6, 0, tzinfo=timezone.utc)
    price = 4042.0
    for i in range(18):
        o = price
        c = price + (0.2 if i % 2 == 0 else -0.1)
        h = max(o, c) + 0.8
        l = min(o, c) - 0.7
        candles.append(_candle(current_start + timedelta(minutes=15 * i), o, min(h, 4048.0), l, c))
        price = c
    # Sweep previous-day high and close back below it, but above recent highs.
    candles.append(_candle(current_start + timedelta(minutes=15 * 18), 4047.2, 4052.0, 4045.5, 4049.2))

    swings = detect_swing_points(candles[-60:], lookback=3)
    liquidity = agent._detect_liquidity(candles, swings, tolerance=0.6)
    sweep = liquidity["recent_sweep"]
    assert sweep["occurred"] is True
    assert sweep["type"] == "buy_side"
    assert sweep["reference_type"] == "previous_day_high"


def test_equal_highs_detail_exposes_touch_quality() -> None:
    agent = SMCAgent({"symbol": "XAU/USD"})
    detail = agent._cluster_liquidity_details([4050.0, 4050.2, 4049.9, 4050.1, 4038.0], tolerance=0.4)
    assert detail
    assert detail[0]["touches"] >= 4
    assert detail[0]["quality"] == "STRONG"


def test_day_archetype_prefers_continuation_after_sweep_day() -> None:
    agent = SMCAgent({"symbol": "XAU/USD"})
    archetype = agent._day_archetype(
        direction="BUY",
        market_structure={"trend": "BULLISH", "structure_quality": "STRONG"},
        liquidity={"recent_sweep": {"occurred": True, "type": "sell_side", "confirmation": "STRONG"}},
        zone="DISCOUNT",
        setup_candidates=[{"setup_type": "STRUCTURE_CONTINUATION", "trigger_state": "AT_POI_WAIT_TRIGGER", "thesis_dominance_score": 74}],
    )
    assert archetype["name"] == "CONTINUATION_AFTER_SWEEP_DAY"
    assert archetype["preferred_execution_family"] == "MITIGATION_LADDER"
