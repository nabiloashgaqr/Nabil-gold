"""Session planner foundation.

Phase 1 goal:
- build a morning / session-level trading plan BEFORE the move
- rank the best primary and standby POIs for the active thesis
- persist the plan in lightweight local storage for later execution phases

This layer is intentionally planning-only. It does NOT place orders by itself.
Later phases can convert its PRIMARY / STANDBY plan objects into live pending
orders, staleness rules, and delayed-touch revalidation.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from utils.helpers import load_trades, save_trades
from utils.instruments import price_to_points, points_to_price


class SessionPlannerService:
    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        cfg = (self.config.get("session_planner") or {}) if isinstance(self.config, dict) else {}
        self.enabled = bool(cfg.get("enabled", True))
        self.min_plan_score = float(cfg.get("min_plan_score", 62) or 62)
        self.min_primary_dominance = float(cfg.get("min_primary_dominance", 50) or 50)
        self.min_return_probability = float(cfg.get("min_return_probability", 42) or 42)
        self.min_primary_quality_score = float(cfg.get("min_primary_quality_score", 70) or 70)
        self.min_trigger_score = float(cfg.get("min_trigger_score", 40) or 40)
        self.require_session_quality = str(cfg.get("require_session_quality", "HIGH") or "HIGH").upper()
        self.require_structure_quality = str(cfg.get("require_structure_quality", "MODERATE") or "MODERATE").upper()
        self.allow_caution_news = bool(cfg.get("allow_caution_news", True))
        self.expire_after_hours = float(cfg.get("expire_after_hours", 8) or 8)
        self.default_pending_slots = int(cfg.get("default_pending_slots", 2) or 2)
        self.standby_min_distance_points = float(cfg.get("standby_min_distance_points", 60) or 60)
        self.max_primary_zone_width_points = float(cfg.get("max_primary_zone_width_points", 260) or 260)
        self.max_standby_zone_width_points = float(cfg.get("max_standby_zone_width_points", 220) or 220)
        self.min_main_rr_for_ready = float(cfg.get("min_main_rr_for_ready", (self.config.get("risk_settings", {}) or {}).get("min_rr_ratio", 1.5)) or 1.5)
        self.min_supporting_agents_for_ready = int(cfg.get("min_supporting_agents_for_ready", 2) or 2)
        self.max_opposing_agents_for_ready = int(cfg.get("max_opposing_agents_for_ready", 1) or 1)
        self.agent_alignment_min_confidence = float(cfg.get("agent_alignment_min_confidence", 68) or 68)
        self.min_authority_alignment_count = int(cfg.get("min_authority_alignment_count", 2) or 2)
        self.fallback_zone_half_width_points = float(cfg.get("fallback_zone_half_width_points", 120) or 120)
        self.fallback_max_reference_levels = int(cfg.get("fallback_max_reference_levels", 3) or 3)
        self.symbol = str(self.config.get("symbol", "XAU/USD"))
        root = Path(__file__).resolve().parents[1]
        self.storage_path = root / "storage" / "session_plans.json"

    def build_plan(self, all_results: Dict[str, Any], *, persist: bool = True) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "plan_ready": False, "plan_status": "DISABLED", "plan_reason": "session planner disabled"}

        symbol = str(all_results.get("symbol") or self.symbol)
        session = all_results.get("session", {}) or all_results.get("session_info", {}) or {}
        news = all_results.get("news", {}) or {}
        daily_bias = all_results.get("daily_bias", {}) or {}
        macro = ((all_results.get("news", {}) or {}).get("macro_direction") or ((all_results.get("macro_fundamental", {}) or {}).get("macro_direction") or {}))
        smc = all_results.get("smc", {}) or {}
        candidates = list(smc.get("setup_candidates") or [])
        market_structure = smc.get("market_structure", {}) or {}
        liquidity = smc.get("liquidity", {}) or {}
        dealing_range = smc.get("dealing_range", {}) or {}
        zone_context = str(smc.get("zone") or "")
        reversal_watch = all_results.get("reversal_watch") or {}
        smc_archetype = str(smc.get("day_archetype") or "").upper()
        smc_archetype_confidence = self._f(smc.get("day_archetype_confidence"), 0.0)
        smc_archetype_reason = str(smc.get("day_archetype_reason") or "").strip()
        smc_preferred_execution_family = str(smc.get("preferred_execution_family") or "").upper()
        current_price = self._f(all_results.get("current_price"), 0.0)

        now = datetime.now(timezone.utc)
        session_label = str(session.get("current_session") or session.get("session") or "Unknown Session")
        session_quality = str(session.get("session_quality") or session.get("quality") or "LOW").upper()

        structure_trend = str(market_structure.get("trend") or "RANGING").upper()
        structure_quality = str(market_structure.get("structure_quality") or "WEAK").upper()
        recent_sweep = (liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {}
        market_objective = self._market_objective(
            structure_trend=structure_trend,
            recent_sweep=recent_sweep,
            zone_context=zone_context,
        )

        base = {
            "enabled": True,
            "symbol": symbol,
            "session_label": session_label,
            "session_quality": session_quality,
            "plan_created_at": self._iso(now),
            "plan_expires_at": self._iso(now + timedelta(hours=self.expire_after_hours)),
            "plan_ready": False,
            "plan_status": "NOT_READY",
            "plan_reason": None,
            "scenario_id": None,
            "plan_id": None,
            "planner_source": None,
            "authority_state": "UNKNOWN",
            "authority_direction": None,
            "authority_reason": None,
            "session_bias": None,
            "scenario_type": None,
            "thesis_family": None,
            "primary_poi": None,
            "standby_poi": None,
            "primary_entry_zone": None,
            "standby_entry_zone": None,
            "primary_entry_price": None,
            "standby_entry_price": None,
            "invalidation_level": None,
            "target_liquidity": None,
            "planner_confidence": 0.0,
            "planner_grade": "D",
            "max_pending_orders_allowed": 0,
            "notes": [],
            "bias_sources": [],
            "directional_alignment_count": 0,
            "structure_trend": structure_trend,
            "structure_quality": structure_quality,
            "market_zone_context": zone_context,
            "recent_sweep": {
                "type": recent_sweep.get("type"),
                "reference_type": recent_sweep.get("reference_type"),
                "confirmation": recent_sweep.get("confirmation"),
            },
            "expected_path": None,
            "execution_preference": None,
            "market_objective": None,
            "market_objective_label": None,
            "market_objective_direction": None,
            "day_archetype": None,
            "day_archetype_confidence": 0,
            "day_archetype_reason": None,
            "preferred_execution_family": None,
            "execution_readiness": {"state": "MAP_ONLY", "reason": "execution not evaluated yet"},
            "primary_rationale": [],
            "standby_rationale": [],
            "plan_narrative": None,
            "poi_classification": None,
            "extreme_poi": False,
            "liquidity_map": {
                "previous_day_high": (liquidity.get("previous_day_levels") or {}).get("high") if isinstance(liquidity, dict) else None,
                "previous_day_low": (liquidity.get("previous_day_levels") or {}).get("low") if isinstance(liquidity, dict) else None,
                "session_high": (liquidity.get("session_liquidity") or {}).get("high") if isinstance(liquidity, dict) else None,
                "session_low": (liquidity.get("session_liquidity") or {}).get("low") if isinstance(liquidity, dict) else None,
                "session_reference": (liquidity.get("session_liquidity") or {}).get("label") if isinstance(liquidity, dict) else None,
            },
            "daily_bias": {
                "bias": daily_bias.get("bias"),
                "confidence": daily_bias.get("confidence"),
            },
            "macro_bias": {
                "bias": macro.get("bias") if isinstance(macro, dict) else None,
                "confidence": macro.get("confidence") if isinstance(macro, dict) else None,
            },
            "market_objective": market_objective.get("objective"),
            "market_objective_label": market_objective.get("label"),
            "market_objective_direction": market_objective.get("direction"),
            "day_archetype": smc_archetype or None,
            "day_archetype_confidence": round(smc_archetype_confidence, 1),
            "day_archetype_reason": smc_archetype_reason or None,
            "preferred_execution_family": smc_preferred_execution_family or None,
            "reversal_watch": reversal_watch if isinstance(reversal_watch, dict) else {},
        }

        if not session.get("trading_allowed", True) or not session.get("allow_signals", True):
            base["plan_status"] = "BLOCKED"
            base["plan_reason"] = str(session.get("reason") or "outside planning session")
            return base

        if not self._session_quality_ok(session_quality):
            base["plan_status"] = "BLOCKED"
            base["plan_reason"] = f"session quality {session_quality} below {self.require_session_quality}"
            return base

        if news.get("can_trade") is False:
            base["plan_status"] = "BLOCKED"
            base["plan_reason"] = f"news blocked: {news.get('market_status') or 'blocked'}"
            return base
        if str(news.get("market_status") or "").upper() == "CAUTION" and not self.allow_caution_news:
            base["plan_status"] = "BLOCKED"
            base["plan_reason"] = "news caution not allowed for morning planning"
            return base

        if not candidates:
            fallback = self._build_fallback_plan(
                base=base,
                symbol=symbol,
                now=now,
                session_label=session_label,
                session=session,
                daily_bias=daily_bias,
                macro=(macro if isinstance(macro, dict) else {}),
                market_structure=market_structure,
                liquidity=liquidity,
                dealing_range=dealing_range,
                zone_context=zone_context,
                reversal_watch=reversal_watch if isinstance(reversal_watch, dict) else {},
                current_price=current_price,
                all_results=all_results,
            )
            if fallback.get("plan_ready"):
                if persist:
                    self.save_plan(fallback)
                return fallback
            base["plan_status"] = fallback.get("plan_status", base.get("plan_status"))
            base["plan_reason"] = fallback.get("plan_reason") or "no structured setup candidates available"
            base["authority_state"] = fallback.get("authority_state", base.get("authority_state"))
            base["authority_direction"] = fallback.get("authority_direction")
            base["authority_reason"] = fallback.get("authority_reason")
            base["execution_readiness"] = fallback.get("execution_readiness", base.get("execution_readiness"))
            return base

        ranked_candidates = self._rank_planner_candidates(
            candidates,
            current_price=current_price,
            structure_trend=structure_trend,
            recent_sweep=recent_sweep,
            zone_context=zone_context,
            symbol=symbol,
        )
        primary = ranked_candidates[0] if ranked_candidates else None
        standby = next(
            (
                candidate for candidate in ranked_candidates[1:]
                if str(candidate.get("direction") or "").upper() == str((primary or {}).get("direction") or "").upper()
            ),
            None,
        )

        primary_dom = self._f(primary.get("thesis_dominance_score"), 0.0)
        primary_rp = self._f(primary.get("return_probability_score"), 0.0)
        primary_quality = self._f(primary.get("quality_score"), self._f((primary.get("setup_quality") or {}).get("score"), 0.0))
        primary_trigger = self._f(primary.get("trigger_score"), 0.0)
        if primary_dom < self.min_primary_dominance or primary_rp < self.min_return_probability:
            fallback = self._build_fallback_plan(
                base=base,
                symbol=symbol,
                now=now,
                session_label=session_label,
                session=session,
                daily_bias=daily_bias,
                macro=(macro if isinstance(macro, dict) else {}),
                market_structure=market_structure,
                liquidity=liquidity,
                dealing_range=dealing_range,
                zone_context=zone_context,
                reversal_watch=reversal_watch if isinstance(reversal_watch, dict) else {},
                current_price=current_price,
                all_results=all_results,
            )
            if fallback.get("plan_ready"):
                if persist:
                    self.save_plan(fallback)
                return fallback
            base["plan_status"] = fallback.get("plan_status", base.get("plan_status"))
            base["plan_reason"] = fallback.get("plan_reason") or (
                f"primary thesis too weak for planning (dominance {primary_dom:.1f}, return probability {primary_rp:.1f})"
            )
            base["authority_state"] = fallback.get("authority_state", base.get("authority_state"))
            base["authority_direction"] = fallback.get("authority_direction")
            base["authority_reason"] = fallback.get("authority_reason")
            base["execution_readiness"] = fallback.get("execution_readiness", base.get("execution_readiness"))
            return base
        if primary_quality < self.min_primary_quality_score:
            base["plan_reason"] = f"primary quality {primary_quality:.1f} below planner floor {self.min_primary_quality_score:.1f}"
            return base
        if primary_trigger < self.min_trigger_score and str(primary.get("setup_state") or "").upper() == "DETECTED":
            base["plan_reason"] = f"primary trigger score {primary_trigger:.1f} still too early for a morning plan"
            return base

        direction = str(primary.get("direction") or "").upper()
        if direction not in {"BUY", "SELL"}:
            base["plan_reason"] = "primary candidate has no directional thesis"
            return base

        objective_direction = str(base.get("market_objective_direction") or "").upper()
        counter_objective_reversal_confirmed = False
        if objective_direction in {"BUY", "SELL"} and objective_direction != direction:
            reversal_ok, reversal_reason = self._counter_objective_reversal_proof(
                primary,
                zone_context=zone_context,
            )
            if not reversal_ok:
                base["plan_status"] = "WATCH_ONLY"
                base["plan_reason"] = f"counter-objective {direction} plan lacks reversal proof: {reversal_reason}"
                return base
            counter_objective_reversal_confirmed = True
        objective_alignment = (
            "COUNTER_OBJECTIVE_REVERSAL_CONFIRMED"
            if counter_objective_reversal_confirmed
            else "ALIGNED_WITH_MARKET_OBJECTIVE"
            if objective_direction in {"BUY", "SELL"} and objective_direction == direction
            else "NEUTRAL_TO_MARKET_OBJECTIVE"
        )

        alignment = self._directional_alignment(direction, daily_bias, macro if isinstance(macro, dict) else {}, structure_trend)
        base["bias_sources"] = alignment["sources"]
        base["directional_alignment_count"] = alignment["count"]
        if alignment["count"] == 0 and not self._aligned_sweep(direction, recent_sweep) and not counter_objective_reversal_confirmed:
            base["plan_reason"] = "no strong bias alignment for a morning plan"
            return base
        if not self._structure_quality_ok(structure_quality, str(primary.get("setup_type") or ""), recent_sweep):
            base["plan_reason"] = f"structure quality {structure_quality} is too weak for this plan type"
            return base

        standby = self._validated_standby(primary, standby, symbol=symbol)
        if objective_alignment == "COUNTER_OBJECTIVE_REVERSAL_CONFIRMED":
            standby = None
        primary_execution_preview = self._execution_levels(
            direction=direction,
            entry_price=self._f(primary.get("entry_price"), 0.0),
            stop_loss=self._f(primary.get("stop_loss"), 0.0),
            target_price=self._f(primary.get("target_liquidity") or primary.get("target_price"), 0.0),
            symbol=symbol,
        )
        quality_ok, quality_reason, quality_diag = self._plan_quality_guard(
            direction=direction,
            primary=primary,
            standby=standby,
            primary_execution=primary_execution_preview,
            all_results=all_results,
            symbol=symbol,
        )
        if not quality_ok:
            base["plan_status"] = "WATCH_ONLY"
            base["plan_reason"] = quality_reason
            base["notes"] = [quality_reason] if quality_reason else []
            base["supporting_agents"] = quality_diag.get("supporting_agents", [])
            base["opposing_agents"] = quality_diag.get("opposing_agents", [])
            return base
        planner_score, planner_notes = self._planner_score(
            direction=direction,
            primary=primary,
            standby=standby,
            session=session,
            daily_bias=daily_bias,
            macro=macro if isinstance(macro, dict) else {},
            news=news,
            market_structure=market_structure,
            recent_sweep=recent_sweep,
            zone_context=zone_context,
        )
        if planner_score < self.min_plan_score:
            base["plan_reason"] = f"planner score {planner_score:.1f} below {self.min_plan_score:.1f}"
            base["notes"] = planner_notes
            return base
        execution_readiness = self._execution_readiness(
            planner_source="setup_candidates",
            direction=direction,
            primary=primary,
            standby=standby,
            all_results=all_results,
            preferred_execution_family=smc_preferred_execution_family,
            macro=macro if isinstance(macro, dict) else {},
        )

        scenario_type = str(primary.get("setup_type") or "SCENARIO")
        scenario_id = self._scenario_id(symbol, direction, scenario_type, session_label, now)
        plan_id = f"PLAN::{scenario_id}"
        primary_execution = self._execution_levels(
            direction=direction,
            entry_price=self._f(primary.get("entry_price"), 0.0),
            stop_loss=self._f(primary.get("stop_loss"), 0.0),
            target_price=self._f(primary.get("target_liquidity") or primary.get("target_price"), 0.0),
            symbol=symbol,
        )
        standby_execution = self._execution_levels(
            direction=direction,
            entry_price=self._f(standby.get("entry_price"), 0.0),
            stop_loss=self._f(standby.get("stop_loss"), 0.0),
            target_price=self._f(standby.get("target_liquidity") or standby.get("target_price"), 0.0),
            symbol=symbol,
        ) if standby else None
        expected_path = self._expected_path(direction, primary, liquidity, dealing_range, current_price)
        day_objective, day_objective_label = self._day_objective(
            direction=direction,
            scenario_type=scenario_type,
            structure_trend=structure_trend,
            recent_sweep=recent_sweep,
            zone_context=zone_context,
        )
        primary_rationale = self._candidate_rationale(primary, direction, structure_trend, structure_quality, recent_sweep, rank_label="PRIMARY")
        standby_rationale = self._candidate_rationale(standby, direction, structure_trend, structure_quality, recent_sweep, rank_label="STANDBY") if standby else []
        execution_preference = self._execution_preference(
            primary,
            standby,
            current_price,
            preferred_execution_family=smc_preferred_execution_family,
        )
        if objective_alignment == "COUNTER_OBJECTIVE_REVERSAL_CONFIRMED":
            execution_preference = "SINGLE_PENDING"
        poi_classification = self._classify_poi(primary, direction, structure_quality, recent_sweep, zone_context, current_price, symbol=symbol)
        primary["poi_classification"] = poi_classification
        primary["extreme_poi"] = poi_classification == "EXTREME_POI"
        plan_narrative = self._plan_narrative(direction, scenario_type, primary, alignment["sources"], expected_path, execution_preference)
        manual_plan = self._manual_plan_hierarchy(
            direction=direction,
            primary=primary,
            standby=standby,
            structure_trend=structure_trend,
            structure_quality=structure_quality,
            recent_sweep=recent_sweep,
            execution_preference=execution_preference,
            expected_path=expected_path,
            narrative=plan_narrative,
            symbol=symbol,
            day_objective=day_objective,
            day_objective_label=day_objective_label,
            market_objective_label=str(base.get("market_objective_label") or ""),
            objective_alignment=objective_alignment,
        )
        manual_plan["target_script"] = {
            "tp1": primary_execution.get("tp1"),
            "tp2": primary_execution.get("tp2"),
        }
        if primary_execution.get("floor_applied"):
            manual_plan["risk_note"] = f"Execution stop normalized to the configured {primary_execution.get('min_sl_distance_points', 0):.0f}-point minimum."

        base.update(
            {
                "plan_ready": True,
                "plan_status": "READY",
                "planner_source": "setup_candidates",
                "authority_state": "CONFIRMED",
                "authority_direction": direction,
                "authority_reason": f"primary setup candidate accepted with alignment from {', '.join(alignment['sources']) or 'setup context'}",
                "scenario_id": scenario_id,
                "plan_id": plan_id,
                "session_bias": direction,
                "scenario_type": scenario_type,
                "thesis_family": f"{direction}::{scenario_type}",
                "primary_poi": self._compact_candidate(primary),
                "standby_poi": self._compact_candidate(standby) if standby else None,
                "primary_entry_zone": self._zone_payload(primary),
                "standby_entry_zone": self._zone_payload(standby) if standby else None,
                "primary_entry_price": primary.get("entry_price"),
                "standby_entry_price": standby.get("entry_price") if standby else None,
                "primary_execution": primary_execution,
                "standby_execution": standby_execution,
                "invalidation_level": primary_execution.get("stop_loss"),
                "target_liquidity": primary.get("target_liquidity") or primary.get("target_price"),
                "day_archetype": smc_archetype or None,
                "day_archetype_confidence": round(smc_archetype_confidence, 1),
                "day_archetype_reason": smc_archetype_reason or None,
                "preferred_execution_family": smc_preferred_execution_family or None,
                "execution_readiness": execution_readiness,
                "planner_confidence": planner_score,
                "planner_grade": self._grade(planner_score),
                "supporting_agents": quality_diag.get("supporting_agents", []),
                "opposing_agents": quality_diag.get("opposing_agents", []),
                "support_count": quality_diag.get("support_count", 0),
                "opposition_count": quality_diag.get("opposition_count", 0),
                "max_pending_orders_allowed": min(self.default_pending_slots, 2 if standby else 1),
                "plan_reason": "session plan ready",
                "notes": planner_notes,
                "day_objective": day_objective,
                "day_objective_label": day_objective_label,
                "objective_alignment": objective_alignment,
                "same_box_ladder": bool(manual_plan.get("same_box_ladder")),
                "expected_path": expected_path,
                "execution_preference": execution_preference,
                "primary_rationale": primary_rationale,
                "standby_rationale": standby_rationale,
                "plan_narrative": plan_narrative,
                "manual_plan": manual_plan,
                "poi_classification": poi_classification,
                "extreme_poi": poi_classification == "EXTREME_POI",
            }
        )
        if persist:
            self.save_plan(base)
        return base

    def save_plan(self, plan: Dict[str, Any]) -> None:
        if not isinstance(plan, dict) or not plan.get("plan_id"):
            return
        rows = load_trades(self.storage_path)
        replaced = False
        for idx, existing in enumerate(rows):
            if str(existing.get("plan_id")) == str(plan.get("plan_id")):
                rows[idx] = deepcopy(plan)
                replaced = True
                break
        if not replaced:
            rows.append(deepcopy(plan))
        # keep the file lean: latest 50 plans only
        rows = sorted(rows, key=lambda r: str(r.get("plan_created_at") or ""), reverse=True)[:50]
        save_trades(rows, self.storage_path)

    def latest_plan(self, symbol: str | None = None) -> Dict[str, Any] | None:
        plans = self.recent_plans(limit=1, symbol=symbol)
        return plans[0] if plans else None

    def recent_plans(self, *, limit: int = 20, symbol: str | None = None) -> List[Dict[str, Any]]:
        symbol = str(symbol or self.symbol)
        rows = load_trades(self.storage_path)
        filtered = [row for row in rows if str(row.get("symbol") or "") == symbol]
        filtered.sort(key=lambda r: str(r.get("plan_created_at") or ""), reverse=True)
        return filtered[:limit]

    def _build_fallback_plan(
        self,
        *,
        base: Dict[str, Any],
        symbol: str,
        now: datetime,
        session_label: str,
        session: Dict[str, Any],
        daily_bias: Dict[str, Any],
        macro: Dict[str, Any],
        market_structure: Dict[str, Any],
        liquidity: Dict[str, Any],
        dealing_range: Dict[str, Any],
        zone_context: str,
        reversal_watch: Dict[str, Any],
        current_price: float,
        all_results: Dict[str, Any],
    ) -> Dict[str, Any]:
        fallback = deepcopy(base)
        smc_payload = all_results.get("smc", {}) or {}
        smc_archetype = str(smc_payload.get("day_archetype") or "").upper()
        smc_archetype_confidence = self._f(smc_payload.get("day_archetype_confidence"), 0.0)
        smc_archetype_reason = str(smc_payload.get("day_archetype_reason") or "").strip()
        smc_preferred_execution_family = str(smc_payload.get("preferred_execution_family") or "").upper()
        authority = self._resolve_authority(
            daily_bias=daily_bias,
            macro=macro,
            market_structure=market_structure,
            recent_sweep=(liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {},
            zone_context=zone_context,
            reversal_watch=reversal_watch if isinstance(reversal_watch, dict) else {},
        )
        fallback["authority_state"] = authority["state"]
        fallback["authority_direction"] = authority["direction"]
        fallback["authority_reason"] = authority["reason"]
        fallback["bias_sources"] = authority["sources"]
        fallback["directional_alignment_count"] = authority["count"]
        if authority["state"] != "CONFIRMED" or authority["direction"] not in {"BUY", "SELL"}:
            fallback["plan_reason"] = authority["reason"] or "fallback day map has insufficient authority"
            return fallback

        direction = str(authority["direction"])
        references = self._fallback_reference_levels(
            direction=direction,
            current_price=current_price,
            liquidity=liquidity,
            dealing_range=dealing_range,
            zone_context=zone_context,
        )
        if not references:
            fallback["plan_reason"] = "fallback day map found no usable reference levels"
            return fallback

        primary = self._fallback_candidate(
            direction=direction,
            rank_label="PRIMARY",
            references=references[: self.fallback_max_reference_levels],
            current_price=current_price,
            symbol=symbol,
            session_label=session_label,
            market_structure=market_structure,
            liquidity=liquidity,
            dealing_range=dealing_range,
            zone_context=zone_context,
        )
        if not primary:
            fallback["plan_reason"] = "fallback day map could not construct a primary POI"
            return fallback

        standby = None
        if len(references) > 1:
            standby = self._fallback_candidate(
                direction=direction,
                rank_label="STANDBY",
                references=references[1 : 1 + self.fallback_max_reference_levels],
                current_price=current_price,
                symbol=symbol,
                session_label=session_label,
                market_structure=market_structure,
                liquidity=liquidity,
                dealing_range=dealing_range,
                zone_context=zone_context,
            )
            standby = self._validated_standby(primary, standby, symbol=symbol)

        primary_execution_preview = self._execution_levels(
            direction=direction,
            entry_price=self._f(primary.get("entry_price"), 0.0),
            stop_loss=self._f(primary.get("stop_loss"), 0.0),
            target_price=self._f(primary.get("target_liquidity") or primary.get("target_price"), 0.0),
            symbol=symbol,
        )
        quality_ok, quality_reason, quality_diag = self._plan_quality_guard(
            direction=direction,
            primary=primary,
            standby=standby,
            primary_execution=primary_execution_preview,
            all_results={
                "technical": all_results.get("technical", {}),
                "classical": all_results.get("classical", {}),
                "smc": all_results.get("smc", {}),
                "price_action": all_results.get("price_action", {}),
                "multitimeframe": all_results.get("multitimeframe", {}),
            },
            symbol=symbol,
        )
        if not quality_ok:
            fallback["plan_status"] = "WATCH_ONLY"
            fallback["plan_reason"] = quality_reason
            fallback["notes"] = [quality_reason] if quality_reason else []
            fallback["supporting_agents"] = quality_diag.get("supporting_agents", [])
            fallback["opposing_agents"] = quality_diag.get("opposing_agents", [])
            return fallback
        planner_score, planner_notes = self._planner_score(
            direction=direction,
            primary=primary,
            standby=standby,
            session=session,
            daily_bias=daily_bias,
            macro=macro,
            news={"market_status": "SAFE", "can_trade": True},
            market_structure=market_structure,
            recent_sweep=(liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {},
            zone_context=zone_context,
        )
        if planner_score < self.min_plan_score:
            fallback["plan_reason"] = f"fallback planner score {planner_score:.1f} below {self.min_plan_score:.1f}"
            fallback["notes"] = planner_notes
            return fallback
        execution_readiness = self._execution_readiness(
            planner_source="fallback_day_map",
            direction=direction,
            primary=primary,
            standby=standby,
            all_results=all_results,
            preferred_execution_family=smc_preferred_execution_family,
            macro=macro,
        )
        if execution_readiness.get("state") not in {"PENDING_EXECUTION_READY", "MARKET_EXECUTION_READY"}:
            fallback["plan_status"] = "WATCH_ONLY"
            fallback["plan_reason"] = f"fallback day map has no execution readiness: {execution_readiness.get('reason') or execution_readiness.get('state')}"
            fallback["execution_readiness"] = execution_readiness
            fallback["notes"] = planner_notes + [str(execution_readiness.get("reason") or execution_readiness.get("state") or "execution readiness blocked")]
            return fallback

        scenario_type = str(primary.get("setup_type") or "DAY_MAP_FALLBACK")
        scenario_id = self._scenario_id(symbol, direction, scenario_type, session_label, now)
        plan_id = f"PLAN::{scenario_id}"
        primary_execution = self._execution_levels(
            direction=direction,
            entry_price=self._f(primary.get("entry_price"), 0.0),
            stop_loss=self._f(primary.get("stop_loss"), 0.0),
            target_price=self._f(primary.get("target_liquidity") or primary.get("target_price"), 0.0),
            symbol=symbol,
        )
        standby_execution = self._execution_levels(
            direction=direction,
            entry_price=self._f(standby.get("entry_price"), 0.0),
            stop_loss=self._f(standby.get("stop_loss"), 0.0),
            target_price=self._f(standby.get("target_liquidity") or standby.get("target_price"), 0.0),
            symbol=symbol,
        ) if standby else None
        expected_path = self._expected_path(direction, primary, liquidity, dealing_range, current_price)
        day_objective, day_objective_label = self._day_objective(
            direction=direction,
            scenario_type=scenario_type,
            structure_trend=str(market_structure.get("trend") or "RANGING"),
            recent_sweep=(liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {},
            zone_context=zone_context,
        )
        objective_alignment = (
            "ALIGNED_WITH_MARKET_OBJECTIVE"
            if str(fallback.get("market_objective_direction") or "").upper() == direction
            else "NEUTRAL_TO_MARKET_OBJECTIVE"
        )
        primary_rationale = self._candidate_rationale(primary, direction, str(market_structure.get("trend") or "RANGING"), str(market_structure.get("structure_quality") or "WEAK"), (liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {}, rank_label="PRIMARY")
        standby_rationale = self._candidate_rationale(standby, direction, str(market_structure.get("trend") or "RANGING"), str(market_structure.get("structure_quality") or "WEAK"), (liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {}, rank_label="STANDBY") if standby else []
        execution_preference = self._execution_preference(
            primary,
            standby,
            current_price,
            preferred_execution_family=smc_preferred_execution_family,
        )
        poi_classification = self._classify_poi(primary, direction, str(market_structure.get("structure_quality") or "WEAK"), (liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {}, zone_context, current_price, symbol=symbol)
        primary["poi_classification"] = poi_classification
        primary["extreme_poi"] = poi_classification == "EXTREME_POI"
        plan_narrative = self._plan_narrative(direction, scenario_type, primary, authority["sources"], expected_path, execution_preference)
        manual_plan = self._manual_plan_hierarchy(
            direction=direction,
            primary=primary,
            standby=standby,
            structure_trend=str(market_structure.get("trend") or "RANGING"),
            structure_quality=str(market_structure.get("structure_quality") or "WEAK"),
            recent_sweep=(liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {},
            execution_preference=execution_preference,
            expected_path=expected_path,
            narrative=plan_narrative,
            symbol=symbol,
            day_objective=day_objective,
            day_objective_label=day_objective_label,
            market_objective_label=str(fallback.get("market_objective_label") or ""),
            objective_alignment=objective_alignment,
        )
        manual_plan["target_script"] = {
            "tp1": primary_execution.get("tp1"),
            "tp2": primary_execution.get("tp2"),
        }
        if primary_execution.get("floor_applied"):
            manual_plan["risk_note"] = f"Execution stop normalized to the configured {primary_execution.get('min_sl_distance_points', 0):.0f}-point minimum."

        fallback.update(
            {
                "plan_ready": True,
                "plan_status": "READY",
                "planner_source": "fallback_day_map",
                "scenario_id": scenario_id,
                "plan_id": plan_id,
                "session_bias": direction,
                "scenario_type": scenario_type,
                "thesis_family": f"{direction}::{scenario_type}",
                "primary_poi": self._compact_candidate(primary),
                "standby_poi": self._compact_candidate(standby) if standby else None,
                "primary_entry_zone": self._zone_payload(primary),
                "standby_entry_zone": self._zone_payload(standby) if standby else None,
                "primary_entry_price": primary.get("entry_price"),
                "standby_entry_price": standby.get("entry_price") if standby else None,
                "primary_execution": primary_execution,
                "standby_execution": standby_execution,
                "invalidation_level": primary_execution.get("stop_loss"),
                "target_liquidity": primary.get("target_liquidity") or primary.get("target_price"),
                "day_archetype": smc_archetype or None,
                "day_archetype_confidence": round(smc_archetype_confidence, 1),
                "day_archetype_reason": smc_archetype_reason or None,
                "preferred_execution_family": smc_preferred_execution_family or None,
                "execution_readiness": execution_readiness,
                "planner_confidence": planner_score,
                "planner_grade": self._grade(planner_score),
                "supporting_agents": quality_diag.get("supporting_agents", []),
                "opposing_agents": quality_diag.get("opposing_agents", []),
                "support_count": quality_diag.get("support_count", 0),
                "opposition_count": quality_diag.get("opposition_count", 0),
                "max_pending_orders_allowed": min(self.default_pending_slots, 2 if standby else 1),
                "plan_reason": "fallback day map ready",
                "notes": planner_notes,
                "day_objective": day_objective,
                "day_objective_label": day_objective_label,
                "objective_alignment": objective_alignment,
                "same_box_ladder": bool(manual_plan.get("same_box_ladder")),
                "expected_path": expected_path,
                "execution_preference": execution_preference,
                "primary_rationale": primary_rationale,
                "standby_rationale": standby_rationale,
                "plan_narrative": plan_narrative,
                "manual_plan": manual_plan,
                "poi_classification": poi_classification,
                "extreme_poi": poi_classification == "EXTREME_POI",
            }
        )
        return fallback

    def _resolve_authority(
        self,
        *,
        daily_bias: Dict[str, Any],
        macro: Dict[str, Any],
        market_structure: Dict[str, Any],
        recent_sweep: Dict[str, Any],
        zone_context: str,
        reversal_watch: Dict[str, Any],
    ) -> Dict[str, Any]:
        dirs = {
            "daily_bias": self._bias_to_direction(daily_bias.get("bias")),
            "macro": self._macro_to_direction(macro.get("bias") if isinstance(macro, dict) else None),
            "structure": self._trend_to_direction(str((market_structure or {}).get("trend") or "")),
            "reversal_watch": str((reversal_watch or {}).get("direction") or "").upper() or None,
        }
        counts = {"BUY": 0, "SELL": 0}
        sources = {"BUY": [], "SELL": []}
        for src, direction in dirs.items():
            if direction in counts:
                counts[direction] += 1
                sources[direction].append(src)
        if counts["BUY"] == counts["SELL"] and counts["BUY"] > 0:
            objective = self._market_objective(
                structure_trend=str((market_structure or {}).get("trend") or ""),
                recent_sweep=recent_sweep,
                zone_context=zone_context,
            )
            objective_direction = str(objective.get("direction") or "").upper()
            if objective_direction in {"BUY", "SELL"}:
                objective_sources = list(sources.get(objective_direction, []))
                objective_sources.append("market_objective_tiebreak")
                return {
                    "state": "CONFIRMED",
                    "direction": objective_direction,
                    "sources": objective_sources,
                    "count": counts.get(objective_direction, 0),
                    "reason": (
                        "day-map authority tie resolved by market objective: "
                        f"{objective.get('label') or objective_direction}"
                    ),
                }
            return {
                "state": "CONFLICTED",
                "direction": None,
                "sources": [],
                "count": 0,
                "reason": "day-map authority conflicted between bullish and bearish signals",
            }
        direction = "BUY" if counts["BUY"] > counts["SELL"] else "SELL" if counts["SELL"] > counts["BUY"] else None
        aligned_sweep = self._aligned_sweep(direction, recent_sweep) if direction else False
        zone_bonus = (direction == "SELL" and str(zone_context).upper() == "PREMIUM") or (direction == "BUY" and str(zone_context).upper() == "DISCOUNT")
        count = counts.get(direction, 0) if direction else 0
        state = "CONFIRMED" if direction and (count >= self.min_authority_alignment_count or (count >= 1 and aligned_sweep and zone_bonus)) else "WEAK"
        reason_parts = []
        if direction:
            if sources.get(direction):
                reason_parts.append(f"{direction} alignment from {', '.join(sources[direction])}")
            if aligned_sweep:
                reason_parts.append("aligned liquidity sweep")
            if zone_bonus:
                reason_parts.append(f"{str(zone_context).lower()} map supports the thesis")
        else:
            reason_parts.append("no directional authority sources")
        return {
            "state": state,
            "direction": direction,
            "sources": sources.get(direction, []) if direction else [],
            "count": count,
            "reason": '; '.join(reason_parts) if reason_parts else 'fallback authority weak',
        }

    def _fallback_reference_levels(
        self,
        *,
        direction: str,
        current_price: float,
        liquidity: Dict[str, Any],
        dealing_range: Dict[str, Any],
        zone_context: str,
    ) -> List[Dict[str, Any]]:
        refs: List[Dict[str, Any]] = []
        zone_context = str(zone_context or '').upper()
        if direction == "SELL":
            self._add_level_ref(refs, (liquidity.get("previous_day_levels") or {}).get("high"), "previous_day_high", current_price, above=True)
            self._add_level_ref(refs, (liquidity.get("session_liquidity") or {}).get("high"), "session_high", current_price, above=True)
            self._add_level_ref(refs, dealing_range.get("high"), "dealing_range_high", current_price, above=True)
            self._add_level_ref(refs, dealing_range.get("midpoint") if zone_context == "PREMIUM" else None, "premium_midpoint", current_price, above=True)
            refs.sort(key=lambda r: (r["price"], r["priority"] ))
        else:
            self._add_level_ref(refs, (liquidity.get("previous_day_levels") or {}).get("low"), "previous_day_low", current_price, above=False)
            self._add_level_ref(refs, (liquidity.get("session_liquidity") or {}).get("low"), "session_low", current_price, above=False)
            self._add_level_ref(refs, dealing_range.get("low"), "dealing_range_low", current_price, above=False)
            self._add_level_ref(refs, dealing_range.get("midpoint") if zone_context == "DISCOUNT" else None, "discount_midpoint", current_price, above=False)
            refs.sort(key=lambda r: (-r["price"], r["priority"]))
        # de-duplicate by rounded price
        unique: List[Dict[str, Any]] = []
        seen: set[float] = set()
        for ref in refs:
            marker = round(ref["price"], 2)
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(ref)
        return unique

    def _add_level_ref(self, refs: List[Dict[str, Any]], value: Any, label: str, current_price: float, *, above: bool) -> None:
        price = self._f(value, 0.0)
        if price <= 0:
            return
        if above and price <= current_price:
            return
        if not above and price >= current_price:
            return
        priority = {
            "previous_day_high": 1,
            "previous_day_low": 1,
            "session_high": 2,
            "session_low": 2,
            "dealing_range_high": 3,
            "dealing_range_low": 3,
            "premium_midpoint": 4,
            "discount_midpoint": 4,
        }.get(label, 9)
        refs.append({"price": round(price, 2), "label": label, "priority": priority})

    def _fallback_candidate(
        self,
        *,
        direction: str,
        rank_label: str,
        references: List[Dict[str, Any]],
        current_price: float,
        symbol: str,
        session_label: str,
        market_structure: Dict[str, Any],
        liquidity: Dict[str, Any],
        dealing_range: Dict[str, Any],
        zone_context: str,
    ) -> Dict[str, Any] | None:
        if not references:
            return None
        prices = [self._f(ref.get("price"), 0.0) for ref in references if self._f(ref.get("price"), 0.0) > 0]
        if not prices:
            return None
        half_width = points_to_price(self.fallback_zone_half_width_points, symbol)
        if direction == "SELL":
            zone_low = min(prices)
            zone_high = max(prices)
            entry_price = zone_low
            stop_loss = zone_high
            target_price = self._f((liquidity.get("session_liquidity") or {}).get("low"), 0.0) or self._f((liquidity.get("previous_day_levels") or {}).get("low"), 0.0) or self._f(dealing_range.get("midpoint"), current_price)
        else:
            zone_high = max(prices)
            zone_low = min(prices)
            entry_price = zone_high
            stop_loss = zone_low
            target_price = self._f((liquidity.get("session_liquidity") or {}).get("high"), 0.0) or self._f((liquidity.get("previous_day_levels") or {}).get("high"), 0.0) or self._f(dealing_range.get("midpoint"), current_price)
        scenario_type = "LIQUIDITY_REVERSAL" if self._aligned_sweep(direction, (liquidity.get("recent_sweep") or {})) else "STRUCTURE_CONTINUATION"
        setup_state = "ENTRY_ARMED" if abs(entry_price - current_price) <= half_width else "POI_MARKED"
        role = rank_label.upper()
        quality = 74.0 if role == "PRIMARY" else 68.0
        dominance = 66.0 if role == "PRIMARY" else 58.0
        revisit = "NEAR" if abs(entry_price - current_price) <= half_width * 2 else "MEDIUM"
        ref_labels = [str(ref.get("label") or "") for ref in references]
        poi_classification = "EXTREME_POI" if role == "PRIMARY" and len(ref_labels) >= 2 else "HIGH_PROBABILITY_POI" if role == "PRIMARY" else "STANDARD_POI"
        return {
            "id": f"DAYMAP::{role}::{symbol}::{session_label}",
            "state_key": f"DAYMAP::{role}::{symbol}::{direction}::{scenario_type}::{round(zone_low,2)}:{round(zone_high,2)}",
            "direction": direction,
            "setup_type": scenario_type,
            "setup_state": setup_state,
            "selection_role": role,
            "selection_rank": 1 if role == "PRIMARY" else 2,
            "entry_price": round(entry_price, 2),
            "stop_loss": round(stop_loss, 2),
            "target_price": round(target_price, 2),
            "target_liquidity": round(target_price, 2),
            "poi_type": "extreme_day_map_zone",
            "poi_zone": {"top": round(zone_high, 2), "bottom": round(zone_low, 2)},
            "poi_low": round(zone_low, 2),
            "poi_high": round(zone_high, 2),
            "poi_quality_score": quality,
            "return_probability_score": 62.0 if role == "PRIMARY" else 54.0,
            "thesis_dominance_score": dominance,
            "trigger_state": "AT_POI_WAIT_TRIGGER",
            "trigger_score": 55.0 if role == "PRIMARY" else 48.0,
            "trigger_ready": False,
            "expected_revisit_window": revisit,
            "displacement_score": float(quality * 0.12),
            "quality_score": quality,
            "quality_grade": "B" if role == "PRIMARY" else "C",
            "poi_classification": poi_classification,
            "extreme_poi": poi_classification == "EXTREME_POI",
            "details": {
                "poi": {"mitigation_status": "FRESH"},
                "selection": {"selection_role": role, "reference_levels": ref_labels},
                "fallback_day_map": True,
                "market_trend": market_structure.get("trend"),
                "structure_quality": market_structure.get("structure_quality"),
                "recent_sweep": liquidity.get("recent_sweep") or {},
                "zone_context": zone_context,
            },
        }

    def _zone_width_points(self, candidate: Dict[str, Any] | None, *, symbol: str) -> float:
        zone = self._zone_payload(candidate)
        if not zone:
            return 0.0
        return abs(self._price_to_points(float(zone.get("high", 0)) - float(zone.get("low", 0)), symbol=symbol))

    def _zones_overlap(self, first: Dict[str, Any] | None, second: Dict[str, Any] | None) -> bool:
        zone1 = self._zone_payload(first)
        zone2 = self._zone_payload(second)
        if not zone1 or not zone2:
            return False
        low = max(float(zone1.get("low", 0)), float(zone2.get("low", 0)))
        high = min(float(zone1.get("high", 0)), float(zone2.get("high", 0)))
        return high > low

    def _agent_alignment_summary(self, direction: str, all_results: Dict[str, Any]) -> Dict[str, Any]:
        supporting: List[str] = []
        opposing: List[str] = []
        for name in ["technical", "classical", "smc", "price_action", "multitimeframe"]:
            result = all_results.get(name, {}) or {}
            signal = str(result.get("signal") or result.get("direction") or "WAIT").upper()
            confidence = self._f(result.get("confidence"), 0.0)
            if confidence < self.agent_alignment_min_confidence:
                continue
            if signal == direction:
                supporting.append(name)
            elif signal in {"BUY", "SELL"} and signal != direction:
                opposing.append(name)
        return {
            "support_count": len(supporting),
            "opposition_count": len(opposing),
            "available_count": len(supporting) + len(opposing),
            "supporting_agents": supporting,
            "opposing_agents": opposing,
        }

    def _execution_readiness(
        self,
        *,
        planner_source: str,
        direction: str,
        primary: Dict[str, Any],
        standby: Dict[str, Any] | None,
        all_results: Dict[str, Any],
        preferred_execution_family: str,
        macro: Dict[str, Any],
    ) -> Dict[str, Any]:
        diag = self._agent_alignment_summary(direction, all_results)
        support_count = int(diag.get("support_count", 0) or 0)
        opposition_count = int(diag.get("opposition_count", 0) or 0)
        available_count = int(diag.get("available_count", 0) or 0)
        supporting_agents = list(diag.get("supporting_agents", []) or [])
        smc_result = all_results.get("smc", {}) or {}
        smc_signal = str(smc_result.get("signal") or smc_result.get("direction") or "WAIT").upper()
        smc_conf = self._f(smc_result.get("confidence"), 0.0)
        has_smc_alignment = smc_signal == direction and smc_conf >= max(55.0, self.agent_alignment_min_confidence - 10.0)
        macro_bias = self._macro_to_direction(macro.get("bias") if isinstance(macro, dict) else None)
        macro_conf = self._f(macro.get("confidence"), 0.0) if isinstance(macro, dict) else 0.0
        macro_min = float((((self.config.get("signal_requirements") or {}).get("two_agent_entry") or {}).get("macro_confirmation") or {}).get("min_confidence", 55) or 55)
        has_macro_confirmation = macro_bias == direction and macro_conf >= macro_min
        trigger_state = str(primary.get("trigger_state") or "").upper()
        trigger_ready = bool(primary.get("trigger_ready")) or trigger_state in {"REJECTION_CONFIRMED", "FAILED_RECLAIM_CONFIRMED", "CONTINUATION_BREAKDOWN_CONFIRMED"}
        setup_state = str(primary.get("setup_state") or "").upper()
        same_box_ladder = self._same_box_ladder_pair(primary, standby)
        preferred_execution_family = str(preferred_execution_family or "").upper()

        if trigger_ready and support_count >= 2 and (has_smc_alignment or has_macro_confirmation):
            state = "MARKET_EXECUTION_READY" if preferred_execution_family in {"FAILED_RECLAIM_CONTINUATION", "CONTINUATION_BREAKDOWN"} or trigger_state in {"FAILED_RECLAIM_CONFIRMED", "CONTINUATION_BREAKDOWN_CONFIRMED", "REJECTION_CONFIRMED"} else "PENDING_EXECUTION_READY"
            reason = f"trigger {trigger_state or 'READY'} with {support_count} execution-support agents"
        elif support_count >= 2 and (has_smc_alignment or has_macro_confirmation):
            state = "PENDING_EXECUTION_READY"
            reason = f"{support_count} execution-support agents confirmed the mapped direction"
        elif available_count > 0 and (has_smc_alignment or support_count >= 1 or has_macro_confirmation or setup_state in {"ENTRY_ARMED", "POI_MARKED"}):
            state = "WATCH_EXECUTION"
            reason = "map is valid but still waiting for stronger execution confirmation"
        else:
            state = "MAP_ONLY"
            reason = "map exists, but no execution-support alignment is present"

        if planner_source == "fallback_day_map" and opposition_count > support_count and not has_smc_alignment:
            state = "MAP_ONLY"
            reason = "fallback map is opposed by execution layer context"

        return {
            "state": state,
            "reason": reason,
            "support_count": support_count,
            "opposition_count": opposition_count,
            "available_count": available_count,
            "supporting_agents": supporting_agents,
            "opposing_agents": list(diag.get("opposing_agents", []) or []),
            "has_smc_alignment": has_smc_alignment,
            "has_macro_confirmation": has_macro_confirmation,
            "trigger_state": trigger_state,
            "trigger_ready": bool(trigger_ready),
            "setup_state": setup_state,
            "same_box_ladder": bool(same_box_ladder),
            "preferred_execution_family": preferred_execution_family or None,
            "planner_source": planner_source,
        }

    def _plan_quality_guard(
        self,
        *,
        direction: str,
        primary: Dict[str, Any],
        standby: Dict[str, Any] | None,
        primary_execution: Dict[str, Any],
        all_results: Dict[str, Any],
        symbol: str,
    ) -> tuple[bool, str | None, Dict[str, Any]]:
        diagnostics = self._agent_alignment_summary(direction, all_results)
        diagnostics["primary_zone_width_points"] = round(self._zone_width_points(primary, symbol=symbol), 1)
        diagnostics["standby_zone_width_points"] = round(self._zone_width_points(standby, symbol=symbol), 1) if standby else 0.0
        diagnostics["main_rr"] = round(self._f(primary_execution.get("rr_ratio"), 0.0), 2)
        if diagnostics["primary_zone_width_points"] > self.max_primary_zone_width_points:
            return False, f"main area too wide ({diagnostics['primary_zone_width_points']:.0f} pts)", diagnostics
        if standby and diagnostics["standby_zone_width_points"] > self.max_standby_zone_width_points:
            return False, f"add area too wide ({diagnostics['standby_zone_width_points']:.0f} pts)", diagnostics
        diagnostics["same_box_ladder"] = self._same_box_ladder_pair(primary, standby)
        if standby and self._zones_overlap(primary, standby) and not diagnostics["same_box_ladder"]:
            return False, "add area overlaps the main area", diagnostics
        if diagnostics["main_rr"] < self.min_main_rr_for_ready:
            return False, f"main area RR {diagnostics['main_rr']:.2f} below {self.min_main_rr_for_ready:.2f}", diagnostics
        if diagnostics.get("available_count", 0) > 0:
            if diagnostics["support_count"] < self.min_supporting_agents_for_ready:
                return False, f"only {diagnostics['support_count']} supporting agents for the mapped direction", diagnostics
            if diagnostics["opposition_count"] > self.max_opposing_agents_for_ready:
                return False, f"too many opposing agents ({diagnostics['opposition_count']}) for a ready map", diagnostics
        return True, None, diagnostics

    def _planner_score(
        self,
        *,
        direction: str,
        primary: Dict[str, Any],
        standby: Dict[str, Any] | None,
        session: Dict[str, Any],
        daily_bias: Dict[str, Any],
        macro: Dict[str, Any],
        news: Dict[str, Any],
        market_structure: Dict[str, Any],
        recent_sweep: Dict[str, Any],
        zone_context: str,
    ) -> tuple[float, List[str]]:
        score = 0.0
        notes: List[str] = []

        dominance = self._f(primary.get("thesis_dominance_score"), 0.0)
        return_prob = self._f(primary.get("return_probability_score"), 0.0)
        quality_score = self._f(primary.get("quality_score"), self._f((primary.get("setup_quality") or {}).get("score"), 0.0))
        trigger_score = self._f(primary.get("trigger_score"), 0.0)

        score += dominance * 0.28
        score += return_prob * 0.20
        score += quality_score * 0.16
        score += min(trigger_score, 100.0) * 0.08

        if str(primary.get("selection_role") or "").upper() == "PRIMARY":
            score += 4.0
            notes.append("primary thesis selected")
        if standby:
            score += 2.0
            notes.append("standby thesis available")

        setup_state = str(primary.get("setup_state") or "").upper()
        if setup_state == "ENTRY_ARMED":
            score += 8.0
            notes.append("price already near primary POI")
        elif setup_state == "POI_MARKED":
            score += 4.0
            notes.append("POI already defined")

        daily_dir = self._bias_to_direction(daily_bias.get("bias"))
        if daily_dir == direction:
            score += 6.0
            notes.append("daily bias aligned")
        elif daily_dir in {"BUY", "SELL"} and daily_dir != direction:
            score -= 8.0
            notes.append("daily bias opposes thesis")

        macro_dir = self._macro_to_direction(macro.get("bias") if isinstance(macro, dict) else None)
        if macro_dir == direction:
            score += 6.0
            notes.append("macro bias aligned")
        elif macro_dir in {"BUY", "SELL"} and macro_dir != direction:
            score -= 6.0
            notes.append("macro bias opposes thesis")

        structure_trend = str(market_structure.get("trend") or "RANGING").upper()
        structure_quality = str(market_structure.get("structure_quality") or "WEAK").upper()
        if structure_trend == ("BULLISH" if direction == "BUY" else "BEARISH"):
            score += 7.0
            notes.append("structure trend aligned")
        elif structure_trend in {"BULLISH", "BEARISH"}:
            score -= 6.0
            notes.append("structure trend opposes thesis")
        if structure_quality == "STRONG":
            score += 5.0
            notes.append("strong structure quality")
        elif structure_quality == "MODERATE":
            score += 2.0
            notes.append("moderate structure quality")
        else:
            score -= 4.0
            notes.append("weak structure quality")

        score += self._mitigation_bonus(primary, notes)
        score += self._zone_context_bonus(direction, zone_context, notes)
        score += self._sweep_bonus(direction, recent_sweep, notes)

        session_quality = str(session.get("session_quality") or session.get("quality") or "LOW").upper()
        session_bonus = {"BEST": 6.0, "HIGH": 5.0, "MEDIUM": 2.0, "LOW": 0.0}.get(session_quality, 0.0)
        score += session_bonus
        if session_bonus > 0:
            notes.append(f"session quality {session_quality}")

        news_status = str(news.get("market_status") or "SAFE").upper()
        if news_status == "SAFE":
            score += 2.0
        elif news_status == "CAUTION":
            score -= 1.0
            notes.append("news caution")

        return round(max(0.0, min(100.0, score)), 1), notes

    def _directional_alignment(
        self,
        direction: str,
        daily_bias: Dict[str, Any],
        macro: Dict[str, Any],
        structure_trend: str,
    ) -> Dict[str, Any]:
        sources: List[str] = []
        if self._bias_to_direction(daily_bias.get("bias")) == direction:
            sources.append("daily_bias")
        if self._macro_to_direction(macro.get("bias") if isinstance(macro, dict) else None) == direction:
            sources.append("macro")
        if structure_trend == ("BULLISH" if direction == "BUY" else "BEARISH"):
            sources.append("structure")
        return {"count": len(sources), "sources": sources}

    def _rank_planner_candidates(
        self,
        candidates: List[Dict[str, Any]],
        *,
        current_price: float,
        structure_trend: str,
        recent_sweep: Dict[str, Any],
        zone_context: str,
        symbol: str,
    ) -> List[Dict[str, Any]]:
        ranked: List[tuple[float, Dict[str, Any]]] = []
        for raw in candidates or []:
            if not isinstance(raw, dict):
                continue
            candidate = deepcopy(raw)
            score = self._candidate_priority_score(
                candidate,
                current_price=current_price,
                structure_trend=structure_trend,
                recent_sweep=recent_sweep,
                zone_context=zone_context,
                symbol=symbol,
            )
            candidate["planner_priority_score"] = round(score, 2)
            ranked.append((score, candidate))
        ranked.sort(
            key=lambda item: (
                item[0],
                self._f(item[1].get("thesis_dominance_score"), 0.0),
                self._f(item[1].get("return_probability_score"), 0.0),
                -self._f(item[1].get("selection_rank"), 99.0),
            ),
            reverse=True,
        )
        ordered = [candidate for _, candidate in ranked]
        for idx, candidate in enumerate(ordered, start=1):
            candidate["selection_rank"] = idx
            if idx == 1:
                candidate["selection_role"] = "PRIMARY"
            elif str(candidate.get("direction") or "").upper() == str((ordered[0] or {}).get("direction") or "").upper():
                candidate["selection_role"] = "STANDBY"
        return ordered

    def _candidate_priority_score(
        self,
        candidate: Dict[str, Any],
        *,
        current_price: float,
        structure_trend: str,
        recent_sweep: Dict[str, Any],
        zone_context: str,
        symbol: str,
    ) -> float:
        direction = str(candidate.get("direction") or "").upper()
        if direction not in {"BUY", "SELL"}:
            return -999.0
        dominance = self._f(candidate.get("thesis_dominance_score"), 0.0)
        reach = self._f(candidate.get("return_probability_score"), 0.0)
        quality = self._f(candidate.get("quality_score"), self._f((candidate.get("setup_quality") or {}).get("score"), 0.0))
        trigger = self._f(candidate.get("trigger_score"), 0.0)
        base = dominance * 0.42 + reach * 0.24 + quality * 0.20 + trigger * 0.14

        role = str(candidate.get("selection_role") or "").upper()
        if role == "PRIMARY":
            base += 4.0
        elif role == "STANDBY":
            base += 1.5

        entry_price = self._f(candidate.get("entry_price"), 0.0)
        mitigation = str((((candidate.get("details") or {}).get("poi") or {}).get("mitigation_status") or "")).upper()
        objective, _ = self._day_objective(
            direction=direction,
            scenario_type=str(candidate.get("setup_type") or ""),
            structure_trend=structure_trend,
            recent_sweep=recent_sweep,
            zone_context=zone_context,
        )
        if objective == "UPSIDE_CONTINUATION_AFTER_SWEEP":
            if direction == "BUY":
                if entry_price > 0 and current_price > 0 and entry_price < current_price:
                    base += 12.0
                if mitigation == "FRESH":
                    base += 4.0
            else:
                base -= 8.0
        elif objective == "DOWNSIDE_CONTINUATION_AFTER_SWEEP":
            if direction == "SELL":
                if entry_price > 0 and current_price > 0 and entry_price > current_price:
                    base += 12.0
                if mitigation == "FRESH":
                    base += 4.0
            else:
                base -= 8.0
        elif objective == "DISCOUNT_REVERSAL_LONG" and direction == "BUY":
            if str(zone_context or "").upper() == "DISCOUNT":
                base += 6.0
        elif objective == "PREMIUM_REVERSAL_SHORT" and direction == "SELL":
            if str(zone_context or "").upper() == "PREMIUM":
                base += 6.0

        if self._aligned_sweep(direction, recent_sweep):
            base += 3.0
        if str(structure_trend or "").upper() == ("BULLISH" if direction == "BUY" else "BEARISH"):
            base += 3.0

        zone = self._zone_payload(candidate)
        if zone and current_price > 0:
            low = float(zone.get("low", 0) or 0)
            high = float(zone.get("high", 0) or 0)
            if low > 0 and high > 0:
                if direction == "BUY" and high < current_price:
                    base += 2.0
                elif direction == "SELL" and low > current_price:
                    base += 2.0
                elif low <= current_price <= high:
                    base += 1.0
        return round(base, 2)

    def _same_box_ladder_pair(self, first: Dict[str, Any] | None, second: Dict[str, Any] | None) -> bool:
        if not isinstance(first, dict) or not isinstance(second, dict):
            return False
        first_sel = (((first.get("details") or {}).get("selection") or {}) if isinstance(first.get("details"), dict) else {})
        second_sel = (((second.get("details") or {}).get("selection") or {}) if isinstance(second.get("details"), dict) else {})
        if not bool(first_sel.get("same_box_ladder")) or not bool(second_sel.get("same_box_ladder")):
            return False
        first_parent = str(first_sel.get("ladder_parent_id") or "").strip()
        second_parent = str(second_sel.get("ladder_parent_id") or "").strip()
        return bool(first_parent and second_parent and first_parent == second_parent)

    def _validated_standby(self, primary: Dict[str, Any], standby: Dict[str, Any] | None, *, symbol: str) -> Dict[str, Any] | None:
        if not isinstance(standby, dict) or not standby:
            return None
        if self._same_box_ladder_pair(primary, standby):
            return standby
        primary_entry = self._f(primary.get("entry_price"), 0.0)
        standby_entry = self._f(standby.get("entry_price"), 0.0)
        if primary_entry > 0 and standby_entry > 0:
            distance_points = abs(self._price_to_points(standby_entry - primary_entry, symbol=symbol))
            if distance_points < self.standby_min_distance_points:
                return None
        if self._zones_overlap(primary, standby):
            return None
        primary_zone = self._zone_payload(primary)
        if primary_zone and standby_entry > 0:
            if float(primary_zone.get("low", 0)) <= standby_entry <= float(primary_zone.get("high", 0)):
                return None
        return standby

    def _mitigation_bonus(self, candidate: Dict[str, Any], notes: List[str]) -> float:
        details = candidate.get("details") or {}
        poi = details.get("poi") if isinstance(details, dict) else {}
        mitigation = str((poi or {}).get("mitigation_status") or "").upper()
        if mitigation == "FRESH":
            notes.append("fresh POI")
            return 6.0
        if mitigation == "TESTED":
            notes.append("tested POI")
            return 2.0
        if mitigation in {"MITIGATED", "PARTIAL"}:
            notes.append("mitigated POI")
            return -6.0
        if mitigation == "INVALIDATED":
            notes.append("invalidated POI")
            return -20.0
        return 0.0

    def _zone_context_bonus(self, direction: str, zone_context: str, notes: List[str]) -> float:
        zone_context = str(zone_context or "").upper()
        if direction == "SELL" and zone_context in {"PREMIUM", "EQUILIBRIUM"}:
            notes.append(f"{zone_context.lower()} sell map")
            return 5.0 if zone_context == "PREMIUM" else 2.5
        if direction == "BUY" and zone_context in {"DISCOUNT", "EQUILIBRIUM"}:
            notes.append(f"{zone_context.lower()} buy map")
            return 5.0 if zone_context == "DISCOUNT" else 2.5
        if zone_context:
            notes.append(f"zone context {zone_context.lower()} opposes thesis")
            return -4.0
        return 0.0

    def _aligned_sweep(self, direction: str, recent_sweep: Dict[str, Any]) -> bool:
        sweep_type = str((recent_sweep or {}).get("type") or "")
        return (direction == "BUY" and sweep_type == "sell_side") or (direction == "SELL" and sweep_type == "buy_side")

    def _sweep_bonus(self, direction: str, recent_sweep: Dict[str, Any], notes: List[str]) -> float:
        if not isinstance(recent_sweep, dict) or not recent_sweep.get("occurred"):
            return 0.0
        if not self._aligned_sweep(direction, recent_sweep):
            notes.append("recent sweep opposes thesis")
            return -4.0
        confirmation = str(recent_sweep.get("confirmation") or "").upper()
        reference_type = str(recent_sweep.get("reference_type") or "liquidity").replace("_", " ")
        notes.append(f"aligned {reference_type} sweep ({confirmation or 'UNKNOWN'})")
        if confirmation == "STRONG":
            return 8.0
        if confirmation == "MODERATE":
            return 5.0
        return 2.0

    def _structure_quality_ok(self, structure_quality: str, setup_type: str, recent_sweep: Dict[str, Any]) -> bool:
        ranks = {"WEAK": 0, "MODERATE": 1, "STRONG": 2}
        actual = ranks.get(str(structure_quality or "WEAK").upper(), 0)
        required = ranks.get(self.require_structure_quality, 1)
        if str(setup_type or "").upper() == "LIQUIDITY_REVERSAL" and recent_sweep.get("occurred"):
            return actual >= 0
        return actual >= required

    def _expected_path(
        self,
        direction: str,
        primary: Dict[str, Any],
        liquidity: Dict[str, Any],
        dealing_range: Dict[str, Any],
        current_price: float,
    ) -> str:
        target = primary.get("target_liquidity") or primary.get("target_price")
        sweep = (liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {}
        ref = str(sweep.get("reference_type") or "liquidity").replace("_", " ")
        if direction == "SELL":
            return f"Premium-to-discount sell path: reject after {ref} sweep, hold below {primary.get('stop_loss')} and target {target or dealing_range.get('midpoint') or current_price}."
        return f"Discount-to-premium buy path: react after {ref} sweep, hold above {primary.get('stop_loss')} and target {target or dealing_range.get('midpoint') or current_price}."

    def _market_objective(self, *, structure_trend: str, recent_sweep: Dict[str, Any], zone_context: str) -> Dict[str, Any]:
        structure_trend = str(structure_trend or "RANGING").upper()
        zone_context = str(zone_context or "").upper()
        sweep_type = str((recent_sweep or {}).get("type") or "")
        if structure_trend == "BULLISH" and sweep_type == "sell_side":
            return {"direction": "BUY", "objective": "UPSIDE_CONTINUATION_AFTER_SWEEP", "label": "Upside continuation after mitigation"}
        if structure_trend == "BEARISH" and sweep_type == "buy_side":
            return {"direction": "SELL", "objective": "DOWNSIDE_CONTINUATION_AFTER_SWEEP", "label": "Downside continuation after mitigation"}
        if structure_trend == "BULLISH" and zone_context == "DISCOUNT":
            return {"direction": "BUY", "objective": "UPSIDE_SESSION_BIAS", "label": "Upside session bias"}
        if structure_trend == "BEARISH" and zone_context == "PREMIUM":
            return {"direction": "SELL", "objective": "DOWNSIDE_SESSION_BIAS", "label": "Downside session bias"}
        return {"direction": None, "objective": None, "label": None}

    def _day_objective(
        self,
        *,
        direction: str,
        scenario_type: str,
        structure_trend: str,
        recent_sweep: Dict[str, Any],
        zone_context: str,
    ) -> tuple[str, str]:
        direction = str(direction or "").upper()
        scenario_type = str(scenario_type or "").upper()
        structure_trend = str(structure_trend or "RANGING").upper()
        zone_context = str(zone_context or "").upper()
        aligned_sweep = self._aligned_sweep(direction, recent_sweep or {})
        if direction == "BUY":
            if aligned_sweep and structure_trend == "BULLISH":
                return "UPSIDE_CONTINUATION_AFTER_SWEEP", "Upside continuation after mitigation"
            if scenario_type == "LIQUIDITY_REVERSAL" or zone_context == "DISCOUNT":
                return "DISCOUNT_REVERSAL_LONG", "Reversal long from discount"
            return "UPSIDE_SESSION_BIAS", "Upside session bias"
        if aligned_sweep and structure_trend == "BEARISH":
            return "DOWNSIDE_CONTINUATION_AFTER_SWEEP", "Downside continuation after mitigation"
        if scenario_type == "LIQUIDITY_REVERSAL" or zone_context == "PREMIUM":
            return "PREMIUM_REVERSAL_SHORT", "Reversal short from premium"
        return "DOWNSIDE_SESSION_BIAS", "Downside session bias"

    def _counter_objective_reversal_proof(self, candidate: Dict[str, Any], *, zone_context: str) -> tuple[bool, str]:
        scenario_type = str(candidate.get("setup_type") or "").upper()
        trigger_state = str(candidate.get("trigger_state") or "").upper()
        setup_state = str(candidate.get("setup_state") or "").upper()
        trigger_score = self._f(candidate.get("trigger_score"), 0.0)
        direction = str(candidate.get("direction") or "").upper()
        zone_context = str(zone_context or "").upper()
        premium_discount_aligned = (direction == "SELL" and zone_context == "PREMIUM") or (direction == "BUY" and zone_context == "DISCOUNT")
        if scenario_type != "LIQUIDITY_REVERSAL":
            return False, "setup is not a liquidity reversal"
        if not premium_discount_aligned:
            return False, "counter-objective setup is not located in the opposing premium/discount zone"
        if trigger_state != "REJECTION_CONFIRMED":
            return False, f"trigger state is {trigger_state or 'UNCONFIRMED'}"
        if setup_state not in {"ENTRY_ARMED", "ENTRY_TRIGGERED", "POI_MARKED"}:
            return False, f"setup state is {setup_state or 'UNKNOWN'}"
        if trigger_score < max(self.min_trigger_score, 60.0):
            return False, f"trigger score {trigger_score:.1f} is below reversal proof threshold"
        return True, "reversal proof confirmed"

    def _execution_preference(
        self,
        primary: Dict[str, Any],
        standby: Dict[str, Any] | None,
        current_price: float,
        *,
        preferred_execution_family: str = "",
    ) -> str:
        trigger_state = str(primary.get("trigger_state") or "").upper()
        setup_state = str(primary.get("setup_state") or "").upper()
        entry_price = self._f(primary.get("entry_price"), 0.0)
        poi_classification = str(primary.get("poi_classification") or "").upper()
        family = str(preferred_execution_family or "").upper()
        if family == "MITIGATION_LADDER":
            return "LADDER_PENDING" if standby else "SINGLE_PENDING"
        if family in {"FAILED_RECLAIM_CONTINUATION", "CONTINUATION_BREAKDOWN"}:
            return "NEAR_MARKET_WATCH" if trigger_state in {"FAILED_RECLAIM_CONFIRMED", "CONTINUATION_BREAKDOWN_CONFIRMED"} else "SINGLE_PENDING"
        if family == "REVERSAL_MAP":
            return "SINGLE_PENDING" if standby is None else "LADDER_PENDING"
        if poi_classification == "EXTREME_POI":
            return "SPLIT_EXECUTION_WATCH"
        if setup_state == "ENTRY_ARMED" and trigger_state == "REJECTION_CONFIRMED":
            return "NEAR_MARKET_WATCH"
        if setup_state == "ENTRY_ARMED" and entry_price > 0 and abs(entry_price - current_price) <= 3.0:
            return "NEAR_MARKET_WATCH"
        if standby:
            return "LADDER_PENDING"
        return "SINGLE_PENDING"

    def _candidate_rationale(
        self,
        candidate: Dict[str, Any] | None,
        direction: str,
        structure_trend: str,
        structure_quality: str,
        recent_sweep: Dict[str, Any],
        *,
        rank_label: str,
    ) -> List[str]:
        if not isinstance(candidate, dict) or not candidate:
            return []
        reasons: List[str] = []
        if candidate.get("poi_type"):
            reasons.append(f"{rank_label.lower()} uses {candidate.get('poi_type')} POI")
        if candidate.get("poi_classification"):
            reasons.append(f"classified as {candidate.get('poi_classification')}")
        if candidate.get("expected_revisit_window"):
            reasons.append(f"revisit window {candidate.get('expected_revisit_window')}")
        if candidate.get("trigger_state"):
            reasons.append(f"trigger {candidate.get('trigger_state')}")
        if self._aligned_sweep(direction, recent_sweep):
            reasons.append("liquidity sweep supports the path")
        reasons.append(f"structure {structure_trend.lower()} / {structure_quality.lower()}")
        return reasons[:5]

    def _execution_levels(
        self,
        *,
        direction: str,
        entry_price: float,
        stop_loss: float,
        target_price: float,
        symbol: str,
    ) -> Dict[str, Any]:
        risk_cfg = (self.config.get("risk_settings") or {}) if isinstance(self.config, dict) else {}
        min_sl_points = self._f(risk_cfg.get("min_sl_distance_points"), 0.0)
        min_sl_distance = points_to_price(min_sl_points, symbol) if min_sl_points > 0 else 0.0
        max_rr = self._f(risk_cfg.get("max_rr_ratio"), 0.0)
        floor_applied = False
        adjusted_stop = float(stop_loss)

        if min_sl_distance > 0 and abs(entry_price - adjusted_stop) < min_sl_distance:
            adjusted_stop = entry_price - min_sl_distance if direction == "BUY" else entry_price + min_sl_distance
            sl_mult = self._f(risk_cfg.get("atr_multiplier_sl"), 2.0) or 2.0
            tp1_ratio = (self._f(risk_cfg.get("atr_multiplier_tp1"), 2.5) or 2.5) / sl_mult
            tp2_ratio = (self._f(risk_cfg.get("atr_multiplier_tp2"), 4.5) or 4.5) / sl_mult
            if direction == "BUY":
                tp1 = entry_price + min_sl_distance * tp1_ratio
                tp2 = entry_price + min_sl_distance * tp2_ratio
            else:
                tp1 = entry_price - min_sl_distance * tp1_ratio
                tp2 = entry_price - min_sl_distance * tp2_ratio
            floor_applied = True
            target_method = "rr_from_floored_sl"
        else:
            tp1, tp2, _ = self._plan_targets(direction, entry_price, adjusted_stop, target_price)
            target_method = "mapped_target"

        risk = abs(adjusted_stop - entry_price)
        if max_rr > 0 and risk > 0:
            max_tp2_distance = risk * max_rr
            if direction == "BUY" and tp2 - entry_price > max_tp2_distance:
                tp2 = entry_price + max_tp2_distance
                target_method += "+max_rr_cap"
            elif direction == "SELL" and entry_price - tp2 > max_tp2_distance:
                tp2 = entry_price - max_tp2_distance
                target_method += "+max_rr_cap"

        rr = abs(tp2 - entry_price) / risk if risk > 0 else 0.0
        return {
            "entry_price": round(entry_price, 2),
            "stop_loss": round(adjusted_stop, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2),
            "rr_ratio": round(rr, 2),
            "floor_applied": floor_applied,
            "target_method": target_method,
            "min_sl_distance_points": round(min_sl_points, 1),
        }

    @staticmethod
    def _plan_targets(direction: str, entry_price: float, stop_loss: float, target_price: float) -> tuple[float, float, float]:
        risk = abs(stop_loss - entry_price)
        reward = abs(target_price - entry_price)
        if risk <= 0 or reward <= 0:
            tp1 = target_price
            tp2 = target_price
            rr = 0.0
            return round(tp1, 2), round(tp2, 2), rr
        one_r = risk
        half_reward = reward * 0.5
        tp1_dist = min(max(one_r, reward * 0.35), half_reward if half_reward > 0 else one_r)
        if direction == "BUY":
            tp1 = entry_price + tp1_dist
            tp2 = target_price
        else:
            tp1 = entry_price - tp1_dist
            tp2 = target_price
        rr = reward / risk if risk > 0 else 0.0
        return round(tp1, 2), round(tp2, 2), round(rr, 2)

    def _manual_plan_hierarchy(
        self,
        *,
        direction: str,
        primary: Dict[str, Any],
        standby: Dict[str, Any] | None,
        structure_trend: str,
        structure_quality: str,
        recent_sweep: Dict[str, Any],
        execution_preference: str,
        expected_path: str,
        narrative: str,
        symbol: str,
        day_objective: str,
        day_objective_label: str,
        market_objective_label: str,
        objective_alignment: str,
    ) -> Dict[str, Any]:
        side_word = "BUY" if direction == "BUY" else "SELL"
        same_box_ladder = self._same_box_ladder_pair(primary, standby)
        main_label = f"MAIN {side_word} AREA"
        add_label = f"MORE {side_word} AREA" if same_box_ladder else f"ADD {side_word} AREA"
        primary_entry = self._f(primary.get("entry_price"), 0.0)
        invalidation = self._f(primary.get("stop_loss"), 0.0)
        target = self._f(primary.get("target_liquidity") or primary.get("target_price"), 0.0)
        trigger_state = str(primary.get("trigger_state") or "").upper()
        setup_state = str(primary.get("setup_state") or "").upper()
        sweep_type = str((recent_sweep or {}).get("type") or "").replace("_", " ")

        confirmation_items: List[str] = []
        if self._aligned_sweep(direction, recent_sweep):
            confirmation_items.append(
                f"Preferred confirmation: rejection after {sweep_type} sweep inside the mapped area."
            )
        if trigger_state == "REJECTION_CONFIRMED":
            confirmation_items.append("Trigger is already rejection-confirmed; price reaction quality matters more than chasing.")
        elif trigger_state:
            confirmation_items.append(
                f"Wait for trigger improvement from {trigger_state.replace('_', ' ').lower()} before aggressive execution."
            )
        if setup_state == "ENTRY_ARMED":
            confirmation_items.append("The zone is armed now — react to live rejection / acceptance instead of forcing late entries.")
        if structure_trend:
            confirmation_items.append(f"Keep {structure_trend.lower()} structure intact; no acceptance through invalidation.")
        if not confirmation_items:
            confirmation_items.append("Wait for live confirmation inside the mapped zone before committing.")

        if standby:
            missed_area_plan = (
                f"If the main area is missed, do not chase. Wait for {add_label.lower()} around "
                f"{self._f(standby.get('entry_price'), 0.0):.2f} while the same thesis stays intact."
            )
        elif str(execution_preference).upper() == "SPLIT_EXECUTION_WATCH":
            missed_area_plan = (
                "If price starts delivering early from the extreme zone, only starter execution is valid; "
                "otherwise wait for deeper mitigation, no chase."
            )
        else:
            missed_area_plan = "If price misses the mapped area, do not chase the move. Wait for a fresh rebuild or a cleaner retest."

        if invalidation > 0:
            if direction == "BUY":
                map_change_plan = f"If an opposite buy-side sweep forms and price then accepts below {invalidation:.2f}, cancel this buy map and rebuild."
            else:
                map_change_plan = f"If an opposite sell-side sweep forms and price then accepts above {invalidation:.2f}, cancel this sell map and rebuild."
        else:
            opposite_sweep_label = "buy-side" if direction == "BUY" else "sell-side"
            map_change_plan = f"If an opposite {opposite_sweep_label} sweep appears with structure flip, cancel this map and rebuild."

        execution_items: List[str] = []
        mode = str(execution_preference or "").upper()
        if objective_alignment == "COUNTER_OBJECTIVE_REVERSAL_CONFIRMED":
            execution_items = [
                "This is a counter-objective reversal, so main leg only is executable for now.",
                "Do not add a second leg unless the broader market objective flips later.",
                "If rejection quality weakens, stand down instead of forcing continuation against the day objective.",
            ]
        elif mode == "LADDER_PENDING":
            execution_items = [
                "Work the main area first.",
                "Keep the add area only if the thesis is still intact after the first reaction.",
                "No blind chasing outside the mapped zones.",
            ]
        elif mode == "SPLIT_EXECUTION_WATCH":
            execution_items = [
                "Starter execution is allowed only if price is already inside the extreme area.",
                "Keep the deeper add-on for better location, not for emotional averaging.",
                "Both legs still obey one invalidation logic.",
            ]
        elif mode == "NEAR_MARKET_WATCH":
            execution_items = [
                "Price is already near the zone.",
                "Wait for live reaction / rejection quality before committing.",
                "If no clean reaction appears, stand down and rebuild later.",
            ]
        else:
            execution_items = [
                "Take only the mapped entry, not an emotional chase.",
                "If the area is missed, wait for a fresh setup.",
            ]

        tp1 = None
        if primary_entry > 0 and invalidation > 0 and target > 0:
            risk = abs(invalidation - primary_entry)
            reward = abs(target - primary_entry)
            if risk > 0 and reward > 0:
                tp1_dist = min(max(risk, reward * 0.35), reward * 0.5)
                tp1 = round(primary_entry + tp1_dist, 2) if direction == "BUY" else round(primary_entry - tp1_dist, 2)

        execution_priority_label = (
            "Counter-objective reversal — main leg only"
            if objective_alignment == "COUNTER_OBJECTIVE_REVERSAL_CONFIRMED"
            else "Same-box ladder — main then more inside one POI"
            if same_box_ladder and standby
            else "Continuation priority — main then add"
            if standby
            else "Single mapped execution"
        )
        return {
            "headline": f"{side_word} DAY MAP",
            "bias_label": f"MAIN {side_word} BIAS",
            "objective": day_objective,
            "objective_label": day_objective_label,
            "market_objective_label": market_objective_label,
            "objective_alignment": objective_alignment,
            "execution_priority_label": execution_priority_label,
            "main_area_label": main_label,
            "add_area_label": add_label,
            "confirmation_items": confirmation_items[:4],
            "missed_area_plan": missed_area_plan,
            "map_change_plan": map_change_plan,
            "execution_items": execution_items,
            "target_script": {
                "tp1": tp1,
                "tp2": round(target, 2) if target > 0 else None,
            },
            "same_box_ladder": same_box_ladder,
            "expected_path": expected_path,
            "narrative": narrative,
            "structure_script": f"{structure_trend} / {structure_quality}",
        }

    def _plan_narrative(
        self,
        direction: str,
        scenario_type: str,
        primary: Dict[str, Any],
        bias_sources: List[str],
        expected_path: str,
        execution_preference: str,
    ) -> str:
        bias_txt = ", ".join(bias_sources) if bias_sources else "internal setup context"
        poi_class = str(primary.get("poi_classification") or "STANDARD_POI")
        return (
            f"{direction} {scenario_type}: bias supported by {bias_txt}; "
            f"{poi_class} around {primary.get('entry_price')} with {execution_preference.lower()} execution. "
            f"{expected_path}"
        )

    def _classify_poi(
        self,
        candidate: Dict[str, Any],
        direction: str,
        structure_quality: str,
        recent_sweep: Dict[str, Any],
        zone_context: str,
        current_price: float,
        *,
        symbol: str,
    ) -> str:
        dominance = self._f(candidate.get("thesis_dominance_score"), 0.0)
        reach = self._f(candidate.get("return_probability_score"), 0.0)
        quality = self._f(candidate.get("quality_score"), self._f((candidate.get("setup_quality") or {}).get("score"), 0.0))
        trigger = self._f(candidate.get("trigger_score"), 0.0)
        entry = self._f(candidate.get("entry_price"), 0.0)
        move_points = abs(self._price_to_points(entry - current_price, symbol=symbol)) if entry > 0 and current_price > 0 else 0.0
        sweep_aligned = self._aligned_sweep(direction, recent_sweep)
        premium_discount_aligned = (direction == "SELL" and str(zone_context).upper() == "PREMIUM") or (direction == "BUY" and str(zone_context).upper() == "DISCOUNT")
        if (
            dominance >= 70
            and reach >= 58
            and quality >= 75
            and trigger >= 45
            and str(structure_quality).upper() in {"STRONG", "MODERATE"}
            and (sweep_aligned or premium_discount_aligned)
            and move_points <= 260
        ):
            return "EXTREME_POI"
        if (
            dominance >= 56
            and reach >= 46
            and quality >= 68
            and str(structure_quality).upper() in {"STRONG", "MODERATE"}
        ):
            return "HIGH_PROBABILITY_POI"
        return "STANDARD_POI"

    def _scenario_id(self, symbol: str, direction: str, scenario_type: str, session_label: str, now: datetime) -> str:
        local_now = self._local_now(now)
        date_key = local_now.strftime("%Y%m%d")
        session_key = self._slug(session_label)
        scenario_key = self._slug(scenario_type)
        return f"SCENARIO::{symbol}::{date_key}::{session_key}::{direction}::{scenario_key}"

    @staticmethod
    def _compact_candidate(candidate: Dict[str, Any] | None) -> Dict[str, Any] | None:
        if not candidate:
            return None
        keep = {
            "id",
            "state_key",
            "direction",
            "setup_type",
            "setup_state",
            "selection_role",
            "selection_rank",
            "entry_price",
            "stop_loss",
            "target_price",
            "target_liquidity",
            "poi_type",
            "poi_low",
            "poi_high",
            "poi_quality_score",
            "return_probability_score",
            "thesis_dominance_score",
            "trigger_state",
            "trigger_score",
            "trigger_ready",
            "expected_revisit_window",
            "displacement_score",
            "quality_score",
            "quality_grade",
            "poi_classification",
            "extreme_poi",
            "priority_score",
            "objective_alignment",
            "objective_direction",
        }
        return {key: candidate.get(key) for key in keep if key in candidate}

    @staticmethod
    def _zone_payload(candidate: Dict[str, Any] | None) -> Dict[str, Any] | None:
        if not candidate:
            return None
        zone = candidate.get("poi_zone") or {}
        if isinstance(zone, dict) and zone.get("top") is not None and zone.get("bottom") is not None:
            try:
                top = float(zone.get("top"))
                bottom = float(zone.get("bottom"))
                return {
                    "low": min(top, bottom),
                    "high": max(top, bottom),
                    "source": candidate.get("poi_type") or "poi",
                }
            except (TypeError, ValueError):
                pass
        low = candidate.get("poi_low")
        high = candidate.get("poi_high")
        if low is not None and high is not None:
            try:
                low_f = float(low)
                high_f = float(high)
                return {"low": min(low_f, high_f), "high": max(low_f, high_f), "source": candidate.get("poi_type") or "poi"}
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _bias_to_direction(value: Any) -> str | None:
        text = str(value or "").upper()
        if text == "BULLISH":
            return "BUY"
        if text == "BEARISH":
            return "SELL"
        return None

    @staticmethod
    def _macro_to_direction(value: Any) -> str | None:
        text = str(value or "").upper()
        if text == "BULLISH_GOLD":
            return "BUY"
        if text == "BEARISH_GOLD":
            return "SELL"
        return None

    @staticmethod
    def _trend_to_direction(value: Any) -> str | None:
        text = str(value or "").upper()
        if text == "BULLISH":
            return "BUY"
        if text == "BEARISH":
            return "SELL"
        return None

    def _session_quality_ok(self, value: str) -> bool:
        ranks = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "BEST": 3}
        return ranks.get(str(value or "LOW").upper(), 0) >= ranks.get(self.require_session_quality, 2)

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 88:
            return "A+"
        if score >= 80:
            return "A"
        if score >= 70:
            return "B"
        if score >= 60:
            return "C"
        return "D"

    def _local_now(self, now: datetime) -> datetime:
        tz_name = str((self.config.get("schedule", {}) or {}).get("timezone") or (self.config.get("trading_hours", {}) or {}).get("timezone") or "Asia/Hebron")
        try:
            return now.astimezone(ZoneInfo(tz_name))
        except Exception:
            return now.astimezone(timezone.utc)

    @staticmethod
    def _slug(value: Any) -> str:
        return "_".join(str(value or "").strip().upper().split()) or "UNKNOWN"

    @staticmethod
    def _iso(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

    def _price_to_points(self, price_delta: float, *, symbol: str) -> float:
        try:
            return float(price_to_points(price_delta, symbol=symbol))
        except Exception:
            return 0.0

    @staticmethod
    def _f(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
