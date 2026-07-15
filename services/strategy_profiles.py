"""Strategy-profile selection for setup-aware decisioning.

Sprint 3 foundation: choose a decision profile from the detected setup context so
thresholds and weights can adapt by setup type instead of using one global
consensus rule for every market condition.
"""

from __future__ import annotations

from typing import Any, Dict


DEFAULT_PROFILES: Dict[str, Dict[str, Any]] = {
    "classic_consensus": {
        "name": "classic_consensus",
        "setup_types": ["*"],
        "min_agents_agree": 3,
        "min_consensus_confidence": 72,
        "agent_min_confidence": 70,
        "lead_agent": None,
        "require_lead_alignment": False,
        "description": "Legacy 5-agent weighted consensus.",
    },
    "liquidity_reversal": {
        "name": "liquidity_reversal",
        "setup_types": ["LIQUIDITY_REVERSAL", "REVERSAL_ATTEMPT"],
        "min_agents_agree": 2,
        "min_consensus_confidence": 70,
        "agent_min_confidence": 68,
        "lead_agent": "smc",
        "require_lead_alignment": True,
        "weight_overrides": {
            "smc": 0.35,
            "price_action": 0.25,
            "multitimeframe": 0.20,
            "classical": 0.10,
            "technical": 0.10,
        },
        "description": "SMC-led reversal profile: sweep + POI + reaction can qualify with 2 strong aligned agents.",
    },
    "trend_pullback": {
        "name": "trend_pullback",
        "setup_types": ["ORDER_BLOCK_PULLBACK", "STRUCTURE_CONTINUATION", "TREND_CONTINUATION", "PULLBACK_ENTRY"],
        "min_agents_agree": 3,
        "min_consensus_confidence": 72,
        "agent_min_confidence": 70,
        "lead_agent": "multitimeframe",
        "require_lead_alignment": True,
        "weight_overrides": {
            "multitimeframe": 0.30,
            "classical": 0.25,
            "price_action": 0.20,
            "smc": 0.15,
            "technical": 0.10,
        },
        "description": "Trend-pullback profile favouring HTF alignment and structure continuation.",
    },
    "range_fade": {
        "name": "range_fade",
        "setup_types": ["RANGE_FADE", "SMC_CONTEXT", "MIXED_ALIGNMENT"],
        "min_agents_agree": 2,
        "min_consensus_confidence": 71,
        "agent_min_confidence": 68,
        "lead_agent": "price_action",
        "require_lead_alignment": False,
        "weight_overrides": {
            "price_action": 0.30,
            "classical": 0.25,
            "smc": 0.20,
            "technical": 0.15,
            "multitimeframe": 0.10,
        },
        "description": "Range/extreme reaction profile with softer lead-agent enforcement.",
    },
}


def _normalized_setup_type(agents_results: Dict[str, Any]) -> str:
    setup = agents_results.get("setup_context") or {}
    if isinstance(setup, dict) and setup.get("setup_type"):
        return str(setup.get("setup_type")).upper()
    smc = agents_results.get("smc", {}) or {}
    smc_structure = smc.get("setup_structure") or {}
    if isinstance(smc_structure, dict) and smc_structure.get("setup_type"):
        return str(smc_structure.get("setup_type")).upper()
    mtf = agents_results.get("multitimeframe", {}) or {}
    if mtf.get("setup_type"):
        return str(mtf.get("setup_type")).upper()
    return "CLASSIC_CONSENSUS"


def select_strategy_profile(config: Dict[str, Any], agents_results: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(config, dict) and config.get("strategy_profiles_enabled") is False:
        fallback = dict(DEFAULT_PROFILES["classic_consensus"])
        fallback["resolved_setup_type"] = _normalized_setup_type(agents_results)
        return fallback
    custom = (config.get("strategy_profiles") or {}) if isinstance(config, dict) else {}
    merged: Dict[str, Dict[str, Any]] = {name: dict(profile) for name, profile in DEFAULT_PROFILES.items()}
    for name, override in custom.items():
        if not isinstance(override, dict):
            continue
        base = dict(merged.get(name, {"name": name}))
        base.update(override)
        merged[name] = base

    setup_type = _normalized_setup_type(agents_results)
    # Prefer explicit setup-type matches before any wildcard profile.
    for profile in merged.values():
        setup_types = [str(x).upper() for x in (profile.get("setup_types") or [])]
        if setup_type in setup_types:
            selected = dict(profile)
            selected["resolved_setup_type"] = setup_type
            return selected
    for profile in merged.values():
        setup_types = [str(x).upper() for x in (profile.get("setup_types") or [])]
        if "*" in setup_types:
            selected = dict(profile)
            selected["resolved_setup_type"] = setup_type
            return selected
    fallback = dict(merged.get("classic_consensus", DEFAULT_PROFILES["classic_consensus"]))
    fallback["resolved_setup_type"] = setup_type
    return fallback
