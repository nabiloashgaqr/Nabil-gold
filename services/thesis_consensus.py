"""Shared entry-grade directional admission for thesis exits and flip guards."""
from __future__ import annotations

from typing import Any, Dict

from utils.helpers import get_agent_weights

VOTING_AGENTS = ("unified_trend", "classical", "smc", "price_action", "auction_flow")
LEGACY_AGENT_ALIASES = {"unified_trend": "technical", "auction_flow": "multitimeframe"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _direction(detail: Dict[str, Any] | None) -> str:
    value = str((detail or {}).get("direction") or (detail or {}).get("signal") or "WAIT").upper()
    return "WAIT" if value in {"NEUTRAL", "HOLD", "NO_TRADE", "NONE", ""} else value


def _external_confirmation(
    target_side: str,
    details: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    signal_cfg = (config.get("signal_requirements") or {}) if isinstance(config, dict) else {}
    path2 = signal_cfg.get("two_agent_entry") or {}
    macro_cfg = path2.get("macro_confirmation") or {}
    gemini_cfg = path2.get("gemini_confirmation") or {}

    macro = details.get("macro_fundamental") or details.get("macro") or {}
    macro_side = _direction(macro)
    macro_conf = _f(macro.get("confidence"), 0.0)
    macro_min = _f(macro_cfg.get("min_confidence"), 55.0)
    if bool(macro_cfg.get("enabled", True)) and macro_side == target_side and macro_conf >= macro_min:
        return {"allow": True, "source": "macro", "confidence": macro_conf}

    gemini = details.get("gemini") or details.get("gemini_review") or {}
    gemini_side = _direction(gemini)
    gemini_conf = _f(gemini.get("confidence"), 0.0)
    gemini_min = _f(gemini_cfg.get("min_confidence"), 70.0)
    available = gemini.get("available", True) if isinstance(gemini, dict) else False
    if (bool(gemini_cfg.get("enabled", True)) and available
            and gemini_side == target_side and gemini_conf >= gemini_min):
        return {"allow": True, "source": "gemini", "confidence": gemini_conf}
    return {"allow": False, "source": None, "confidence": 0.0}


def evaluate_directional_admission(
    target_side: str,
    agent_details: Dict[str, Any] | None,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply the operator's exact three entry theses to ``target_side``.

    1) >=3 qualified agents with net weighted confidence >=72;
    2) exactly/at least 2 qualified agents at the same confidence + Macro;
    3) exactly/at least 2 qualified agents at the same confidence + Gemini.
    Qualified opposition is deducted with the same formula as DecisionAgent.
    """
    target_side = str(target_side or "").upper()
    details = agent_details if isinstance(agent_details, dict) else {}
    if target_side not in {"BUY", "SELL"} or not details:
        return {"allow": False, "path": None, "available": False,
                "supporters": [], "opponents": [], "confidence": 0.0}

    sig = config.get("signal_requirements") or {}
    min_agent_conf = _f(sig.get("agent_min_confidence"), 67.0)
    min_agents = int(sig.get("min_agents_agree", 3) or 3)
    min_consensus = _f(sig.get("min_consensus_confidence"), 72.0)
    path2 = sig.get("two_agent_entry") or {}
    path2_min_agents = int(path2.get("min_agents_agree", 2) or 2)
    path2_min_conf = _f(path2.get("min_consensus_confidence"), min_consensus)
    weights = get_agent_weights(config)
    opposite = "SELL" if target_side == "BUY" else "BUY"

    supporters = []
    opponents = []
    support_score = 0.0
    opposition_score = 0.0
    support_weight = 0.0
    weighted_conf_sum = 0.0
    for name in VOTING_AGENTS:
        used_name = name
        detail = details.get(name)
        if not isinstance(detail, dict):
            legacy = LEGACY_AGENT_ALIASES.get(name)
            detail = details.get(legacy) if legacy else None
            if isinstance(detail, dict):
                used_name = str(legacy)
        if not isinstance(detail, dict):
            continue
        confidence = _f(detail.get("confidence"), 0.0)
        if confidence < min_agent_conf:
            continue
        side = _direction(detail)
        weight = _f(weights.get(name), 0.0)
        score = confidence / 100.0 * weight
        if side == target_side:
            supporters.append(used_name)
            support_score += score
            support_weight += weight
            weighted_conf_sum += confidence * weight
        elif side == opposite:
            opponents.append(used_name)
            opposition_score += score

    support_avg = weighted_conf_sum / support_weight if support_weight else 0.0
    edge = support_score - opposition_score
    opposition_ratio = opposition_score / max(support_score, 0.0001)
    opposition_penalty = min(30.0, opposition_ratio * 30.0)
    confidence = max(0.0, min(95.0, support_avg - opposition_penalty))

    common = {
        "available": True,
        "target_side": target_side,
        "supporters": supporters,
        "opponents": opponents,
        "support_count": len(supporters),
        "opposition_count": len(opponents),
        "support_score": round(support_score, 4),
        "opposition_score": round(opposition_score, 4),
        "edge": round(edge, 4),
        "confidence": round(confidence, 1),
        "min_agent_confidence": min_agent_conf,
        "min_consensus_confidence": min_consensus,
    }

    if len(supporters) >= min_agents and edge > 0 and confidence >= min_consensus:
        return {**common, "allow": True, "path": "THREE_AGENT_CONSENSUS",
                "reason": f"{len(supporters)} qualified agents admit {target_side} at {confidence:.1f}%"}

    if (bool(path2.get("enabled", True)) and len(supporters) >= path2_min_agents
            and edge > 0 and confidence >= path2_min_conf):
        external = _external_confirmation(target_side, details, config)
        if external.get("allow"):
            source = str(external.get("source") or "").upper()
            return {
                **common, "allow": True, "path": f"TWO_AGENT_{source}",
                "confirm_source": external.get("source"),
                "confirm_confidence": external.get("confidence"),
                "reason": (
                    f"{len(supporters)} qualified agents admit {target_side} at "
                    f"{confidence:.1f}% + {external.get('source')} "
                    f"{_f(external.get('confidence')):.1f}%"
                ),
            }

    return {
        **common, "allow": False, "path": None,
        "reason": (
            f"{target_side} admission failed: support={len(supporters)}, "
            f"opposition={len(opponents)}, net={confidence:.1f}%"
        ),
    }
