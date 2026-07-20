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
        current_price = self._f(all_results.get("current_price"), 0.0)

        now = datetime.now(timezone.utc)
        session_label = str(session.get("current_session") or session.get("session") or "Unknown Session")
        session_quality = str(session.get("session_quality") or session.get("quality") or "LOW").upper()

        structure_trend = str(market_structure.get("trend") or "RANGING").upper()
        structure_quality = str(market_structure.get("structure_quality") or "WEAK").upper()
        recent_sweep = (liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {}

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
                current_price=current_price,
            )
            if fallback.get("plan_ready"):
                if persist:
                    self.save_plan(fallback)
                return fallback
            base["plan_reason"] = fallback.get("plan_reason") or "no structured setup candidates available"
            base["authority_state"] = fallback.get("authority_state", base.get("authority_state"))
            base["authority_direction"] = fallback.get("authority_direction")
            base["authority_reason"] = fallback.get("authority_reason")
            return base

        primary = next((c for c in candidates if str(c.get("selection_role") or "").upper() == "PRIMARY"), None)
        standby = next((c for c in candidates if str(c.get("selection_role") or "").upper() == "STANDBY"), None)
        if not primary:
            primary = candidates[0]

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
                current_price=current_price,
            )
            if fallback.get("plan_ready"):
                if persist:
                    self.save_plan(fallback)
                return fallback
            base["plan_reason"] = (
                f"primary thesis too weak for planning (dominance {primary_dom:.1f}, return probability {primary_rp:.1f})"
            )
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

        alignment = self._directional_alignment(direction, daily_bias, macro if isinstance(macro, dict) else {}, structure_trend)
        base["bias_sources"] = alignment["sources"]
        base["directional_alignment_count"] = alignment["count"]
        if alignment["count"] == 0 and not self._aligned_sweep(direction, recent_sweep):
            base["plan_reason"] = "no strong bias alignment for a morning plan"
            return base
        if not self._structure_quality_ok(structure_quality, str(primary.get("setup_type") or ""), recent_sweep):
            base["plan_reason"] = f"structure quality {structure_quality} is too weak for this plan type"
            return base

        standby = self._validated_standby(primary, standby, symbol=symbol)
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

        scenario_type = str(primary.get("setup_type") or "SCENARIO")
        scenario_id = self._scenario_id(symbol, direction, scenario_type, session_label, now)
        plan_id = f"PLAN::{scenario_id}"
        expected_path = self._expected_path(direction, primary, liquidity, dealing_range, current_price)
        primary_rationale = self._candidate_rationale(primary, direction, structure_trend, structure_quality, recent_sweep, rank_label="PRIMARY")
        standby_rationale = self._candidate_rationale(standby, direction, structure_trend, structure_quality, recent_sweep, rank_label="STANDBY") if standby else []
        execution_preference = self._execution_preference(primary, standby, current_price)
        poi_classification = self._classify_poi(primary, direction, structure_quality, recent_sweep, zone_context, current_price, symbol=symbol)
        plan_narrative = self._plan_narrative(direction, scenario_type, primary, alignment["sources"], expected_path, execution_preference)

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
                "invalidation_level": primary.get("stop_loss"),
                "target_liquidity": primary.get("target_liquidity") or primary.get("target_price"),
                "planner_confidence": planner_score,
                "planner_grade": self._grade(planner_score),
                "max_pending_orders_allowed": min(self.default_pending_slots, 2 if standby else 1),
                "plan_reason": "session plan ready",
                "notes": planner_notes,
                "expected_path": expected_path,
                "execution_preference": execution_preference,
                "primary_rationale": primary_rationale,
                "standby_rationale": standby_rationale,
                "plan_narrative": plan_narrative,
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
        current_price: float,
    ) -> Dict[str, Any]:
        fallback = deepcopy(base)
        authority = self._resolve_authority(
            daily_bias=daily_bias,
            macro=macro,
            market_structure=market_structure,
            recent_sweep=(liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {},
            zone_context=zone_context,
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

        scenario_type = str(primary.get("setup_type") or "DAY_MAP_FALLBACK")
        scenario_id = self._scenario_id(symbol, direction, scenario_type, session_label, now)
        plan_id = f"PLAN::{scenario_id}"
        expected_path = self._expected_path(direction, primary, liquidity, dealing_range, current_price)
        primary_rationale = self._candidate_rationale(primary, direction, str(market_structure.get("trend") or "RANGING"), str(market_structure.get("structure_quality") or "WEAK"), (liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {}, rank_label="PRIMARY")
        standby_rationale = self._candidate_rationale(standby, direction, str(market_structure.get("trend") or "RANGING"), str(market_structure.get("structure_quality") or "WEAK"), (liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {}, rank_label="STANDBY") if standby else []
        execution_preference = self._execution_preference(primary, standby, current_price)
        poi_classification = self._classify_poi(primary, direction, str(market_structure.get("structure_quality") or "WEAK"), (liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {}, zone_context, current_price, symbol=symbol)
        plan_narrative = self._plan_narrative(direction, scenario_type, primary, authority["sources"], expected_path, execution_preference)

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
                "invalidation_level": primary.get("stop_loss"),
                "target_liquidity": primary.get("target_liquidity") or primary.get("target_price"),
                "planner_confidence": planner_score,
                "planner_grade": self._grade(planner_score),
                "max_pending_orders_allowed": min(self.default_pending_slots, 2 if standby else 1),
                "plan_reason": "fallback day map ready",
                "notes": planner_notes,
                "expected_path": expected_path,
                "execution_preference": execution_preference,
                "primary_rationale": primary_rationale,
                "standby_rationale": standby_rationale,
                "plan_narrative": plan_narrative,
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
    ) -> Dict[str, Any]:
        dirs = {
            "daily_bias": self._bias_to_direction(daily_bias.get("bias")),
            "macro": self._macro_to_direction(macro.get("bias") if isinstance(macro, dict) else None),
            "structure": self._trend_to_direction(str((market_structure or {}).get("trend") or "")),
        }
        counts = {"BUY": 0, "SELL": 0}
        sources = {"BUY": [], "SELL": []}
        for src, direction in dirs.items():
            if direction in counts:
                counts[direction] += 1
                sources[direction].append(src)
        if counts["BUY"] == counts["SELL"] and counts["BUY"] > 0:
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

    def _validated_standby(self, primary: Dict[str, Any], standby: Dict[str, Any] | None, *, symbol: str) -> Dict[str, Any] | None:
        if not isinstance(standby, dict) or not standby:
            return None
        primary_entry = self._f(primary.get("entry_price"), 0.0)
        standby_entry = self._f(standby.get("entry_price"), 0.0)
        if primary_entry > 0 and standby_entry > 0:
            distance_points = abs(self._price_to_points(standby_entry - primary_entry, symbol=symbol))
            if distance_points < self.standby_min_distance_points:
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
            high_ref = (liquidity.get("previous_day_levels") or {}).get("high") if isinstance(liquidity, dict) else None
            return f"Premium-to-discount sell path: reject after {ref} sweep, hold below {primary.get('stop_loss')} and target {target or dealing_range.get('midpoint') or current_price}."
        low_ref = (liquidity.get("previous_day_levels") or {}).get("low") if isinstance(liquidity, dict) else None
        return f"Discount-to-premium buy path: react after {ref} sweep, hold above {primary.get('stop_loss')} and target {target or dealing_range.get('midpoint') or current_price}."

    def _execution_preference(self, primary: Dict[str, Any], standby: Dict[str, Any] | None, current_price: float) -> str:
        trigger_state = str(primary.get("trigger_state") or "").upper()
        setup_state = str(primary.get("setup_state") or "").upper()
        entry_price = self._f(primary.get("entry_price"), 0.0)
        poi_classification = str(primary.get("poi_classification") or "").upper()
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
