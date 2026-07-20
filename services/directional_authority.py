"""Directional authority / conflict governor.

Phase D goal:
- stop weak local opposite-direction ideas from fighting a confirmed day map
- allow only a clearly stronger reversal / regime-flip thesis to override it
"""

from __future__ import annotations

from typing import Any, Dict, List


class DirectionalAuthorityService:
    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        cfg = (self.config.get("directional_authority") or {}) if isinstance(self.config, dict) else {}
        self.enabled = bool(cfg.get("enabled", True))
        self.min_confidence_for_flip = float(cfg.get("min_confidence_for_flip", 88) or 88)
        self.min_trigger_score_for_flip = float(cfg.get("min_trigger_score_for_flip", 70) or 70)
        self.require_reversal_setup_for_flip = bool(cfg.get("require_reversal_setup_for_flip", True))
        self.require_rejection_confirmed_for_flip = bool(cfg.get("require_rejection_confirmed_for_flip", True))
        self.require_fresh_sweep_for_flip = bool(cfg.get("require_fresh_sweep_for_flip", True))

    def review(
        self,
        decision: Dict[str, Any],
        session_plan: Dict[str, Any],
        open_trades: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        side = str(decision.get("decision") or "").upper()
        if not self.enabled or side not in {"BUY", "SELL"}:
            return {"action": "ALLOW", "reason": None}

        authority_state = str((session_plan or {}).get("authority_state") or "UNKNOWN").upper()
        authority_direction = str((session_plan or {}).get("authority_direction") or "").upper()
        if authority_state != "CONFIRMED" or authority_direction not in {"BUY", "SELL"}:
            return {"action": "ALLOW", "reason": None}
        if side == authority_direction:
            return {"action": "ALLOW", "reason": "signal aligns with confirmed day-map authority"}

        setup = decision.get("setup_context") or {}
        if not isinstance(setup, dict):
            setup = {}
        confidence = self._f(decision.get("confidence"), 0.0)
        setup_type = str(setup.get("setup_type") or "").upper()
        trigger_state = str(setup.get("trigger_state") or "").upper()
        trigger_score = self._f(setup.get("trigger_score"), 0.0)
        sweep_side = str(setup.get("sweep_side") or "").lower()

        reversal_setup = setup_type in {"LIQUIDITY_REVERSAL", "REVERSAL_ATTEMPT"}
        rejection_confirmed = trigger_state == "REJECTION_CONFIRMED"
        sweep_aligned = (side == "BUY" and sweep_side == "sell_side") or (side == "SELL" and sweep_side == "buy_side")

        allow_flip = True
        reasons: List[str] = []
        if confidence < self.min_confidence_for_flip:
            allow_flip = False
            reasons.append(f"confidence {confidence:.0f}% below flip threshold {self.min_confidence_for_flip:.0f}%")
        if trigger_score < self.min_trigger_score_for_flip:
            allow_flip = False
            reasons.append(f"trigger {trigger_score:.0f} below flip threshold {self.min_trigger_score_for_flip:.0f}")
        if self.require_reversal_setup_for_flip and not reversal_setup:
            allow_flip = False
            reasons.append("setup is not a reversal-grade thesis")
        if self.require_rejection_confirmed_for_flip and not rejection_confirmed:
            allow_flip = False
            reasons.append("trigger is not rejection confirmed")
        if self.require_fresh_sweep_for_flip and not sweep_aligned:
            allow_flip = False
            reasons.append("no aligned fresh sweep for regime flip")

        if not allow_flip:
            live_opposite = [
                t for t in (open_trades or [])
                if str(t.get("status") or "").upper() in {"OPEN", "PARTIAL", "TP1_HIT", "PENDING"}
                and str(t.get("type") or t.get("side") or "").upper() == authority_direction
                and str(t.get("symbol") or "").upper() == str(decision.get("symbol") or self.config.get("symbol", "XAU/USD")).upper()
            ]
            prefix = f"confirmed {authority_direction} day map still owns this symbol"
            if live_opposite:
                prefix += f" with {len(live_opposite)} active same-map trade(s)"
            return {
                "action": "BLOCK_OPPOSITE_LOCAL",
                "reason": f"{prefix}; {'; '.join(reasons)}",
                "authority_direction": authority_direction,
                "signal_direction": side,
            }

        return {
            "action": "ALLOW_REGIME_FLIP",
            "reason": (
                f"opposite-direction idea overrides the {authority_direction} day map as a high-authority reversal"
            ),
            "authority_direction": authority_direction,
            "signal_direction": side,
        }

    @staticmethod
    def _f(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
