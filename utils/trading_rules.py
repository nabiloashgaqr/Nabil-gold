"""THE single source of truth for the stop / targets / trailing / post-TP2 laws.

Operator directive 2026-08-07 (full audit): one place owns the maths and the
fallback defaults; every door (run_analysis, the risk agent, the session
planner fallback, open-trades management, the post-TP2 guard) calls this
module and nothing else. `tests/test_one_source_of_trading_rules.py` fails if
any other module mentions the rule keys or re-implements the formulae.

The DATA stays in config.json; this module owns the FORMULAE and the default
values used when a key is absent. Changing a number in config.json binds
every door at once -- that is the whole point.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from utils.instruments import points_to_price, price_to_points

# ── the only fallback defaults in the codebase ─────────────────────────────
DEFAULT_STOP_RULE = {"min_liquidity_points": 200.0, "safety_buffer_points": 70.0,
                     "max_stop_points": 400.0}
DEFAULT_MIN_TP1_RR = 0.8
DEFAULT_TP2_MULTIPLE = 2.0
DEFAULT_MAX_TP2_BEYOND_POINTS = 200.0
DEFAULT_POST_TP2 = {"min_distance_points": 250.0, "window_hours": 2.5}
DEFAULT_TRAILING = {"enabled": False, "distance_points": 150.0, "step_points": 40.0}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _block(cfg: Dict[str, Any], name: str) -> Dict[str, Any]:
    """Phase 2: the canonical `trading_rules.<name>` block wins; the legacy
    sections (risk_settings / trade_management / trailing_stop /
    post_tp2_reentry) remain read-compatible aliases."""
    return ((cfg or {}).get("trading_rules") or {}).get(name) or {}


# ── stops ───────────────────────────────────────────────────────────────────

def stop_rule(cfg: Dict[str, Any]) -> Dict[str, float]:
    """The liquidity-rule stop parameters (200 / 70 / 400 on gold today)."""
    raw = _block(cfg, "stop") or ((cfg or {}).get("risk_settings") or {}).get("stop_from_liquidity") or {}
    out = dict(DEFAULT_STOP_RULE)
    for key in out:
        if _f(raw.get(key), 0.0) > 0:
            out[key] = _f(raw.get(key))
    out["enabled"] = bool(raw.get("enabled", True))
    return out


def stop_from_liquidity_points(
    *,
    direction: str,
    entry: float,
    liquidity_map: Dict[str, Any] | None,
    cfg: Dict[str, Any],
    symbol: str,
    rule: Dict[str, Any] | None = None,
) -> float:
    """Distance in POINTS of the rule stop from entry.

    Liquidity closer than min_liquidity_points is noise; the stop sits
    safety_buffer_points beyond the first eligible level; beyond
    max_stop_points (or none eligible) ships the cap directly.
    """
    if rule is not None:
        merged = dict(DEFAULT_STOP_RULE)
        for key in merged:
            if _f(rule.get(key), 0.0) > 0:
                merged[key] = _f(rule.get(key))
        merged["enabled"] = bool(rule.get("enabled", True))
        rule = merged
    else:
        rule = stop_rule(cfg)
    if not rule["enabled"]:
        flat = _f(((cfg or {}).get("risk_settings") or {}).get("min_sl_distance_points"), 0.0)
        return flat
    liquidity_map = liquidity_map or {}
    side = "sell_side" if direction == "BUY" else "buy_side"
    distances: List[float] = []
    for raw in list(liquidity_map.get(side) or []):
        level = _f(raw, 0.0)
        if level <= 0:
            continue
        if (level < entry) if direction == "BUY" else (level > entry):
            distances.append(abs(price_to_points(entry - level, symbol)))
    eligible = sorted(d for d in distances if d >= rule["min_liquidity_points"])
    if not eligible or eligible[0] > rule["max_stop_points"]:
        return rule["max_stop_points"]
    return min(eligible[0] + rule["safety_buffer_points"], rule["max_stop_points"])


# ── targets ─────────────────────────────────────────────────────────────────

def target_ratios(cfg: Dict[str, Any], risk: Dict[str, Any] | None = None,
                  default_min_tp1_rr: float = DEFAULT_MIN_TP1_RR) -> Dict[str, float]:
    """default_min_tp1_rr=0.0 reproduces the old VALIDATOR semantics (skip the
    check when the key is absent); the target law itself enforces 0.8."""
    canon = _block(cfg, "targets")
    risk = risk if risk is not None else ((cfg or {}).get("risk_settings") or {})
    def _pick(canon_key: str, legacy_key: str, default: float) -> float:
        if _f(canon.get(canon_key), 0.0) > 0:
            return _f(canon.get(canon_key))
        return _f(risk.get(legacy_key), default)
    return {
        "min_tp1_rr": _pick("min_tp1_rr", "min_tp1_rr", default_min_tp1_rr) or default_min_tp1_rr,
        "tp2_multiple": _pick("tp2_multiple", "min_tp2_multiple_of_tp1", DEFAULT_TP2_MULTIPLE) or DEFAULT_TP2_MULTIPLE,
        "max_beyond_points": _pick("max_beyond_points", "max_tp2_beyond_tp1_points", DEFAULT_MAX_TP2_BEYOND_POINTS),
    }


def targets_law(
    *,
    direction: str,
    entry: float,
    risk_price: float,
    levels: List[float] | None,
    cfg: Dict[str, Any],
    symbol: str,
    risk_cfg: Dict[str, Any] | None = None,
) -> Tuple[float, float, bool]:
    """(tp1, tp2, used_pool) under the operator's target law.

    TP1 = a real pool within max_beyond of the 0.8R rung, else the rung.
    TP2 = double the TP1 distance; a pool AFTER the adjusted level within
    max_beyond (200 pts) is a better objective and wins (farthest in band).
    """
    ratios = target_ratios(cfg, risk_cfg)
    max_beyond = points_to_price(ratios["max_beyond_points"], symbol)

    def _dist(level: float) -> float:
        return abs(level - entry)

    ordered = sorted(
        {lv for lv in (levels or []) if _f(lv, 0.0) > 0 and
         ((lv > entry) if direction == "BUY" else (lv < entry))},
        key=_dist,
    )

    def _level(rr_r: float) -> float:
        return entry + risk_price * rr_r if direction == "BUY" else entry - risk_price * rr_r

    d_ratio = _dist(_level(ratios["min_tp1_rr"]))
    tp1_pools = [lv for lv in ordered if d_ratio <= _dist(lv) <= d_ratio + max_beyond]
    used_pool = bool(tp1_pools)
    tp1 = min(tp1_pools, key=_dist) if tp1_pools else _level(ratios["min_tp1_rr"])

    d1 = _dist(tp1)
    d2_default = ratios["tp2_multiple"] * d1
    beyond = [lv for lv in ordered if d2_default < _dist(lv) <= d2_default + max_beyond]
    if beyond:
        tp2 = max(beyond, key=_dist)
        used_pool = True
    else:
        tp2 = (entry + d2_default) if direction == "BUY" else (entry - d2_default)
    return round(tp1, 2), round(tp2, 2), used_pool


# ── trailing / breakeven ────────────────────────────────────────────────────

def trailing_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Same priority as the manager always used: trade_management then the
    legacy trailing_stop section."""
    canon = _block(cfg, "trailing")
    tm = (cfg or {}).get("trade_management") or {}
    ts = (cfg or {}).get("trailing_stop") or {}
    out = dict(DEFAULT_TRAILING)
    out["enabled"] = bool(canon.get("enabled", tm.get("trailing_stop_enabled", ts.get("enabled", out["enabled"]))))
    out["distance_points"] = _f(canon.get("distance_points",
        tm.get("trailing_distance_points", ts.get("trailing_distance"))), out["distance_points"])
    out["step_points"] = _f(canon.get("step_points",
        tm.get("trailing_step_points", ts.get("trailing_step"))), out["step_points"])
    out["early_breakeven_points"] = _f(canon.get("early_breakeven_points",
        tm.get("early_breakeven_points", ts.get("early_breakeven_points"))), 200.0)
    out["activation_at"] = str(canon.get("activation_at", tm.get("trailing_activation_at", "TP1")))
    return out


# ── display wording (phase 3) ───────────────────────────────────────────────

def stop_rule_note(cfg: Dict[str, Any]) -> str:
    """The one sentence every card prints about the stop rule; built from the
    live numbers so a config change rewrites every card automatically."""
    r = stop_rule(cfg)
    return (f"Execution stop set by the operator liquidity rule "
            f"(ignore <{r['min_liquidity_points']:.0f} pts, "
            f"+{r['safety_buffer_points']:.0f} safety, "
            f"cap {r['max_stop_points']:.0f}).")


def post_tp2_note(cfg: Dict[str, Any]) -> str:
    r = post_tp2_rule(cfg)
    window = f"{r['window_hours']:g}h"
    return (f"Post-TP2 same-direction re-entry blocked within "
            f"{r['min_distance_points']:.0f} pts of the exhausted TP2 for "
            f"{window} (absolute, no early release).")


# ── post-TP2 re-entry ───────────────────────────────────────────────────────

def post_tp2_rule(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = _block(cfg, "post_tp2") or (cfg or {}).get("post_tp2_reentry") or {}
    out = dict(DEFAULT_POST_TP2)
    if _f(raw.get("min_distance_points"), 0.0) > 0:
        out["min_distance_points"] = _f(raw.get("min_distance_points"))
    if _f(raw.get("window_hours"), 0.0) > 0:
        out["window_hours"] = _f(raw.get("window_hours"))
    out["enabled"] = bool(raw.get("enabled", True))
    return out
