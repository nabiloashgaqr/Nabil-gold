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
        # Live-book retirement. The five flip conditions above describe one
        # specific way a map can be overturned: a reversal-grade setup with a
        # confirmed rejection and an aligned sweep. A plain continuation move
        # in the opposite direction cannot satisfy them at any strength, so a
        # map that has simply gone stale had no expiry route at all -- the
        # agents it claims to summarise could never contradict it.
        self.allow_live_book_retirement = bool(cfg.get("allow_live_book_retirement", True))
        sig_cfg = (self.config.get("signal_requirements") or {}) if isinstance(self.config, dict) else {}
        self.agent_min_confidence = self._f(sig_cfg.get("agent_min_confidence", 70), 70.0)
        # Retirement needs the same majority a fresh plan needs to be admitted,
        # so a map is never retired by a book that could not have produced one.
        self.min_agents_to_retire_map = int(
            cfg.get("min_agents_to_retire_map", (sig_cfg.get("min_agents_agree") or 3)) or 3
        )

    VOTING_AGENTS = ("technical", "classical", "smc", "price_action", "multitimeframe")

    def _live_book_split(self, decision: Dict[str, Any], side: str) -> Dict[str, Any]:
        """Count qualified agents for and against ``side`` right now."""
        details = decision.get("agent_details")
        if not isinstance(details, dict) or not details:
            return {"available": False, "support": [], "oppose": []}
        support: List[str] = []
        oppose: List[str] = []
        for name in self.VOTING_AGENTS:
            detail = details.get(name)
            if not isinstance(detail, dict):
                continue
            direction = str(detail.get("direction") or detail.get("signal") or "WAIT").upper()
            if self._f(detail.get("confidence"), 0.0) < self.agent_min_confidence:
                continue
            if direction == side:
                support.append(name)
            elif direction in {"BUY", "SELL"}:
                oppose.append(name)
        return {"available": True, "support": support, "oppose": oppose}

    def _is_at_risk(self, trade: Dict[str, Any], direction: str) -> bool:
        """Can this position still lose money if its stop is hit?

        A stop at or beyond the entry means the trade is protected: the worst
        case is breakeven or better, so it is not something a new opposite
        idea can damage. Anything else -- including a stop that has not moved,
        or a pending order not yet filled -- is genuinely exposed.

        Unknown or unparseable prices are treated as AT RISK. A missing field
        must never quietly unlock the map.
        """
        entry = self._f(trade.get("entry_price"), 0.0)
        stop = self._f(trade.get("stop_loss"), 0.0)
        if entry <= 0 or stop <= 0:
            return True
        if str(trade.get("status") or "").upper() == "PENDING":
            # Not filled yet: it will open at full risk the moment it fills.
            return True
        if direction == "BUY":
            return stop < entry
        if direction == "SELL":
            return stop > entry
        return True

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
            symbol_key = str(decision.get("symbol") or self.config.get("symbol", "XAU/USD")).upper()
            on_map = [
                t for t in (open_trades or [])
                if str(t.get("status") or "").upper() in {"OPEN", "PARTIAL", "TP1_HIT", "PENDING"}
                and str(t.get("type") or t.get("side") or "").upper() == authority_direction
                and str(t.get("symbol") or "").upper() == symbol_key
            ]
            # Only a trade that can still LOSE money defends the map.
            #
            # This list existed to stop a fresh opposite signal contradicting
            # a position that is already exposed. A trade whose stop has been
            # carried to breakeven is not exposed: its worst outcome is zero.
            # Counting it anyway meant one protected, profitable runner kept
            # the whole symbol locked to its map -- so five qualified agents
            # reading the other way could neither retire the map nor plan
            # against it, purely because a risk-free position was still open.
            #
            # Measuring risk by price rather than by the sl_moved_to_entry
            # flag is deliberate: the manager itself distrusts that flag
            # (open_trades_manager.py:733) because older rows carry it while
            # stop_loss still shows the original wider stop.
            live_opposite = [t for t in on_map if self._is_at_risk(t, authority_direction)]
            protected = [t for t in on_map if t not in live_opposite]

            # Before refusing, ask whether the map still describes the book.
            #
            # A day map is a thesis about where price is going. Theses expire,
            # and this service had no mechanism for that: the five flip
            # conditions require a reversal-grade setup, so a plain
            # continuation against a stale map could never clear them however
            # many agents agreed or however confident they were. The map was
            # unfalsifiable by the very agents it claims to summarise.
            #
            # Retirement is deliberately narrow. It needs the same qualified
            # majority a new plan needs for admission, *zero* qualified agents
            # still defending the map, and no live trades riding it -- open
            # positions are managed by their own stops and must not be
            # contradicted by a fresh opposite signal. Anything less and the
            # map holds.
            if self.allow_live_book_retirement and not live_opposite:
                book = self._live_book_split(decision, side)
                if (
                    book["available"]
                    and not book["oppose"]
                    and len(book["support"]) >= self.min_agents_to_retire_map
                ):
                    retire_reason = (
                        f"{len(book['support'])} qualified agents "
                        f"({', '.join(book['support'])}) now read {side} and none "
                        f"still support the {authority_direction} day map; the map "
                        f"is retired rather than allowed to veto the live book"
                    )
                    if protected:
                        retire_reason += (
                            f" ({len(protected)} {authority_direction} trade(s) still open "
                            f"but protected at breakeven, so no live risk defends the map)"
                        )
                    return {
                        "action": "ALLOW_MAP_RETIRED",
                        "reason": retire_reason,
                        "authority_direction": authority_direction,
                        "signal_direction": side,
                        "opposing_agents": book["support"],
                        "retired_map": True,
                    }

            prefix = f"confirmed {authority_direction} day map still owns this symbol"
            if live_opposite:
                prefix += f" with {len(live_opposite)} at-risk same-map trade(s)"
            elif protected:
                # Say so explicitly. A refusal that mentions open trades while
                # every one of them is risk-free reads as if the position were
                # the cause, when the flip conditions are.
                prefix += (
                    f" ({len(protected)} same-map trade(s) open but protected, "
                    f"so they are not what refused this)"
                )
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
    def apply_retirement(session_plan: Dict[str, Any], review: Dict[str, Any]) -> bool:
        """Record a retirement verdict on the plan itself.

        Retiring a map in this service only decides *this* gate. Every later
        gate re-reads ``authority_state`` from the plan: DayMapSanityService
        refuses any side that disagrees with a CONFIRMED map, so a plan left
        reading CONFIRMED would block the very signal the retirement just
        allowed, for the same reason, one step later. The verdict has to be
        written back or it is only an announcement.

        The direction is preserved under a separate key so the audit trail
        still shows what the map claimed before the live book retired it.
        """
        if not isinstance(session_plan, dict):
            return False
        if str(review.get("action") or "") != "ALLOW_MAP_RETIRED":
            return False
        session_plan["authority_direction_before_retirement"] = session_plan.get("authority_direction")
        session_plan["authority_state"] = "RETIRED"
        session_plan["authority_retired_by_live_book"] = True
        session_plan["authority_reason"] = review.get("reason")
        session_plan["authority_retired_agents"] = list(review.get("opposing_agents") or [])
        return True

    @staticmethod
    def _f(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
