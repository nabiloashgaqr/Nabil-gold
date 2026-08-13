"""Read-only live inspection of the shared five-agent book and Auction Flow.

Does not run analysis orchestration, send Telegram, save trades, or touch MT5
orders. It reads the already stored shared candle snapshot + Auction SQLite.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from agents.auction_flow_agent import AuctionFlowAgent
from agents.classical_agent import ClassicalAgent
from agents.price_action_agent import PriceActionAgent
from agents.smc_agent import SMCAgent
from agents.unified_trend_agent import UnifiedTrendAgent
from services.agent_confidence_audit import audit_agent_book
from services.thesis_consensus import evaluate_directional_admission
from utils.helpers import get_agent_weights, load_config

CORE = (
    ("unified_trend", "Unified Trend", UnifiedTrendAgent),
    ("classical", "Classical", ClassicalAgent),
    ("smc", "SMC", SMCAgent),
    ("price_action", "Price Action", PriceActionAgent),
    ("auction_flow", "Auction Flow", AuctionFlowAgent),
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _latest_plan() -> Dict[str, Any]:
    path = ROOT / "storage" / "session_plans.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        rows = [row for row in rows if isinstance(row, dict)]
        return max(
            rows,
            key=lambda row: str(
                row.get("analysis_run_at") or row.get("updated_at")
                or row.get("created_at") or ""
            ),
            default={},
        )
    except Exception:
        return {}


def _calibration(config: Dict[str, Any], key: str) -> Dict[str, Any]:
    path = Path((config.get(key) or {}).get("calibration_path") or f"storage/model_calibration/{key}_v1.json")
    if not path.is_absolute():
        path = ROOT / path
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    config = load_config()
    shared_path = Path(
        (config.get("shared_market_data") or {}).get("storage_path")
        or "storage/shared_market_data.json"
    )
    if not shared_path.is_absolute():
        shared_path = ROOT / shared_path
    if not shared_path.exists():
        raise SystemExit(f"Shared snapshot not found: {shared_path}")
    data = json.loads(shared_path.read_text(encoding="utf-8"))
    shared = data.get("shared_market_data") or {}
    snapshot_id = str(shared.get("snapshot_id") or "MISSING")
    weights = get_agent_weights(config)
    min_conf = _f((config.get("signal_requirements") or {}).get("agent_min_confidence"), 67.0)

    results: Dict[str, Dict[str, Any]] = {}
    rows = []
    for key, label, cls in CORE:
        result = cls(config).analyze(data)
        # The production run_agent wrapper attaches this same metadata to every
        # result. The standalone checker calls agents directly, so mirror that
        # attachment before running the shared-source integrity audit.
        if isinstance(result, dict):
            result["shared_market_data"] = dict(shared)
        results[key] = result
        direction = str(result.get("signal") or result.get("direction") or "WAIT").upper()
        confidence = _f(result.get("confidence"), 0.0)
        qualified = direction in {"BUY", "SELL"} and confidence >= min_conf
        rows.append({
            "agent": key,
            "label": label,
            "direction": direction,
            "confidence": round(confidence, 1),
            "weight": _f(weights.get(key), 0.0),
            "qualified": qualified,
            "shared_snapshot_id": snapshot_id,
            "summary": str(result.get("summary") or ""),
        })

    auction = results.get("auction_flow") or {}
    trend = results.get("unified_trend") or {}
    smc = results.get("smc") or {}
    plan_row = _latest_plan()
    plan = plan_row.get("payload") if isinstance(plan_row.get("payload"), dict) else plan_row
    plan = plan if isinstance(plan, dict) else {}
    plan_side = str(plan.get("session_bias") or plan.get("authority_direction") or "WAIT").upper()

    admission_buy = evaluate_directional_admission("BUY", results, config)
    admission_sell = evaluate_directional_admission("SELL", results, config)
    confidence_audit = audit_agent_book(
        results,
        config,
        calibrations={
            "unified_trend": _calibration(config, "unified_trend"),
            "auction_flow": _calibration(config, "auction_flow"),
        },
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "shared_data": {
            "snapshot_id": snapshot_id,
            "source": shared.get("source"),
            "stored": shared.get("stored"),
            "fetched_once": shared.get("fetched_once"),
            "storage_path": str(shared_path),
            "timeframes": shared.get("timeframes"),
        },
        "policy": {
            "agent_min_confidence": min_conf,
            "weights": weights,
        },
        "agents": rows,
        "confidence_integrity": confidence_audit,
        "auction_flow": {
            "direction": auction.get("signal") or auction.get("direction"),
            "raw_direction": auction.get("raw_direction"),
            "confidence": auction.get("confidence"),
            "qualified": next((r["qualified"] for r in rows if r["agent"] == "auction_flow"), False),
            "weight": weights.get("auction_flow"),
            "state": auction.get("state"),
            "raw_edge": auction.get("raw_edge"),
            "base_edge": auction.get("base_edge"),
            "coherence": auction.get("coherence"),
            "data_quality_score": auction.get("data_quality_score"),
            "family_scores": auction.get("family_scores"),
            "timeframe_value_context": auction.get("timeframe_value_context"),
            "session_vwap": auction.get("session_vwap"),
            "poc": auction.get("poc"),
            "vah": auction.get("vah"),
            "val": auction.get("val"),
            "tick_imbalance_60s": auction.get("tick_imbalance_60s"),
            "tick_imbalance_5m": auction.get("tick_imbalance_5m"),
            "activity_ratio": auction.get("activity_ratio"),
            "spread_points": auction.get("spread_points"),
            "flow_health": auction.get("flow_health"),
            "calibration": auction.get("calibration"),
            "reasons": auction.get("reasons"),
            "warnings": auction.get("warnings"),
            "summary": auction.get("summary"),
        },
        "smc": {
            "direction": smc.get("signal") or smc.get("direction"),
            "confidence": smc.get("confidence"),
            "weight": weights.get("smc"),
            "market_structure": smc.get("market_structure"),
            "zone": smc.get("zone"),
            "timeframe_analysis": smc.get("timeframe_analysis"),
            "timeframe_fusion": smc.get("timeframe_fusion"),
            "setup_candidates_count": len(smc.get("setup_candidates") or []),
            "setup_structure": smc.get("setup_structure"),
            "liquidity": smc.get("liquidity"),
            "signals": smc.get("signals"),
            "summary": smc.get("summary"),
        },
        "unified_trend": {
            "direction": trend.get("signal") or trend.get("direction"),
            "raw_direction": trend.get("raw_direction"),
            "confidence": trend.get("confidence"),
            "weight": weights.get("unified_trend"),
            "raw_edge": trend.get("raw_edge"),
            "coherence": trend.get("coherence"),
            "family_scores": trend.get("family_scores"),
            "calibration": trend.get("calibration"),
        },
        "current_core_admission": {
            "BUY": admission_buy,
            "SELL": admission_sell,
        },
        "latest_map": {
            "plan_id": plan.get("plan_id"),
            "side": plan_side,
            "status": plan.get("plan_status"),
            "ready": bool(plan.get("plan_ready")),
            "quality": plan.get("planner_confidence"),
            "authority": plan.get("authority_state"),
            "auction_same_direction": (
                str(auction.get("signal") or auction.get("direction") or "WAIT").upper() == plan_side
            ) if plan_side in {"BUY", "SELL"} else None,
            "auction_qualified": next((r["qualified"] for r in rows if r["agent"] == "auction_flow"), False),
        },
    }

    out_dir = ROOT / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"agents_now_{stamp}.json"
    txt_path = out_dir / f"agents_now_{stamp}.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "SHARED FIVE-AGENT LIVE READ (READ ONLY)",
        "=" * 58,
        f"Snapshot: {snapshot_id}",
        f"Source: {shared.get('source')} | fetched_once={shared.get('fetched_once')} | stored={shared.get('stored')}",
        f"Agent bar: {min_conf:.0f}%",
        "",
        "AGENTS",
    ]
    for row in rows:
        lines.append(
            f"- {row['label']:<15} {row['direction']:<4} {row['confidence']:>5.1f}% "
            f"| weight {row['weight'] * 100:>4.0f}% | "
            f"{'QUALIFIED' if row['qualified'] else 'NOT QUALIFIED'}"
        )
    lines.extend([
        "",
        "CONFIDENCE ARITHMETIC AUDIT",
        f"- overall: {'PASS' if confidence_audit.get('ok') else 'FAIL'}",
        f"- one shared snapshot: {confidence_audit.get('shared_source_ok')} {confidence_audit.get('shared_snapshot_ids')}",
        f"- weights sum: {confidence_audit.get('weights_sum')} (ok={confidence_audit.get('weights_ok')})",
    ])
    for name, check in (confidence_audit.get("agents") or {}).items():
        lines.append(
            f"- {name}: {'PASS' if check.get('ok') else 'FAIL'} | "
            f"actual={check.get('actual_confidence')} recalculated={check.get('recalculated_confidence')} "
            f"diff={check.get('difference')} method={check.get('method')}"
        )
    lines.extend([
        "",
        "AUCTION FLOW DETAIL",
        f"- state: {auction.get('state')}",
        f"- direction: {auction.get('signal') or auction.get('direction')} (raw={auction.get('raw_direction')})",
        f"- confidence: {auction.get('confidence')}% | weight {weights.get('auction_flow', 0) * 100:.0f}%",
        f"- edge/coherence: {auction.get('raw_edge')} / {auction.get('coherence')}",
        f"- family scores: {auction.get('family_scores')}",
        f"- tick imbalance 60s/5m: {auction.get('tick_imbalance_60s')} / {auction.get('tick_imbalance_5m')}",
        f"- activity/spread: {auction.get('activity_ratio')} / {auction.get('spread_points')} pts",
        f"- VWAP/POC/VAH/VAL: {auction.get('session_vwap')} / {auction.get('poc')} / {auction.get('vah')} / {auction.get('val')}",
        f"- timeframe value context: {auction.get('timeframe_value_context')}",
        f"- health: {auction.get('flow_health')}",
        f"- calibration: {auction.get('calibration')}",
        f"- reasons: {auction.get('reasons') or auction.get('warnings')}",
        "",
        "SMC DETAIL",
        f"- direction/confidence: {smc.get('signal') or smc.get('direction')} / {smc.get('confidence')}%",
        f"- per-timeframe reads: {smc.get('timeframe_analysis')}",
        f"- fusion: {smc.get('timeframe_fusion')}",
        f"- structure/zone: {smc.get('market_structure')} / {smc.get('zone')}",
        f"- setup candidates: {len(smc.get('setup_candidates') or [])}",
        f"- signals: {smc.get('signals')}",
        f"- summary: {smc.get('summary')}",
        "",
        "CURRENT ADMISSION",
        f"- BUY : allow={admission_buy.get('allow')} path={admission_buy.get('path')} support={admission_buy.get('support_count')} net={admission_buy.get('confidence')}%",
        f"- SELL: allow={admission_sell.get('allow')} path={admission_sell.get('path')} support={admission_sell.get('support_count')} net={admission_sell.get('confidence')}%",
        "",
        "LATEST MAP",
        f"- side/status/ready: {plan_side} / {plan.get('plan_status')} / {bool(plan.get('plan_ready'))}",
        f"- map quality/authority: {plan.get('planner_confidence')} / {plan.get('authority_state')}",
        f"- Auction aligns/qualified: {report['latest_map']['auction_same_direction']} / {report['latest_map']['auction_qualified']}",
        "",
        f"JSON: {json_path}",
        f"TXT : {txt_path}",
    ])
    text = "\n".join(lines)
    txt_path.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
