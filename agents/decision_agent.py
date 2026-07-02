"""Decision Agent — classic 5-agent weighted consensus.

No external model final gate is used. The final signal is calculated from the
five analysis agents only:

- Technical
- Classical
- SMC
- Price Action
- Multi-Timeframe

Rules:
- Ignore agents below ``agent_min_confidence`` (default 60%).
- A normal entry needs at least 2 qualified agents in the same direction.
- Their net weighted confidence after subtracting opposition must be >=65%.
- Counter-trend trades against Daily Bias need at least 2 qualified agents and
  net confidence >=75%.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class DecisionAgent(BaseAgent):
    """Final decision from weighted consensus + safety filters."""

    name = "decision"

    def __init__(self, config: Dict[str, Any], learning_service: Any = None, **_kwargs):
        super().__init__(config)
        self.learning_service = learning_service
        self.min_confidence = float(config.get("risk_settings", {}).get("min_confidence", 65) or 65)
        self.min_rr_ratio = float(config.get("risk_settings", {}).get("min_rr_ratio", 1.5) or 1.5)

        signal_req = config.get("signal_requirements", {}) or {}
        self.min_agents_agree = int(signal_req.get("min_agents_agree", 2) or 2)
        self.min_agreement_pct = float(signal_req.get("min_agreement_percentage", 1) or 1)
        self.allow_all_signals = bool(signal_req.get("allow_all_signals", False))
        self.agent_min_confidence = int(signal_req.get("agent_min_confidence", 60) or 60)
        self.min_consensus_confidence = float(signal_req.get("min_consensus_confidence", self.min_confidence) or self.min_confidence)

        self.default_weights = {
            "technical": 0.20,
            "classical": 0.20,
            "smc": 0.25,
            "price_action": 0.15,
            "multitimeframe": 0.20,
        }
        self.current_weights = self._load_weights()
        self.voting_agents = set(self.default_weights)

    def _load_weights(self) -> Dict[str, float]:
        if self.learning_service is not None:
            db_weights = getattr(self.learning_service, "current_weights", None)
            if db_weights:
                return {k: float(v) for k, v in dict(db_weights).items()}
        config_weights = self.config.get("agent_weights", {}) or {}
        if config_weights:
            return {k: float(v) for k, v in config_weights.items()}
        return self.default_weights.copy()

    def update_weights(self, new_weights: Dict[str, float]) -> None:
        self.current_weights = {k: float(v) for k, v in new_weights.items()}
        logger.info("Updated agent weights: %s", self.current_weights)

    def get_adjusted_confidence(self, agent_name: str, base_confidence: float) -> float:
        if not self.learning_service:
            return base_confidence
        recommendation = self.learning_service.get_agent_recommendation(agent_name)
        if recommendation == "INCREASE_CONFIDENCE":
            return min(base_confidence * 1.1, 95)
        if recommendation == "DECREASE_CONFIDENCE":
            return max(base_confidence * 0.9, 50)
        return base_confidence

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        agents_results = data.get("all_agents_results", data)
        indicators = data.get("indicators", {})
        session_info = data.get("session", data.get("session_info", {}))

        votes = self._collect_votes(agents_results)
        classic = self._classic_decision(votes)
        final_signal, final_confidence, reasoning = self._final_decision(classic, session_info)
        result = {
            "agent": self.name,
            "signal": final_signal,
            "decision": final_signal,
            "confidence": final_confidence,
            "reasoning": reasoning,
            "votes": votes,
            "weights": self.current_weights.copy(),
            "classic": classic,
            "learning": self._get_learning_info(),
            "risk_assessment": self._assess_risk(final_signal, indicators),
            "entry_attribution": self._entry_attribution(final_signal, classic, agents_results),
            "agent_structured": self._agent_structured_payload(agents_results),
            "reason_codes": self._merged_reason_codes(agents_results),
            "timestamp": self.now_iso(),
        }
        return self._apply_safety_filters(result, agents_results)

    async def analyze_async(self, data: Dict[str, Any]) -> Dict[str, Any]:
        agents_results = data.get("all_agents_results", data)
        if self.learning_service is not None:
            try:
                learned = await self.learning_service.load_current_weights()
                if learned:
                    self.current_weights = {k: float(v) for k, v in learned.items()}
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not load learned weights, using current/config weights: %s", exc)
        return self.analyze({**data, "all_agents_results": agents_results})

    def _collect_votes(self, agents_results: Dict[str, Any]) -> Dict[str, list]:
        votes = {"BUY": [], "SELL": [], "WAIT": []}
        for agent_name, result in agents_results.items():
            if agent_name not in self.voting_agents or not isinstance(result, dict):
                continue
            signal = str(result.get("signal") or result.get("direction") or "WAIT").upper()
            if signal in {"NEUTRAL", "HOLD", "NO_TRADE", "NONE", ""}:
                signal = "WAIT"
            if signal not in votes:
                signal = "WAIT"
            try:
                confidence = float(result.get("confidence", 0) or 0)
            except (TypeError, ValueError):
                confidence = 0
            if confidence < self.agent_min_confidence:
                continue
            weight = float(self.current_weights.get(agent_name, self.default_weights.get(agent_name, 0.15)) or 0.15)
            adjusted = self.get_adjusted_confidence(agent_name, confidence)
            score = (adjusted / 100.0) * weight
            votes[signal].append({
                "agent": agent_name,
                "confidence": confidence,
                "adjusted_confidence": adjusted,
                "weight": weight,
                "score": score,
                "learning_adjusted": confidence != adjusted,
                "reason_codes": list(result.get("reason_codes", []) or [])[:8],
                "evidence": list(result.get("evidence", []) or [])[:5],
                "confidence_breakdown": result.get("confidence_breakdown", {}) or {},
            })
        return votes

    def _classic_decision(self, votes: Dict[str, list]) -> Dict[str, Any]:
        buy = self._direction_metrics("BUY", votes)
        sell = self._direction_metrics("SELL", votes)
        candidates = [("BUY", buy), ("SELL", sell)]
        valid = [(side, m) for side, m in candidates if m["valid"]]

        decision = "WAIT"
        confidence = 50.0
        rejection_reason = None
        if valid:
            decision, selected = max(valid, key=lambda item: (item[1]["edge"], item[1]["confidence"]))
            confidence = selected["confidence"]
        else:
            total_voting = len(votes["BUY"]) + len(votes["SELL"])
            best = max([buy, sell], key=lambda m: (m["support_count"], m["confidence"], m["edge"]))
            if total_voting == 0:
                rejection_reason = f"No qualified agents (need >= {self.agent_min_confidence}%)"
            elif best["support_count"] < self.min_agents_agree:
                rejection_reason = f"Need at least {self.min_agents_agree} agreeing agents with weighted confidence >= {self.min_consensus_confidence:.0f}%"
            elif best["edge"] <= 0:
                rejection_reason = "Opposing agents offset the setup (weighted edge <= 0)"
            elif best["confidence"] < self.min_consensus_confidence:
                rejection_reason = f"Net weighted confidence {best['confidence']:.0f}% below {self.min_consensus_confidence:.0f}% after opposition penalty"
            else:
                rejection_reason = "No valid weighted consensus edge"

        buy_count = len(votes["BUY"])
        sell_count = len(votes["SELL"])
        total = buy_count + sell_count
        buy_pct = (buy_count / total * 100) if total else 0
        sell_pct = (sell_count / total * 100) if total else 0
        directional = votes["BUY"] + votes["SELL"]
        strongest = max(directional, key=lambda x: x.get("score", 0), default=None)
        strongest_ctx = None
        if strongest:
            strongest_signal = "BUY" if strongest in votes["BUY"] else "SELL"
            strongest_ctx = {
                "agent": strongest.get("agent"),
                "signal": strongest_signal,
                "confidence": round(float(strongest.get("confidence", 0)), 1),
                "adjusted_confidence": round(float(strongest.get("adjusted_confidence", strongest.get("confidence", 0))), 1),
                "weight": strongest.get("weight"),
                "score": round(float(strongest.get("score", 0)), 3),
                "mode": "classic_consensus",
                "reason_codes": strongest.get("reason_codes", []),
                "evidence": strongest.get("evidence", []),
            }

        selected_metrics = buy if decision == "BUY" else sell if decision == "SELL" else None
        supporting_evidence = self._supporting_evidence(decision, selected_metrics, votes)
        return {
            "decision": decision,
            "confidence": round(float(confidence), 1),
            "buy_score": buy["support_score"],
            "sell_score": sell["support_score"],
            "buy_net_score": buy["edge"],
            "sell_net_score": sell["edge"],
            "buy_count": buy_count,
            "sell_count": sell_count,
            "buy_agreement_pct": round(buy_pct, 1),
            "sell_agreement_pct": round(sell_pct, 1),
            "total_voting_agents": total,
            "strongest_agent": strongest.get("agent") if strongest else None,
            "strongest_directional": strongest_ctx,
            "supporting_evidence": supporting_evidence,
            "rejection_reason": rejection_reason,
            "consensus": {
                "mode": "5_agent_weighted_consensus",
                "selected": selected_metrics,
                "BUY": buy,
                "SELL": sell,
                "rules": {
                    "agent_min_confidence": self.agent_min_confidence,
                    "min_agents_agree": self.min_agents_agree,
                    "min_consensus_confidence": self.min_consensus_confidence,
                },
            },
        }

    def _agent_structured_payload(self, agents_results: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for name in self.voting_agents:
            result = agents_results.get(name) or {}
            if not isinstance(result, dict):
                continue
            payload[name] = {
                "signal": result.get("signal") or result.get("direction") or "WAIT",
                "confidence": result.get("confidence", 0),
                "reason_codes": list(result.get("reason_codes", []) or [])[:10],
                "evidence": list(result.get("evidence", []) or [])[:6],
                "invalidations": list(result.get("invalidations", []) or [])[:5],
                "confidence_breakdown": result.get("confidence_breakdown", {}) or {},
                "data_quality": result.get("data_quality", {}) or {},
            }
        return payload

    def _merged_reason_codes(self, agents_results: Dict[str, Any]) -> list[str]:
        codes: list[str] = []
        for result in agents_results.values():
            if isinstance(result, dict):
                codes.extend(str(c) for c in (result.get("reason_codes", []) or []) if c)
        seen = []
        for code in codes:
            if code not in seen:
                seen.append(code)
        return seen[:20]

    def _entry_attribution(self, signal: str, classic: Dict[str, Any], agents_results: Dict[str, Any]) -> Dict[str, Any]:
        """Explain why the setup was entered or why WAIT was selected.

        Stored inside signal_snapshot for post-trade review; no schema change is
        required.  It separates entry drivers, blockers and regime context so a
        later closed trade can be attributed without re-running old agents.
        """
        signal = str(signal or "WAIT").upper()
        selected = ((classic.get("consensus") or {}).get("selected") or {}) if isinstance(classic, dict) else {}
        supporters = list(selected.get("supporters") or []) if signal in {"BUY", "SELL"} else []
        opponents = list(selected.get("opponents") or []) if signal in {"BUY", "SELL"} else []
        structured = self._agent_structured_payload(agents_results)

        def agent_score(name: str) -> float:
            result = agents_results.get(name) or {}
            try:
                confidence = float(result.get("confidence", 0) or 0)
            except (TypeError, ValueError):
                confidence = 0.0
            weight = float(self.current_weights.get(name, self.default_weights.get(name, 0.15)) or 0.15)
            return confidence * weight

        ranked = sorted(supporters, key=agent_score, reverse=True)
        primary = ranked[0] if ranked else (((classic.get("strongest_directional") or {}).get("agent")) if isinstance(classic, dict) else None)
        mtf = agents_results.get("multitimeframe", {}) or {}
        classical = agents_results.get("classical", {}) or {}
        technical = agents_results.get("technical", {}) or {}
        news = agents_results.get("news", {}) or {}
        daily = agents_results.get("daily_bias", {}) or {}

        blockers: list[str] = []
        if news and (news.get("can_trade") is False or str(news.get("market_status", "")).upper() == "DANGER"):
            blockers.append("news_event_risk")
        if str(mtf.get("entry_permission", "")).upper() in {"BLOCKED", "NOT_RECOMMENDED"}:
            blockers.append("mtf_entry_permission")
        bias = str(daily.get("bias", "NEUTRAL")).upper()
        if signal == "BUY" and bias == "BEARISH":
            blockers.append("daily_bias_against_buy")
        elif signal == "SELL" and bias == "BULLISH":
            blockers.append("daily_bias_against_sell")

        timing_state = mtf.get("timing_state") or "UNKNOWN"
        failure_mode = "NONE"
        if signal == "WAIT":
            failure_mode = "NO_VALID_CONSENSUS"
        elif blockers:
            failure_mode = str(blockers[0]).upper()
        elif str(timing_state).upper() in {"LATE", "EXHAUSTED", "EARLY"}:
            failure_mode = f"TIMING_{str(timing_state).upper()}"

        macro = news.get("macro_direction") if isinstance(news, dict) else {}
        return {
            "mode": "post_trade_ready",
            "signal": signal,
            "primary_entry_driver": primary,
            "supporting_agents": ranked,
            "opposing_agents": opponents,
            "blockers": blockers,
            "failure_mode": failure_mode,
            "timing_state": timing_state,
            "mtf_failure_mode": mtf.get("mtf_failure_mode"),
            "entry_permission": mtf.get("entry_permission"),
            "pattern_quality": classical.get("pattern_quality", {}),
            "breakout_quality": classical.get("breakout_quality", {}),
            "technical_regime": technical.get("market_regime") or (technical.get("technical") or {}).get("market_regime", {}),
            "event_risk": news.get("event_risk", {}) if isinstance(news, dict) else {},
            "macro_direction": macro if isinstance(macro, dict) else {},
            "daily_bias": {"bias": daily.get("bias"), "confidence": daily.get("confidence"), "strength_band": daily.get("strength_band")},
            "agent_reason_codes": {name: structured.get(name, {}).get("reason_codes", []) for name in structured},
        }

    def _supporting_evidence(self, decision: str, selected_metrics: Dict[str, Any] | None, votes: Dict[str, list]) -> list[str]:
        """Build concise human-readable consensus evidence for the signal message."""
        if decision not in {"BUY", "SELL"} or not selected_metrics:
            return []
        supporters = selected_metrics.get("supporters", []) or []
        opponents = selected_metrics.get("opponents", []) or []
        confidence = float(selected_metrics.get("confidence", 0) or 0)
        support_count = int(selected_metrics.get("support_count", 0) or 0)
        support_avg = float(selected_metrics.get("support_avg_confidence", 0) or 0)
        opposition_count = int(selected_metrics.get("opposition_count", 0) or 0)
        edge = float(selected_metrics.get("edge", 0) or 0)

        names = {
            "technical": "Technical",
            "classical": "Classical",
            "smc": "SMC",
            "price_action": "Price Action",
            "multitimeframe": "Multi-Timeframe",
        }
        support_labels = [names.get(str(a), str(a).replace("_", " ").title()) for a in supporters]
        opponent_labels = [names.get(str(a), str(a).replace("_", " ").title()) for a in opponents]

        evidence: list[str] = []
        if support_labels:
            evidence.append(
                f"Qualified supporters: {', '.join(support_labels)} backed {decision} "
                f"with weighted confidence {confidence:.0f}%"
            )
        if support_count:
            evidence.append(
                f"Consensus quality: {support_count} qualified agent(s), "
                f"average supporter confidence {support_avg:.0f}%, weighted edge {edge:.2f}"
            )
        if opposition_count:
            evidence.append(f"Opposition check: {opposition_count} opposing agent(s) were deducted from the final score")
        elif opponent_labels:
            evidence.append(f"Opposition check: no qualified opposing vote against {decision}")
        return evidence

    def _direction_metrics(self, side: str, votes: Dict[str, list]) -> Dict[str, Any]:
        opposite = "SELL" if side == "BUY" else "BUY"
        supporters = votes.get(side, []) or []
        opponents = votes.get(opposite, []) or []
        support_score = sum(float(v.get("score", 0) or 0) for v in supporters)
        opposition_score = sum(float(v.get("score", 0) or 0) for v in opponents)
        edge = support_score - opposition_score
        support_weight = sum(float(v.get("weight", 0) or 0) for v in supporters)
        support_count = len(supporters)
        if support_weight > 0:
            support_avg = sum(float(v.get("adjusted_confidence", v.get("confidence", 0)) or 0) * float(v.get("weight", 0) or 0) for v in supporters) / support_weight
        else:
            support_avg = 0.0
        opposition_ratio = opposition_score / max(support_score, 0.0001)
        opposition_penalty = min(30.0, opposition_ratio * 30.0)
        confidence = max(0.0, min(95.0, support_avg - opposition_penalty))
        valid = bool(edge > 0 and support_count >= self.min_agents_agree and confidence >= self.min_consensus_confidence)
        return {
            "side": side,
            "support_count": support_count,
            "opposition_count": len(opponents),
            "support_score": round(support_score, 4),
            "opposition_score": round(opposition_score, 4),
            "edge": round(edge, 4),
            "support_weight": round(support_weight, 4),
            "support_avg_confidence": round(support_avg, 1),
            "opposition_penalty": round(opposition_penalty, 1),
            "confidence": round(confidence, 1),
            "valid": valid,
            "supporters": [v.get("agent") for v in supporters],
            "opponents": [v.get("agent") for v in opponents],
        }

    def _final_decision(self, classic: Dict[str, Any], ai_or_session: Dict[str, Any], session_info: Dict[str, Any] | None = None) -> tuple[str, float, str]:
        # Backward-compatible signature: older tests/callers passed (classic, ai, session).
        # External AI is ignored in classic consensus mode.
        if session_info is None:
            session_info = ai_or_session
        if not session_info.get("allow_signals", True):
            return "WAIT", 0, f"Reports session ({session_info.get('current_session', 'Unknown')}) - no signals sent"
        if not session_info.get("trading_allowed"):
            return "WAIT", 0, "Outside trading hours"
        final_signal = str(classic.get("decision", "WAIT")).upper()
        final_conf = float(classic.get("confidence", 0) or 0)
        if final_signal not in {"BUY", "SELL"}:
            return "WAIT", round(final_conf, 1), f"Classic consensus WAIT: {classic.get('rejection_reason') or 'No valid weighted consensus'}"
        if final_conf < self.min_confidence:
            return "WAIT", round(final_conf, 1), f"Classic consensus {final_signal} blocked: confidence {final_conf:.0f}% below {self.min_confidence:.0f}%"
        selected = (classic.get("consensus", {}) or {}).get("selected") or {}
        reason = (
            f"Classic 5-agent weighted consensus = {final_signal}; confidence {final_conf:.0f}% "
            f"(min {self.min_confidence:.0f}%), support={selected.get('support_count', 0)} agent(s), "
            f"opposition={selected.get('opposition_count', 0)} agent(s), edge={selected.get('edge', 0)}"
        )
        return final_signal, round(final_conf, 1), reason

    def _get_learning_info(self) -> Dict[str, Any]:
        info = {"enabled": self.learning_service is not None, "current_weights": self.current_weights.copy()}
        if self.learning_service and getattr(self.learning_service, "learning_history", None):
            last = self.learning_service.learning_history[-1]
            info.update({"last_update": last.report_date, "trades_analyzed": last.total_trades_analyzed, "overall_win_rate": last.overall_win_rate})
        return info

    def _assess_risk(self, signal: str, indicators: Dict[str, Any]) -> Dict[str, Any]:
        factors = []
        score = 0
        rsi = float(indicators.get("rsi", 50) or 50)
        if rsi > 75 or rsi < 25:
            factors.append("RSI in extreme zone")
            score += 1
        spread = float(indicators.get("spread", 0) or 0)
        if spread > 5:
            factors.append(f"High spread: {spread}")
            score += 1
        atr = float(indicators.get("atr", 0) or 0)
        if atr and atr < 1.0:
            factors.append("Low ATR - weak volatility")
            score += 1
        assessment = "Acceptable ✅" if score == 0 else "Moderate ⚠️" if score == 1 else "High ❌"
        return {"score": score, "assessment": assessment, "factors": factors}

    def _same_direction_vote_count(self, result: Dict[str, Any], signal: str) -> int:
        votes = result.get("votes", {}) or {}
        side_votes = votes.get(str(signal).upper(), []) if isinstance(votes, dict) else []
        if isinstance(side_votes, list):
            return len(side_votes)
        classic = result.get("classic", {}) or {}
        return int(classic.get("buy_count" if signal == "BUY" else "sell_count", 0) or 0)

    def _apply_safety_filters(self, result: Dict[str, Any], agents_results: Dict[str, Any]) -> Dict[str, Any]:
        warnings = list(result.get("warnings", []) or [])
        signal = str(result.get("signal", "WAIT")).upper()

        session = agents_results.get("session", {}) or {}
        if session and not session.get("trading_allowed", True):
            warnings.append(f"Session blocked: {session.get('reason', 'outside trading hours')}")
            signal = "WAIT"
        if session and not session.get("allow_signals", True):
            warnings.append(f"Signals disabled in current session: {session.get('current_session')}")
            signal = "WAIT"

        news = agents_results.get("news", {}) or {}
        if news and (news.get("can_trade") is False or str(news.get("market_status", "")).upper() == "DANGER"):
            warnings.append(f"News blocked: {news.get('summary', news.get('market_status', 'DANGER'))}")
            signal = "WAIT"

        daily_bias = agents_results.get("daily_bias", {}) or {}
        if signal in {"BUY", "SELL"} and daily_bias.get("enabled", True):
            bias = str(daily_bias.get("bias", "NEUTRAL")).upper()
            bias_conf = float(daily_bias.get("confidence") or 0)
            db = self.config.get("daily_bias_filter", {}) or {}
            required_agents = int(db.get("contrarian_min_agents_for_lower_confidence", 2) or 2)
            required_conf = float(db.get("contrarian_min_confidence", 75) or 75)
            is_contrarian = (bias == "BULLISH" and signal == "SELL") or (bias == "BEARISH" and signal == "BUY")
            same_count = self._same_direction_vote_count(result, signal)
            if is_contrarian and (same_count < required_agents or float(result.get("confidence") or 0) < required_conf):
                warnings.append(
                    f"Daily Bias (4H) blocks counter-trend: bias={bias} ({bias_conf}%), signal={signal}. "
                    f"Counter-trend requires ≥{required_agents} qualified agents and confidence ≥{required_conf}% "
                    f"({same_count} qualified agent(s) support {signal})."
                )
                signal = "WAIT"

        risk = agents_results.get("risk", {}) or {}
        if signal in {"BUY", "SELL"} and risk and not risk.get("approved", False):
            warnings.append(f"Risk rejected: {risk.get('rejection_reason', 'not approved')}")
            signal = "WAIT"

        if signal != result.get("signal"):
            reason = "; ".join(warnings[-3:]) or "Safety filter blocked signal"
            result["reasoning"] = f"{result.get('reasoning', '')} | {reason}".strip(" |")
            result["confidence"] = 0 if signal == "WAIT" else result.get("confidence", 0)
        result["signal"] = signal
        result["decision"] = signal
        result["warnings"] = warnings
        return result

    def _calculate_quality_score(self, analysis: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        confidence = float(analysis.get("confidence") or 0)
        risk = context.get("risk", {}) or {}
        news = context.get("news", {}) or {}
        session = context.get("session", {}) or {}
        classic = analysis.get("classic", {}) or {}
        signal = str(analysis.get("signal", "WAIT")).upper()
        tp2 = ((risk.get("take_profit", {}) or {}).get("tp2", {}) or {})
        rr = float(tp2.get("rr_ratio") or 0)
        agreement = float(classic.get("buy_agreement_pct" if signal == "BUY" else "sell_agreement_pct", 0) or 0)
        components = {
            "confidence": min(confidence, 100) * 0.30,
            "agreement": min(agreement, 100) * 0.20,
            "risk_reward": min(max((rr / 3.0) * 20.0, 0), 20.0),
            "risk_approved": 10.0 if risk.get("approved") else 0.0,
        }
        news_status = str(news.get("market_status", "SAFE")).upper()
        components["news"] = 10.0 if news_status == "SAFE" and news.get("can_trade", True) else 5.0 if news.get("can_trade", True) else 0.0
        sq = str(session.get("session_quality", session.get("quality", "LOW"))).upper()
        components["session"] = {"BEST": 10.0, "HIGH": 9.0, "MEDIUM": 6.0, "LOW": 3.0}.get(sq, 0.0) if session.get("trading_allowed", True) else 0.0
        penalty = min(len(analysis.get("warnings", []) or []) * 4.0, 12.0)
        raw = max(0.0, min(100.0, sum(components.values()) - penalty))
        grade, label = ("A+", "Elite") if raw >= 90 else ("A", "Strong") if raw >= 80 else ("B", "Good") if raw >= 70 else ("C", "Acceptable") if raw >= 60 else ("D", "Weak")
        return {"score": round(raw, 1), "grade": grade, "label": label, "components": {k: round(v, 1) for k, v in components.items()}, "penalty": round(penalty, 1), "rr_ratio": round(rr, 2), "agreement_pct": round(agreement, 1)}

    def _order_type(self, signal: str, entry: float, current_price: float | None) -> str:
        oe = self.config.get("order_execution", {}) or {}
        entry_style = str(oe.get("entry_style", "market")).lower()
        if entry_style in ("market", "fixed_risk"):
            return f"{signal}_MARKET"
        try:
            entry = float(entry)
            current = float(current_price or entry)
        except (TypeError, ValueError):
            return f"{signal}_MARKET"
        threshold = float(oe.get("market_threshold_points" if entry_style == "hybrid" else "pending_threshold_points", 30) or 30) / (10.0 if entry_style == "hybrid" else 1.0)
        if abs(entry - current) <= max(threshold, 0.01):
            return f"{signal}_MARKET"
        if signal == "BUY":
            return "BUY_LIMIT" if entry < current else "BUY_STOP"
        if signal == "SELL":
            return "SELL_LIMIT" if entry > current else "SELL_STOP"
        return "UNKNOWN"

    def _to_trade_decision(self, analysis: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        final_signal = str(analysis.get("signal", "WAIT")).upper()
        risk = context.get("risk", {}) or {}
        current_price = context.get("current_price")
        signal_payload: Dict[str, Any] = {}
        if final_signal in {"BUY", "SELL"}:
            entry_info = risk.get("entry", {}) or {}
            entry_zone = entry_info.get("zone", {}) or {}
            sl = risk.get("stop_loss", {}) or {}
            tp = risk.get("take_profit", {}) or {}
            tp1 = tp.get("tp1", {}) or {}
            tp2 = tp.get("tp2", {}) or {}
            entry_price = entry_info.get("price") or current_price
            order_type = entry_info.get("order_type") or self._order_type(final_signal, float(entry_price or 0), current_price)
            entry_kind = entry_info.get("kind") or ("MARKET" if order_type.endswith("MARKET") else order_type.split("_")[-1])
            signal_payload = {
                "type": final_signal,
                "entry": {
                    "price": entry_price,
                    "low": entry_zone.get("low", entry_price),
                    "high": entry_zone.get("high", entry_price),
                    "kind": entry_kind,
                    "order_type": order_type,
                    "basis": entry_info.get("basis", ""),
                    "current_price": entry_info.get("current_price", current_price),
                    "distance_points": entry_info.get("distance_points", 0.0),
                },
                "stop_loss": sl.get("price", 0),
                "tp1": tp1.get("price", 0),
                "tp2": tp2.get("price", 0),
                "tp1_rr": tp1.get("rr_ratio", 0),
                "tp2_rr": tp2.get("rr_ratio", 0),
                "rr_ratio": tp2.get("rr_ratio", tp1.get("rr_ratio", 0)),
                "order_type": order_type,
                "entry_kind": entry_kind,
                "position_size": risk.get("position_size", {}),
                "risk_summary": risk.get("summary", ""),
            }
        reasons = [analysis.get("reasoning", "")]
        if signal_payload.get("risk_summary"):
            reasons.append(signal_payload.get("risk_summary"))
        reasons.extend(analysis.get("warnings", []) or [])
        return {
            "decision": final_signal,
            "signal": signal_payload,
            "confidence": analysis.get("confidence", 0),
            "quality": self._calculate_quality_score(analysis, context),
            "current_price": current_price,
            "reasons": [r for r in reasons if r],
            "warnings": analysis.get("warnings", []),
            "votes": analysis.get("votes", {}),
            "weights": analysis.get("weights", {}),
            "classic": analysis.get("classic", {}),
            "supportive_evidence": (analysis.get("classic", {}) or {}).get("supporting_evidence", []),
            "entry_attribution": analysis.get("entry_attribution", {}),
            "agent_structured": analysis.get("agent_structured", {}),
            "reason_codes": analysis.get("reason_codes", []),
            "agent_context": (analysis.get("classic", {}) or {}).get("strongest_directional"),
            "consensus_mode": True,
            "learning": analysis.get("learning", {}),
            "risk": risk,
            "risk_assessment": analysis.get("risk_assessment", {}),
            "session_info": context.get("session", {}),
            "news": context.get("news", {}),
            "daily_bias": context.get("daily_bias", {}),
            "dynamic_risk": context.get("dynamic_risk", {}),
            "summary": analysis.get("reasoning", ""),
            "timestamp": analysis.get("timestamp", self.now_iso()),
        }

    def decide(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._to_trade_decision(self.analyze(data), data.get("all_agents_results", data))

    async def decide_async(self, data: Dict[str, Any]) -> Dict[str, Any]:
        analysis = await self.analyze_async(data)
        return self._to_trade_decision(analysis, data.get("all_agents_results", data))

    def get_decision_message(self, result: Dict[str, Any]) -> str:
        signal = result.get("signal", "WAIT")
        confidence = result.get("confidence", 0)
        votes = result.get("votes", {})
        classic = result.get("classic", {})
        lines = ["━━━━━━━━━━━━━━━━━━━━", "🧭 *Final Decision*", "━━━━━━━━━━━━━━━━━━━━", f"📊 Signal: *{signal}*", f"🎯 Confidence: *{confidence}%*", ""]
        lines.append("🔥 Agreement requirements:")
        lines.append(f"├ Agents: {classic.get('total_voting_agents', 0)}/{self.min_agents_agree}")
        lines.append(f"└ Min weighted confidence: {self.min_consensus_confidence:.0f}%")
        lines.append("")
        lines.append("🗳️ Agent votes:")
        lines.append(f"├ BUY: {len(votes.get('BUY', []))} ({classic.get('buy_agreement_pct', 0):.0f}%)")
        lines.append(f"├ SELL: {len(votes.get('SELL', []))} ({classic.get('sell_agreement_pct', 0):.0f}%)")
        lines.append(f"└ WAIT: {len(votes.get('WAIT', []))}")
        if classic.get("rejection_reason") and signal == "WAIT":
            lines.append(f"❌ Wait reason: {classic['rejection_reason']}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)
