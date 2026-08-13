import hashlib
import json
import time
from pathlib import Path

from agents.auction_flow_agent import AuctionFlowAgent
from agents.classical_agent import ClassicalAgent
from agents.price_action_agent import PriceActionAgent
from agents.smc_agent import SMCAgent
from agents.unified_trend_agent import FEATURE_NAMES, UnifiedTrendAgent
from services.agent_confidence_audit import audit_agent_book
from services.auction_flow_store import AuctionFlowStore
from services.map_quality import bounded_map_display_quality
from services.shared_market_data import publish_shared_market_data
from services.session_planner import SessionPlannerService
from services.timeframe_fusion import fuse_timeframe_results
from services.telegram_bot import TelegramService
from services.thesis_consensus import VOTING_AGENTS
from utils.helpers import get_agent_weights


def _candles(count=240, start=4300.0, step=0.35, seconds=300):
    now = int(time.time()) - count * seconds
    out = []
    for i in range(count):
        close = start + i * step
        out.append({
            "time": now + i * seconds,
            "open": close - step * 0.6,
            "high": close + 0.8,
            "low": close - 0.8,
            "close": close,
            "volume": 100 + i % 17,
        })
    return out


def _native_book():
    seconds = {"5m": 300, "15m": 900, "1H": 3600, "4H": 14400}
    book = {}
    for tf, sec in seconds.items():
        candles = _candles(seconds=sec)
        book[tf] = {
            "symbol": "XAU/USD", "timeframe": tf, "data": candles,
            "current_price": candles[-1]["close"], "source": "mt5",
            "resampled_from": None,
            "source_integrity": {"source": "mt5", "supports_signal_generation": True},
        }
    return {
        "symbol": "XAU/USD", "timeframe": "15m",
        "data": book["15m"]["data"], "timeframes": book,
        "current_price": book["15m"]["current_price"], "source": "mt5",
    }


def _base_config(tmp_path=None):
    cfg = {
        "symbol": "XAU/USD",
        "primary_timeframe": "15m",
        "all_agents_timeframes": {
            "required": ["5m", "15m", "1H", "4H"],
            "require_all": True, "require_native": True,
        },
        "signal_requirements": {"agent_min_confidence": 67},
        "agent_weights": {
            "unified_trend": .20, "classical": .25, "smc": .20,
            "price_action": .20, "auction_flow": .15,
        },
        "unified_trend": {"calibration_required": False},
        "filters": {"max_spread_points": 5},
        "instruments": [{"symbol": "XAU/USD", "point_size": .1}],
    }
    if tmp_path:
        cfg["auction_flow"] = {
            "enabled": True,
            "storage_path": str(tmp_path / "auction.sqlite3"),
            "session_timezone": "UTC",
            "profile_bin_points": 10,
            "value_area_pct": 70,
            "min_unique_ticks_5m": 60,
            "max_tick_age_seconds": 5,
            "max_heartbeat_age_seconds": 10,
            "calibration_required": True,
            "calibration_version": "auction_flow_v1",
            "calibration_path": str(tmp_path / "auction_cal.json"),
            "min_calibration_samples": 300,
        }
    return cfg


def _write_calibration(path: Path, payload):
    raw = dict(payload)
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    raw["checksum"] = hashlib.sha256(canonical).hexdigest()
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_canonical_book_replaces_old_two_agents():
    cfg = _base_config()
    assert get_agent_weights(cfg) == {
        "unified_trend": .20, "classical": .25, "smc": .20,
        "price_action": .20, "auction_flow": .15,
    }
    assert VOTING_AGENTS == (
        "unified_trend", "classical", "smc", "price_action", "auction_flow"
    )
    assert abs(sum(get_agent_weights(cfg).values()) - 1.0) < 1e-9


def test_every_candle_agent_reads_all_four_native_timeframes():
    cfg = _base_config()
    data = _native_book()
    unified = UnifiedTrendAgent(cfg).analyze(data)
    classical = ClassicalAgent(cfg).analyze(data)
    smc = SMCAgent(cfg).analyze(data)
    pa = PriceActionAgent(cfg).analyze(data)
    assert set(unified["timeframe_family_scores"]) == {"5m", "15m", "1H", "4H"}
    for result in (classical, smc, pa):
        assert set(result["timeframe_analysis"]) == {"5m", "15m", "1H", "4H"}
        assert result["timeframe_fusion"]["method"] == "equal_native_timeframes_one_vote"


def test_resampled_timeframe_is_refused_for_every_agent():
    cfg = _base_config()
    data = _native_book()
    data["timeframes"]["4H"]["resampled_from"] = "5m"
    for agent in (UnifiedTrendAgent(cfg), ClassicalAgent(cfg), SMCAgent(cfg), PriceActionAgent(cfg)):
        result = agent.analyze(data)
        assert result["signal"] == "WAIT"
        assert result["confidence"] == 0
        assert "4H" in result.get("missing_timeframes", result.get("data_quality", {}).get("missing_timeframes", []))


def test_auction_flow_starts_with_preloaded_history_and_reads_four_timeframes(tmp_path):
    cfg = _base_config(tmp_path)
    _write_calibration(tmp_path / "auction_cal.json", {
        "version": "auction_flow_v1", "sample_count": 300,
        "logistic": {"a": 0.8, "b": 2.0},
    })
    store = AuctionFlowStore(cfg["auction_flow"]["storage_path"])
    now = time.time()
    price = 4380.0
    for i in range(180):
        mid = price + i * .01
        store.record_tick({
            "time_msc": int((now - 179 + i) * 1000),
            "bid": mid - .10, "ask": mid + .10, "last": 0, "flags": i,
        })
    store.mark_heartbeat()
    result = AuctionFlowAgent(cfg).analyze(_native_book())
    assert result["state"] != "INSUFFICIENT_LIVE_TICKS"
    assert set(result["timeframe_value_context"]) == {"5m", "15m", "1H", "4H"}
    assert result["session_vwap"] > 0
    assert result["poc"] > 0
    assert result["vah"] > 0
    assert result["val"] > 0


def test_auction_bulk_history_aggregation_is_exact(tmp_path):
    store = AuctionFlowStore(tmp_path / "bulk.sqlite3")
    start = int(time.time()) - 100
    ticks = [
        {"time_msc": (start + i) * 1000, "bid": 4300 + i * .01,
         "ask": 4300.2 + i * .01, "last": 0, "flags": i}
        for i in range(100)
    ]
    assert store.bulk_record_ticks(ticks) == 100
    health = store.health(now=start + 100)
    assert health["unique_ticks_5m"] == 100


def test_auction_store_self_heals_if_database_file_is_recreated(tmp_path):
    path = tmp_path / "recreated.sqlite3"
    store = AuctionFlowStore(path)
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    now = int(time.time())
    assert store.record_tick({
        "time_msc": now * 1000, "bid": 4300.0, "ask": 4300.2,
        "last": 0, "flags": 1,
    })
    assert store.meta().get("last_tick_ts_ms") == str(now * 1000)


def test_auction_zero_tick_age_is_fresh_not_infinity(tmp_path):
    cfg = _base_config(tmp_path)
    cfg["auction_flow"]["calibration_required"] = False
    agent = AuctionFlowAgent(cfg)
    problem = agent._data_problem({
        "session_ticks": 100,
        "health": {
            "last_tick_age_seconds": 0.0,
            "heartbeat_age_seconds": 0.0,
            "unique_ticks_5m": 100,
            "current_spread": .2,
        },
    }, .1)
    assert problem is None


def test_all_neutral_smc_frames_show_neutral_confidence_not_fake_zero():
    results = {
        tf: {"agent": "smc", "direction": "NEUTRAL", "signal": "WAIT",
             "confidence": conf, "summary": "neutral SMC"}
        for tf, conf in zip(("5m", "15m", "1H", "4H"), (25, 30, 35, 40))
    }
    fused = fuse_timeframe_results("smc", results)
    assert fused["signal"] == "WAIT"
    assert fused["direction"] == "NEUTRAL"
    assert fused["confidence"] == 32.5
    assert fused["timeframe_fusion"]["directional_count"] == 0


def test_auction_prune_commits_before_best_effort_wal_checkpoint(tmp_path):
    store = AuctionFlowStore(tmp_path / "prune.sqlite3")
    now = int(time.time())
    store.bulk_record_ticks([
        {"time_msc": (now - 10 + i) * 1000, "bid": 4300 + i * .01,
         "ask": 4300.2 + i * .01, "last": 0, "flags": i}
        for i in range(10)
    ])
    # Regression: Windows failed here after calibration with
    # sqlite3.OperationalError: database table is locked.
    store.prune(second_days=7, minute_days=90)
    assert store.health(now=now)["unique_ticks_5m"] == 10


def test_live_orchestration_has_only_new_five_agent_imports_and_one_scheduled_tick_owner():
    root = Path(__file__).resolve().parents[1]
    analysis = (root / "scripts" / "run_analysis.py").read_text(encoding="utf-8")
    assert "from agents.unified_trend_agent import UnifiedTrendAgent" in analysis
    assert "from agents.auction_flow_agent import AuctionFlowAgent" in analysis
    assert "from agents.technical_agent import TechnicalAgent" not in analysis
    assert "from agents.multitimeframe_agent import MultiTimeframeAgent" not in analysis
    tick_manager = (root / "scripts" / "run_tick_manager.py").read_text(encoding="utf-8")
    assert "copy_ticks_from" in tick_manager  # capture every broker tick since the last msc
    assert "self._auction_store.mark_heartbeat()" in tick_manager  # startup permission probe
    installer = (root / "deploy" / "INSTALL_UNIFIED_FIVE_AGENTS.ps1").read_text(encoding="utf-8")
    assert 'Start-ScheduledTask -TaskName "SS_TickManager"' in installer
    assert "python scripts\\run_tick_manager.py" not in installer
    assert "PYTHON_DOTENV_DISABLED" in installer
    assert "Restore-PreviousIncompleteSwitch" in installer
    assert "Stop-TickManagerProcesses" in installer
    assert "Grant-AuctionStorageAccess" in installer
    assert "icacls.exe" in installer
    assert "AUCTION SQLITE SCHEMA OK" in installer
    assert 'execute(\\"' not in installer  # backslash does not escape quotes in PowerShell 5.1
    assert "Write-Error $_" not in installer  # rollback must run before any terminating error
    assert all(ord(char) < 128 for char in installer)  # Windows PowerShell 5.1 parses UTF-8 without BOM as ANSI
    hotfix = (root / "deploy" / "APPLY_SHARED_DATA_PLANNER_FIX.ps1").read_text(encoding="ascii")
    assert "build_agent_calibrations.py" not in hotfix
    assert "Required existing runtime artifact" in hotfix
    assert "storage\\shared_market_data.json" in hotfix


def test_one_fetched_book_is_stored_and_shared_by_all_agents(tmp_path):
    cfg = _base_config()
    cfg["shared_market_data"] = {
        "enabled": True,
        "storage_path": str(tmp_path / "shared_market_data.json"),
    }
    shared = publish_shared_market_data(_native_book(), cfg)
    snapshot_id = shared["shared_market_data"]["snapshot_id"]
    assert snapshot_id.startswith("MT5BOOK::")
    assert Path(shared["shared_market_data"]["storage_path"]).exists()

    class FakeAgent:
        def analyze(self, data):
            assert data is shared
            return {"signal": "WAIT", "confidence": 0}

    from scripts.run_analysis import run_agent
    for name in ("unified_trend", "classical", "smc", "price_action", "auction_flow"):
        result = run_agent(name, FakeAgent(), shared)
        assert result["shared_market_data"]["snapshot_id"] == snapshot_id


def test_core_agents_do_not_fetch_market_data_independently():
    root = Path(__file__).resolve().parents[1]
    files = (
        "agents/unified_trend_agent.py", "agents/classical_agent.py",
        "agents/smc_agent.py", "agents/price_action_agent.py",
        "agents/auction_flow_agent.py",
    )
    for rel in files:
        source = (root / rel).read_text(encoding="utf-8")
        assert "MarketDataService" not in source
        assert "mt5_feed" not in source
        assert "requests.get" not in source


def test_all_observed_but_unqualified_agents_cannot_make_ready_map():
    cfg = _base_config()
    cfg["session_planner"] = {
        "min_supporting_agents_for_ready": 2,
        "max_opposing_agents_for_ready": 1,
        "agent_alignment_min_confidence": 67,
        "min_main_rr_for_ready": 1.5,
        "max_primary_zone_width_points": 450,
        "max_standby_zone_width_points": 450,
    }
    planner = SessionPlannerService(cfg)
    book = {
        "unified_trend": {"signal": "WAIT", "confidence": 51},
        "classical": {"signal": "BUY", "confidence": 57},
        "smc": {"signal": "SELL", "confidence": 61},
        "price_action": {"signal": "SELL", "confidence": 59},
        "auction_flow": {"signal": "WAIT", "confidence": 0},
    }
    ok, reason, diag = planner._plan_quality_guard(
        direction="SELL",
        primary={"entry_price": 4420, "poi_zone": {"bottom": 4416, "top": 4422}},
        standby=None,
        primary_execution={"rr_ratio": 2.0},
        all_results=book,
        symbol="XAU/USD",
    )
    assert ok is False
    assert diag["observed_count"] == 5
    assert diag["available_count"] == 0
    assert diag["unqualified_count"] == 5
    assert "0 qualified supporting agents" in str(reason)


def test_map_header_recounts_only_qualified_core_agents_precisely():
    from scripts.run_analysis import _session_plan_book_summary
    opinions = [
        {"key": "unified_trend", "direction": "SELL", "confidence": 80, "weight": .20},
        {"key": "classical", "direction": "BUY", "confidence": 78, "weight": .25},
        {"key": "smc", "direction": "SELL", "confidence": 61, "weight": .20},
        {"key": "price_action", "direction": "WAIT", "confidence": 40, "weight": .20},
        {"key": "auction_flow", "direction": "SELL", "confidence": 70, "weight": .15},
        {"key": "macro_fundamental", "direction": "SELL", "confidence": 90,
         "weight": None, "external_confirmation": True},
    ]
    book = _session_plan_book_summary(opinions, target_side="SELL", min_confidence=67)
    assert book["supporters"] == ["unified_trend", "auction_flow"]
    assert book["opponents"] == ["classical"]
    assert set(book["inactive_or_unqualified"]) == {"smc", "price_action"}
    assert book["support_count"] == 2
    assert book["opposition_count"] == 1
    assert book["inactive_count"] == 2
    assert "macro_fundamental" not in book["supporters"]


def test_plan_card_shows_weights_admission_and_shared_source_without_frames_at_trend():
    service = TelegramService({})
    captured = {}
    service.send_message = lambda text, **_kwargs: captured.setdefault("text", text) or True
    plan = {
        "symbol": "XAU/USD", "session_bias": "SELL", "plan_status": "READY",
        "planner_confidence": 88, "planner_grade": "A+", "authority_state": "CONFIRMED",
        "session_label": "Test", "session_quality": "HIGH",
        "primary_entry_zone": {"low": 4416, "high": 4422},
        "primary_entry_price": 4420, "invalidation_level": 4447,
        "target_liquidity": 4380,
        "execution_admission": {"allow": True, "path": "THREE_AGENT_CONSENSUS", "confidence": 75},
        "agent_book_summary": {
            "supporters": ["smc"], "opponents": [],
            "support_count": 1, "opposition_count": 0,
            "inactive_or_unqualified": ["unified_trend", "classical", "price_action", "auction_flow"],
            "inactive_count": 4, "matches_execution_admission": True,
        },
        "agent_min_confidence": 67,
        "shared_market_data": {"snapshot_id": "MT5BOOK::same-for-all"},
        "agent_opinions": [
            {"key": "unified_trend", "label": "Unified Trend", "direction": "WAIT", "confidence": 51,
             "weight": .20, "qualified": False, "qualification_bar": 67,
             "summary": "Unified Trend: WAIT 51%"},
            {"key": "smc", "label": "SMC", "direction": "SELL", "confidence": 80,
             "weight": .20, "qualified": True, "qualification_bar": 67,
             "summary": "SMC SELL"},
        ],
    }
    assert service.send_session_plan(plan)
    text = captured["text"]
    assert "Map quality" in text
    assert "quality, not probability" in text
    assert "100.0%" not in text
    assert "Execution admission" in text
    assert "🟢 1 support" in text
    assert "🔴 0 oppose" in text
    assert "⚪ 4 wait/below 67%" in text
    assert "support [smc]" in text
    assert "weight 20%" in text
    assert "NOT QUALIFIED" in text
    assert "⚪ <b>Unified Trend</b>: WAIT" in text
    assert "🔴 <b>SMC</b>: SELL" in text
    assert "one shared stored MT5 snapshot" in text
    trend_line = next(line for line in text.splitlines() if "Unified Trend" in line)
    assert "5m/15m/1H/4H" not in trend_line


def test_map_display_quality_cannot_claim_100_percent_probability():
    plan = {
        "planner_confidence": 100,
        "primary_poi": {
            "thesis_dominance_score": 100,
            "return_probability_score": 100,
            "quality_score": 100,
            "trigger_score": 100,
        },
    }
    quality = bounded_map_display_quality(plan, {
        "session_planner": {"map_display_quality": {"ceiling": 95}}
    })
    assert quality["score"] == 95
    assert quality["not_probability"] is True
    assert quality["legacy_planner_score"] == 100


def test_fusion_confidence_audit_recalculates_arithmetic_exactly():
    cfg = _base_config()
    snapshot = {"snapshot_id": "MT5BOOK::same"}
    results = {}
    for name, confidence in (("classical", 70), ("smc", 75), ("price_action", 80)):
        per_tf = {
            tf: {"direction": "BUY", "confidence": confidence}
            for tf in ("5m", "15m", "1H", "4H")
        }
        result = fuse_timeframe_results(name, per_tf)
        result["shared_market_data"] = snapshot
        results[name] = result
    # Calibrated agents use a known logistic equation whose expected value is
    # recomputed independently by the audit.
    for name in ("unified_trend", "auction_flow"):
        results[name] = {
            "signal": "WAIT", "direction": "WAIT", "confidence": 50.0,
            "raw_edge": 0.0, "shared_market_data": snapshot,
        }
    calibration = {"logistic": {"a": 0.0, "b": 1.0}}
    audit = audit_agent_book(results, cfg, {
        "unified_trend": calibration, "auction_flow": calibration,
    })
    assert audit["ok"] is True
    assert audit["weights_sum"] == 1.0
    assert audit["shared_source_ok"] is True
    assert all(row["arithmetic_ok"] for row in audit["agents"].values())


def test_unified_calibration_feature_contract():
    assert FEATURE_NAMES == (
        "ema", "structure", "momentum", "macd_hist", "macd_slope",
        "rsi_center", "rsi_accel", "rsi_divergence",
    )
