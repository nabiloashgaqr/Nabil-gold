"""Probe: replay the 2026-08-04 09:00 BUY day map through the REAL code.

Question answered by measurement, not by claim:
    With the exact numbers of the published card, which step stops the
    session-plan ladder from creating a pending order?

Measured on both configs:
    * aug-3 live config (agent bar 70 -- what ran at 06:00 UTC)
    * current config    (agent bar 67 -- deployed 16:55 local)

Run:  python scripts/probe_map_refusal_20260804.py
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.helpers import load_config  # noqa: E402
import scripts.run_analysis as ra  # noqa: E402
from services.session_planner import SessionPlannerService  # noqa: E402
from services.dynamic_risk import should_block_signal  # noqa: E402

SEP = "─" * 78

# ── The card, verbatim ──────────────────────────────────────────────────────
CARD_AGENTS = {
    "technical":       {"direction": "WAIT", "confidence": 41.0},
    "classical":       {"direction": "BUY",  "confidence": 74.0},
    "smc":             {"direction": "BUY",  "confidence": 76.0},
    "price_action":    {"direction": "BUY",  "confidence": 84.0},
    "multitimeframe":  {"direction": "BUY",  "confidence": 92.0},
}
MACRO = {"bias": "BEARISH_GOLD", "confidence": 64.0}  # SELL 64%

PLAN_GEOMETRY = {
    "zone_low": 4057.95, "zone_high": 4063.95, "ref_entry": 4060.95,
    "structural_stop": 4057.83,   # pre-floor; <=50 pts so the 150-pt floor binds
    "invalidation": 4045.95,      # = floored stop, as printed on the card
    "tp1": 4075.00, "tp2": 4090.00,
}


def build_plan(readiness_state="PENDING_EXECUTION_READY"):
    g = PLAN_GEOMETRY
    return {
        "plan_ready": True,
        "plan_status": "READY",
        "symbol": "XAU/USD",
        "session_bias": "BUY",
        "session_label": "Asia Morning",
        "planner_grade": "A+",
        "planner_confidence": 97.8,
        "authority_state": "WEAK",
        "authority_direction": "BUY",
        "preferred_execution_family": "FAILED_RECLAIM_CONTINUATION",
        "execution_readiness": {"state": readiness_state, "reason": "probe"},
        "primary_poi": {
            "direction": "BUY",
            "entry_price": g["ref_entry"],
            "stop_loss": g["structural_stop"],
            "target_price": g["tp2"],
            "target_liquidity": g["tp2"],
            "poi_zone": {"top": g["zone_high"], "bottom": g["zone_low"]},
            "poi_low": g["zone_low"],
            "poi_high": g["zone_high"],
            "setup_state": "ENTRY_ARMED",
            "trigger_state": "FAILED_RECLAIM_CONFIRMED",
            "trigger_ready": True,
            "scenario_type": "FAILED_RECLAIM_CONTINUATION",
            "poi_classification": "EXTREME_POI",
            "quality_score": 97.8,
        },
        "standby_poi": {},
    }


def base_decision(price: float, with_plan=True):
    d = {
        "symbol": "XAU/USD",
        "decision": "WAIT",  # live consensus read WAIT (Technical 41 drags it)
        "confidence": 0.0,
        "current_price": price,
        "agent_details": {k: dict(v) for k, v in CARD_AGENTS.items()},
    }
    if with_plan:
        d["session_plan"] = build_plan()
    return d


class FakeDB:
    """Empty history: no open trades, no recent trades, nothing to duplicate."""
    def get_open_trades(self): return []
    def get_recent_trades(self, limit=50): return []
    def get_recent_closed_trades(self, limit=150): return []
    def get_trades_closed_since(self, *a, **k): return []
    def get_recent_signals(self, *a, **k): return []
    def new_trade_id(self): return "TRADE_20260804_060000_000000_probe001"
    def save_trade(self, row): print(f"      [db] save_trade -> {row.get('trade_id')}")
    def update_trade(self, *a, **k): pass
    def __getattr__(self, name):
        def _noop(*a, **k):
            return []
        return _noop


class FakeTelegram:
    def send_signal(self, decision):
        sig = decision.get("signal", {})
        print(f"      [telegram] SIGNAL SENT: {decision.get('decision')} "
              f"{sig.get('order_type')} @ {(sig.get('entry') or {}).get('price')}")
        return True
    def send_scenario_governance(self, *a, **k): return True
    def __getattr__(self, name):
        def _noop(*a, **k):
            return True
        return _noop


def section(title):
    print(f"\n{SEP}\n{title}\n{SEP}")


def gate_count(config, decision):
    gate = ra._planner_execution_gate(decision, config)
    return gate


def main():
    config_now = load_config()
    aug3_path = Path("/tmp/config_aug3.json")
    config_aug3 = json.loads(aug3_path.read_text()) if aug3_path.exists() else None

    bar_now = config_now["signal_requirements"]["agent_min_confidence"]
    bar_aug3 = (config_aug3 or {}).get("signal_requirements", {}).get("agent_min_confidence")

    section(f"0) CONFIG UNDER TEST · aug-3 live bar={bar_aug3} · current bar={bar_now}")

    # ── 1) The execution gate, exact card numbers ──────────────────────────
    section("1) EXECUTION GATE · exact card reads (4 BUY agents ≥ 70)")
    for label, cfg in (("aug-3 (bar 70)", config_aug3), ("current (bar 67)", config_now)):
        if cfg is None:
            continue
        g = gate_count(cfg, base_decision(4061.0))
        print(f"  [{label}] allow={g.get('allow')} kind={g.get('kind')} "
              f"support={g.get('support_count')} oppose={g.get('oppose_count')}")
        print(f"             reason: {g.get('reason')}")

    # drift: how far must one BUY agent fall before the gate flips?
    section("2) GATE DRIFT · lowering one BUY agent's confidence (aug-3 bar 70)")
    for agent in ("classical", "smc", "price_action", "multitimeframe"):
        flip = None
        for c in range(92, 50, -1):
            d = base_decision(4061.0)
            d["agent_details"][agent]["confidence"] = float(c)
            if not ra._planner_execution_gate(d, config_aug3).get("allow"):
                flip = c
                break
        print(f"  {agent:>16}: gate flips to REFUSE when confidence reaches {flip}% "
              f"(card value {CARD_AGENTS[agent]['confidence']:.0f}%)")

    # ── 3) execution readiness (planner layer, alignment bar) ──────────────
    section("3) EXECUTION READINESS · SessionPlannerService._execution_readiness")
    for label, cfg in (("aug-3", config_aug3), ("current", config_now)):
        sp = SessionPlannerService(cfg)
        align_bar = sp.agent_alignment_min_confidence
        all_results = {k: {"signal": v["direction"], "confidence": v["confidence"]}
                       for k, v in CARD_AGENTS.items()}
        all_results["smc"] = {"signal": "BUY", "confidence": 76.0}
        plan = build_plan()
        r = sp._execution_readiness(
            planner_source="session_map",
            direction="BUY",
            primary=plan["primary_poi"],
            standby=None,
            all_results=all_results,
            preferred_execution_family="FAILED_RECLAIM_CONTINUATION",
            macro=MACRO,
        )
        print(f"  [{label}] alignment_bar={align_bar} → state={r['state']} · {r['reason']}")
        # SMC drift: when does readiness leave the executable states?
        for c in (76, 68, 60, 55, 50):
            ar = copy.deepcopy(all_results)
            ar["smc"]["confidence"] = float(c)
            rr = sp._execution_readiness(
                planner_source="session_map", direction="BUY",
                primary=plan["primary_poi"], standby=None, all_results=ar,
                preferred_execution_family="FAILED_RECLAIM_CONTINUATION", macro=MACRO)
            print(f"      SMC@{c:>2} → {rr['state']}")

    # ── 4) dynamic risk probe ────────────────────────────────────────────────
    section("4) DYNAMIC RISK · should_block_signal (planner probe conf=97.8)")
    probe = {"decision": "BUY", "confidence": 97.8, "quality": {"score": 97.8}}
    for level, min_conf, min_qual, can_trade in (
        ("NORMAL", 0, 0, True), ("CAUTION", 75, 70, True),
        ("STRICT", 82, 80, True), ("HALT", 0, 0, False)):
        dr = {"enabled": True, "level": level, "can_trade": can_trade,
              "warnings": ["3 consecutive losses: trading halted"],
              "min_confidence_required": min_conf, "min_quality_score": min_qual}
        blocked = should_block_signal(probe, dr)
        print(f"  {level:>8}: {'BLOCKED — ' + blocked if blocked else 'passes'}")

    # ── 5) price sweep through the real ladder ──────────────────────────────
    for label, cfg in (("AUG-3 LIVE (bar 70)", config_aug3), ("CURRENT (bar 67)", config_now)):
        if cfg is None:
            continue
        section(f"5) FULL LADDER REPLAY · {label} · price sweep 4055→4070 "
                f"(card numbers, empty history, telegram OK)")
        prices = [4055.0, 4057.0, 4058.0, 4059.0, 4060.0, 4060.95, 4062.0,
                  4063.5, 4063.95, 4065.0, 4067.0, 4070.0]
        for price in prices:
            ra._LAST_LADDER_STOP.clear()
            db, tg = FakeDB(), FakeTelegram()
            created = ra._execute_session_plan_ladder(
                base_decision(price), {"session_plan": build_plan()},
                [], db, tg, cfg)
            stop = dict(ra._LAST_LADDER_STOP)
            reason = stop.get("reason") if not created else "—"
            extra = {k: v for k, v in stop.items() if k != "reason"}
            print(f"  price {price:>7.2f} → orders={created}  "
                  f"{('STOP: ' + str(reason)) if reason != '—' else 'ORDER CREATED'}"
                  f"{'  ' + json.dumps(extra, ensure_ascii=False) if extra else ''}")

    # ── 6) one instrumented close look at the publishing price ──────────────
    section("6) LEG PRICING DETAIL · _build_plan_ladder_decision @ price 4061 (aug-3)")
    leg = ra._build_plan_ladder_decision(base_decision(4061.0), build_plan(),
                                         build_plan()["primary_poi"], config_aug3 or config_now)
    if leg is None:
        print("  leg = None (no order possible)")
    else:
        sig = leg.get("signal", {})
        print(f"  order_type={sig.get('order_type')} entry={ (sig.get('entry') or {}).get('price') } "
              f"sl={sig.get('stop_loss')} tp1={sig.get('tp1')} tp2={sig.get('tp2')} "
              f"rr={(leg.get('risk') or {}).get('rr_ratio') or leg.get('rr_ratio')}")

    print(f"\n{SEP}\nprobe complete\n{SEP}")


if __name__ == "__main__":
    main()
