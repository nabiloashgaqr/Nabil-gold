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


class SessionPlannerService:
    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        cfg = (self.config.get("session_planner") or {}) if isinstance(self.config, dict) else {}
        self.enabled = bool(cfg.get("enabled", True))
        self.min_plan_score = float(cfg.get("min_plan_score", 62) or 62)
        self.min_primary_dominance = float(cfg.get("min_primary_dominance", 50) or 50)
        self.min_return_probability = float(cfg.get("min_return_probability", 42) or 42)
        self.require_session_quality = str(cfg.get("require_session_quality", "HIGH") or "HIGH").upper()
        self.allow_caution_news = bool(cfg.get("allow_caution_news", True))
        self.expire_after_hours = float(cfg.get("expire_after_hours", 8) or 8)
        self.default_pending_slots = int(cfg.get("default_pending_slots", 2) or 2)
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

        now = datetime.now(timezone.utc)
        session_label = str(session.get("current_session") or session.get("session") or "Unknown Session")
        session_quality = str(session.get("session_quality") or session.get("quality") or "LOW").upper()

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
            base["plan_reason"] = "no structured setup candidates available"
            return base

        primary = next((c for c in candidates if str(c.get("selection_role") or "").upper() == "PRIMARY"), None)
        standby = next((c for c in candidates if str(c.get("selection_role") or "").upper() == "STANDBY"), None)
        if not primary:
            primary = candidates[0]

        primary_dom = self._f(primary.get("thesis_dominance_score"), 0.0)
        primary_rp = self._f(primary.get("return_probability_score"), 0.0)
        if primary_dom < self.min_primary_dominance or primary_rp < self.min_return_probability:
            base["plan_reason"] = (
                f"primary thesis too weak for planning (dominance {primary_dom:.1f}, return probability {primary_rp:.1f})"
            )
            return base

        direction = str(primary.get("direction") or "").upper()
        if direction not in {"BUY", "SELL"}:
            base["plan_reason"] = "primary candidate has no directional thesis"
            return base

        planner_score, planner_notes = self._planner_score(
            direction=direction,
            primary=primary,
            standby=standby,
            session=session,
            daily_bias=daily_bias,
            macro=macro if isinstance(macro, dict) else {},
            news=news,
        )
        if planner_score < self.min_plan_score:
            base["plan_reason"] = f"planner score {planner_score:.1f} below {self.min_plan_score:.1f}"
            base["notes"] = planner_notes
            return base

        scenario_type = str(primary.get("setup_type") or "SCENARIO")
        scenario_id = self._scenario_id(symbol, direction, scenario_type, session_label, now)
        plan_id = f"PLAN::{scenario_id}"
        base.update(
            {
                "plan_ready": True,
                "plan_status": "READY",
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
    ) -> tuple[float, List[str]]:
        score = 0.0
        notes: List[str] = []

        dominance = self._f(primary.get("thesis_dominance_score"), 0.0)
        return_prob = self._f(primary.get("return_probability_score"), 0.0)
        quality_score = self._f(primary.get("quality_score"), self._f((primary.get("setup_quality") or {}).get("score"), 0.0))
        trigger_score = self._f(primary.get("trigger_score"), 0.0)

        score += dominance * 0.35
        score += return_prob * 0.25
        score += quality_score * 0.20
        score += min(trigger_score, 100.0) * 0.10

        if str(primary.get("selection_role") or "").upper() == "PRIMARY":
            score += 4.0
            notes.append("primary thesis selected")
        if standby:
            score += 2.0
            notes.append("standby thesis available")

        setup_state = str(primary.get("setup_state") or "").upper()
        if setup_state == "ENTRY_ARMED":
            score += 6.0
            notes.append("price already near primary POI")
        elif setup_state == "POI_MARKED":
            score += 3.0
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

    @staticmethod
    def _f(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
