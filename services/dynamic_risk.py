"""Dynamic Risk Management.

Adjusts trading strictness according to recent losses and daily performance.
It does not execute broker orders; it controls whether paper/live signals are
allowed and raises required confidence/quality after bad streaks.
"""

from __future__ import annotations

from typing import Any, Dict, List


OPEN_STATUSES = {"OPEN", "PARTIAL", "TP1_HIT", "PENDING"}


class DynamicRiskManager:
    """Evaluate adaptive risk constraints from database performance."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.settings = config.get("dynamic_risk_management", {}) or {}
        self.base_min_confidence = float(config.get("risk_settings", {}).get("min_confidence", 60) or 60)

    def _pnl(self, trade: Dict[str, Any]) -> float:
        for key in ("final_pnl", "current_pnl", "current_pnl_points", "pnl"):
            value = trade.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    def _closed_trades(self, trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [t for t in trades if str(t.get("status", "")).upper() not in OPEN_STATUSES]

    def evaluate(self, database: Any) -> Dict[str, Any]:
        """Return dynamic risk state and current requirements."""
        if not self.settings.get("enabled", True):
            return {
                "enabled": False,
                "can_trade": True,
                "level": "NORMAL",
                "min_confidence_required": self.base_min_confidence,
                "min_quality_score": 0,
                "risk_multiplier": 1.0,
                "warnings": [],
                "summary": "Dynamic risk disabled",
            }

        consecutive_losses = int(database.get_consecutive_losses())
        today_trades = database.get_today_trades()
        recent_trades = database.get_recent_trades(limit=int(self.settings.get("recent_trades_limit", 20) or 20))
        closed_today = self._closed_trades(today_trades)
        daily_pnl = sum(self._pnl(t) for t in closed_today)
        recent_closed = self._closed_trades(recent_trades)
        recent_losses = len([t for t in recent_closed if self._pnl(t) < 0 or str(t.get("status", "")).upper() == "SL_HIT"])
        recent_wins = len([t for t in recent_closed if self._pnl(t) > 0 or str(t.get("status", "")).upper() == "TP2_HIT"])

        warn_after = int(self.settings.get("warn_after_losses", 2) or 2)
        halt_after = int(self.settings.get("halt_after_losses", 3) or 3)
        daily_loss_limit = float(self.settings.get("daily_loss_limit_points", 30) or 30)
        caution_min_conf = float(self.settings.get("caution_min_confidence", 75) or 75)
        strict_min_conf = float(self.settings.get("strict_min_confidence", 82) or 82)
        caution_min_quality = float(self.settings.get("caution_min_quality_score", 70) or 70)
        strict_min_quality = float(self.settings.get("strict_min_quality_score", 80) or 80)

        can_trade = True
        level = "NORMAL"
        min_conf = self.base_min_confidence
        min_quality = float(self.settings.get("normal_min_quality_score", 0) or 0)
        risk_multiplier = 1.0
        warnings: List[str] = []

        if consecutive_losses >= halt_after:
            can_trade = False
            level = "HALT"
            min_conf = 100
            min_quality = 100
            risk_multiplier = 0.0
            warnings.append(f"Halt: {consecutive_losses} consecutive losses")
        elif daily_pnl <= -abs(daily_loss_limit):
            can_trade = False
            level = "DAILY_HALT"
            min_conf = 100
            min_quality = 100
            risk_multiplier = 0.0
            warnings.append(f"Daily halt: daily loss {daily_pnl:.1f} pts exceeded limit {daily_loss_limit:.1f}")
        elif consecutive_losses >= warn_after:
            level = "STRICT"
            min_conf = max(self.base_min_confidence, strict_min_conf)
            min_quality = strict_min_quality
            risk_multiplier = float(self.settings.get("strict_risk_multiplier", 0.5) or 0.5)
            warnings.append(f"Strict mode: {consecutive_losses} consecutive losses, higher confidence/quality required")
        elif recent_losses >= int(self.settings.get("recent_losses_caution", 2) or 2) and recent_losses > recent_wins:
            level = "CAUTION"
            min_conf = max(self.base_min_confidence, caution_min_conf)
            min_quality = caution_min_quality
            risk_multiplier = float(self.settings.get("caution_risk_multiplier", 0.75) or 0.75)
            warnings.append(f"Caution: recent losses ({recent_losses}) exceed wins ({recent_wins})")

        return {
            "enabled": True,
            "can_trade": can_trade,
            "level": level,
            "consecutive_losses": consecutive_losses,
            "daily_pnl_points": round(daily_pnl, 2),
            "recent_wins": recent_wins,
            "recent_losses": recent_losses,
            "min_confidence_required": round(min_conf, 1),
            "min_quality_score": round(min_quality, 1),
            "risk_multiplier": round(risk_multiplier, 2),
            "warnings": warnings,
            "summary": f"Dynamic Risk {level}: min_conf={min_conf:.0f}, min_quality={min_quality:.0f}, multiplier={risk_multiplier:.2f}",
        }


def should_block_signal(decision: Dict[str, Any], dynamic_risk: Dict[str, Any]) -> str | None:
    """Return reason to block signal under dynamic risk, or None."""
    if not dynamic_risk.get("enabled", True):
        return None
    if not dynamic_risk.get("can_trade", True):
        return "; ".join(dynamic_risk.get("warnings", [])) or "Dynamic risk halted trading"
    signal = str(decision.get("decision", "WAIT")).upper()
    if signal not in {"BUY", "SELL"}:
        return None
    confidence = float(decision.get("confidence") or 0)
    min_conf = float(dynamic_risk.get("min_confidence_required") or 0)
    if confidence < min_conf:
        return f"Confidence {confidence:.1f}% below Dynamic Risk requirement {min_conf:.1f}%"
    quality = decision.get("quality", {}) or {}
    quality_score = float(quality.get("score") or 0)
    min_quality = float(dynamic_risk.get("min_quality_score") or 0)
    if quality_score < min_quality:
        return f"Signal quality {quality_score:.1f}% below Dynamic Risk requirement {min_quality:.1f}%"
    return None
