"""Open Trades Manager Agent.

يتابع الصفقات المفتوحة في Supabase/JSON، يحسب الربح والخسارة، يرسل تحديثات
تليجرام عند الاقتراب من الهدف أو تحقق الأهداف/الوقف/التعادل، ويمنع تكرار
الرسائل عبر حقل ``updates_sent``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from services.pending_governor import PendingGovernor
from services.scenario_governor import ScenarioGovernor
from utils.helpers import calculate_pips, canonical_session_label, load_config
from utils import trading_rules as _tr
from utils.instruments import points_to_price


class OpenTradesManager(BaseAgent):
    """Evaluate and update open trades for the stateless GitHub Actions runner."""

    name = "open_trades_manager"
    OPEN_STATUSES = {"OPEN", "PARTIAL", "TP1_HIT"}
    # THESIS_EXIT is the automatic close: the system decided the thesis the
    # trade was opened on no longer holds. MANUAL_CLOSE is retained because a
    # human close (scripts/run_close_trade_now.py) is a genuinely different
    # event, and because rows written before the rename still carry it.
    CLOSED_STATUSES = {"TP2_HIT", "SL_HIT", "BE_HIT", "EXPIRED", "THESIS_EXIT", "MANUAL_CLOSE"}
    # Events that must NEVER trigger a Telegram notification because they are
    # internal/system signals not related to actual trade state changes.
    SILENT_EVENTS = {"PRICE_SANITY_FAILED"}
    # Telegram notifications are intentionally restricted to real trade-state
    # changes. Informational markers such as NEAR_TP1 / LONG_RUNNING /
    # EXIT_WARNING are still persisted in updates_sent to avoid repeated
    # internal triggers, but they do not send Telegram messages. This matches
    # the production rule: "send only when something actually changed".
    NOTIFIABLE_EVENTS = {
        "ORDER_FILLED",
        "NEWS_HOLD",
        "PENDING_CANCELLED",
        "MOVE_SL_TO_BE",
        "TRAILING_SL_UPDATED",
        "THESIS_SCALE_OUT",
        "TP1_HIT",
        "TP2_HIT",
        "SL_HIT",
        "TRAILING_SL_HIT",
        "BE_HIT",
        "EXPIRED",
        "THESIS_EXIT",
        "MANUAL_CLOSE",
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        super().__init__(config or load_config())
        self.management = self.config.get("trade_management", {})
        self.near_tp1_progress = float(self.management.get("near_tp1_progress", 0.80))
        self.time_warning_hours = float(self.management.get("time_warning_hours", 4))
        self.expire_after_hours = float(self.management.get("expire_after_hours", 8))
        # When True, a time-expired trade that is in profit AND already protected
        # (stop moved to entry / breakeven or better) is NOT force-closed; its
        # trailing/breakeven stop is left to manage the exit instead.
        self.keep_protected_winners_open = bool(self.management.get("keep_protected_winners_open", True))
        self.auto_be = bool(self.management.get("auto_move_sl_to_entry_after_tp1", True))
        # Minimum travel, in R, before touching TP1 is allowed to arm the
        # breakeven stop. Measured in R rather than points so it holds across
        # instruments and stop widths.
        self.min_breakeven_rr = float(self.management.get("min_breakeven_rr", 0.5) or 0.0)
        # Fraction of the position actually closed when TP1 is reached.
        #
        # `partial_close_at_tp1: true` / `partial_close_percentage: 50` have
        # been in config.json from the start, and until now nothing read the
        # percentage. TP1 set a `partial_close` label and the position kept
        # running at full size, so TP2 settled 100% at the TP2 price.
        #
        # On 2026-07-31 trade d917b1d5 reported "+456.1 pts" at TP2. The
        # account had booked half at 4051.98 and half at 4029.17: +342.1. The
        # card overstated the result by 114 points, and the scoreboard, the
        # weekly report and the learning service all consumed the inflated
        # number -- so every two-target trade taught the system a payoff it
        # never received.
        self.partial_close_at_tp1 = bool(self.management.get("partial_close_at_tp1", False))
        self.partial_close_fraction = min(
            max(float(self.management.get("partial_close_percentage", 0) or 0.0) / 100.0, 0.0), 1.0
        )

        # Trailing stop + breakeven: read from trade_management first,
        # then instruments (per-symbol override), then trailing_stop (legacy).
        # Priority: trade_management > instruments > trailing_stop > defaults.
        tm = self.management  # trade_management
        ts_config = self.config.get("trailing_stop", {})  # legacy section

        # 2026-08-07 audit: trailing parameters come from the single source
        # of truth (utils.trading_rules.trailing_params).
        _trail = _tr.trailing_params(self.config)
        self.trailing_enabled = bool(_trail["enabled"])
        self.trailing_distance = float(_trail["distance_points"])
        self.trailing_step = float(_trail["step_points"])
        self.trailing_min_profit_lock = float(ts_config.get("min_profit_lock", 0.0))
        self.early_breakeven_points = float(
            tm.get("early_breakeven_points", ts_config.get("early_breakeven_points", 200.0))
        )

        # Fixed-risk mode: track scale-in info
        oe = self.config.get("order_execution", {}) or {}
        self.entry_style = str(oe.get("entry_style", "market")).lower()
        self.fr = oe.get("fixed_risk", {}) or {}

        # Hybrid entry: auto-convert stale PENDING orders to MARKET after N cycles.
        oe = self.config.get("order_execution", {}) or {}
        self.entry_style = str(oe.get("entry_style", "market")).lower()
        self.pending_order_max_cycles = int(oe.get("pending_order_max_cycles", 6) or 6)
        self.pending_expire_after_hours = float(oe.get("pending_expire_after_hours", 24) or 24)
        pnh = (oe.get("pending_news_hold", {}) or {}) if isinstance(oe, dict) else {}
        self.pending_news_hold_enabled = bool(pnh.get("enabled", True))
        _reactivation_delay = pnh.get("reactivation_delay_minutes", 3)
        _limit_drift = pnh.get("limit_max_drift_points", 30)
        _stop_drift = pnh.get("stop_max_drift_points", 20)
        self.pending_news_reactivation_delay_minutes = float(3 if _reactivation_delay is None else _reactivation_delay)
        self.pending_news_limit_max_drift_points = float(30 if _limit_drift is None else _limit_drift)
        self.pending_news_stop_max_drift_points = float(20 if _stop_drift is None else _stop_drift)
        self.pending_news_require_rr_recheck = bool(pnh.get("require_rr_recheck", True))
        self.pending_news_require_spread_recheck = bool(pnh.get("require_spread_recheck", True))
        self.pending_news_cancel_if_drift_exceeds = bool(pnh.get("cancel_if_drift_exceeds", True))
        # Zone-touch activation.
        #
        # A planner map publishes an entry ZONE, then waits for one price
        # inside it. On 2026-07-30 a BUY zone 4054.49-4062.05 was touched at
        # 4060.40 -- inside the zone, 21 points above the reference entry --
        # and the order never filled. Price then ran to 4079.88, through TP1.
        #
        # Chasing that at market was not the answer: the stop stays where the
        # map put it, so entering 80 points higher inflates risk from 185 to
        # 265 points and collapses RR from 1.90 to 1.02, below the configured
        # floor. The near-miss path refuses it for exactly that reason.
        #
        # Filling at the zone EDGE and carrying the stop the same distance
        # keeps the planned risk intact: 185 points either way, RR 1.69.
        # The trade the map described is taken, at the worst price inside the
        # area the map itself drew.
        zta = (oe.get("zone_touch_activation", {}) or {}) if isinstance(oe, dict) else {}
        self.zone_touch_enabled = bool(zta.get("enabled", True))
        self.zone_touch_require_exit_in_favour = bool(zta.get("require_exit_in_favour", True))
        self.zone_touch_preserve_planned_risk = bool(zta.get("preserve_planned_risk", True))
        self.zone_touch_min_remaining_rr = float(zta.get("min_remaining_rr", 1.5) or 1.5)
        # Do not board a move that has already happened. Measured against the
        # distance from the ZONE EDGE to TP1: once price has covered most of
        # that, the reward left no longer justifies the mapped stop, and the
        # RR test alone will not catch it because TP2 may still be far away.
        # Defaults to the near-miss ceiling when zone-touch does not set its
        # own, so an operator who tightened one chase-guard is not silently
        # bypassed by the other.
        _zt_progress = zta.get("max_target_progress_pct")
        if _zt_progress is None:
            _zt_progress = (oe.get("near_miss_execution", {}) or {}).get("max_target_progress_pct", 60)
        self.zone_touch_max_target_progress_pct = float(_zt_progress or 60)
        self.zone_touch_require_planner_context = bool(zta.get("require_planner_context", True))
        # Give the mapped price its chance first.
        #
        # The reference entry is the price the plan actually wants. Only once
        # price has been inside the area for this long without filling is the
        # order treated as sitting too deep. One analysis cycle is 5 minutes,
        # so the default waits two of them.
        self.zone_touch_grace_minutes = float(zta.get("grace_minutes", 10) or 10)
        # Once price has left the zone in favour, the edge is a fact of the
        # past: the fill is judged by where the zone was, not by where price
        # happens to sit on the cycle that notices. Without this, a pullback
        # back toward the edge would cancel an activation that had already
        # earned itself.
        self.zone_touch_allow_after_return = bool(zta.get("allow_after_return", True))
        nme = (oe.get("near_miss_execution", {}) or {}) if isinstance(oe, dict) else {}
        self.near_miss_enabled = bool(nme.get("enabled", True))
        self.near_miss_min_halo_points = float(nme.get("min_halo_points", 12) or 12)
        self.near_miss_max_halo_points = float(nme.get("max_halo_points", 25) or 25)
        self.near_miss_zone_width_multiplier = float(nme.get("zone_width_multiplier", 0.75) or 0.75)
        self.near_miss_recent_range_multiplier = float(nme.get("recent_range_multiplier", 0.18) or 0.18)
        self.near_miss_min_confirmation_points = float(nme.get("min_confirmation_points", 12) or 12)
        self.near_miss_max_target_progress_pct = float(nme.get("max_target_progress_pct", 30) or 30)
        self.near_miss_min_remaining_rr = float(nme.get("min_remaining_rr", 1.8) or 1.8)
        self.near_miss_require_planner_context = bool(nme.get("require_planner_context", True))
        # Carry the planned risk to the converted fill.
        #
        # A near-miss conversion re-prices the ENTRY to the market but left
        # the stop where the plan put it, so the distance between them grew
        # by however far price had run. 2026-08-03, trade 2f72579f: a SELL
        # planned at 4037.48 with a 400-point stop at 4077.48 was converted
        # at 4031.76 -- the entry moved 57 points against the plan and the
        # risk silently became 457 points. Nothing asked permission, and the
        # RR test passed because 1.84 still cleared its 1.8 bar.
        #
        # zone_touch_activation already solves this with
        # `preserve_planned_risk`: move the stop by the same distance the
        # entry moved, so the trade risks what it was authorised to risk.
        # Same principle, same default.
        self.near_miss_preserve_planned_risk = bool(nme.get("preserve_planned_risk", True))
        pf = (self.config.get("pending_freshness", {}) or {}) if isinstance(self.config, dict) else {}
        self.pending_freshness_enabled = bool(pf.get("enabled", True))
        self.pending_freshness_aging_after_hours = float(pf.get("aging_after_hours", 2) or 2)
        self.pending_freshness_stale_after_hours = float(pf.get("stale_after_hours", 6) or 6)
        self.pending_freshness_stale_after_excursion_points = float(pf.get("stale_after_excursion_points", 250) or 250)
        self.pending_freshness_stale_after_target_progress_pct = float(pf.get("stale_after_target_progress_pct", 60) or 60)
        self.pending_freshness_revalidation_on_session_change = bool(pf.get("mark_revalidation_required_on_session_change", True))
        ptr = (pf.get("touch_revalidation") or {}) if isinstance(pf, dict) else {}
        self.pending_touch_revalidation_enabled = bool(ptr.get("enabled", True))
        # Minutes an order must exist before a candle extreme (rather than the
        # live price) may fill it. Defaults to one 15m bar, the primary frame.
        # `or` would swallow an explicit 0, leaving no way to switch the guard
        # off; read the value directly and only fall back when it is absent.
        _min_age = ptr.get("min_age_minutes_for_candle_fill", 15)
        try:
            self.pending_touch_min_age_minutes = float(_min_age)
        except (TypeError, ValueError):
            self.pending_touch_min_age_minutes = 15.0
        # Candle timestamps govern touch detection (operator directive,
        # 2026-08-05). The manager used to receive "the latest candle" with no
        # timestamp and trust its extremes on an age proxy alone -- so a stale
        # or mislabeled bar could fill orders and trip stops at prices the
        # market never printed (TRADE_..._2e09b05c activated at 4090.25 while
        # every displayed price stayed above 4093). Two guards now:
        #   * a candle older than max_candle_age_minutes is a price-reading
        #     failure: its extremes are collapsed to the live price, and
        #     pending orders wait for a fresh bar before activating;
        #   * a fresh candle may only fill an order with action from a window
        #     that started at or after the order existed (a straddling bar is
        #     judged by the live price alone).
        _bar_tf = str((self.config.get("data_source") or {}).get("base_timeframe", "5m") or "5m")
        _bar_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}.get(_bar_tf, 5)
        self.pending_touch_bar_minutes = float(_bar_minutes)
        _max_age = ptr.get("max_candle_age_minutes", _bar_minutes * 2)
        try:
            self.pending_touch_max_candle_age_minutes = float(_max_age)
        except (TypeError, ValueError):
            self.pending_touch_max_candle_age_minutes = float(_bar_minutes * 2)
        self.pending_touch_revalidation_min_confirmation_points = float(ptr.get("min_confirmation_points", 15) or 15)
        self.pending_touch_revalidation_limit_max_drift_points = float(ptr.get("limit_max_drift_points", 40) or 40)
        self.pending_touch_revalidation_stop_max_drift_points = float(ptr.get("stop_max_drift_points", 25) or 25)
        self.pending_touch_revalidation_cancel_on_failed = bool(ptr.get("cancel_on_failed_revalidation", True))
        self.profile_overrides = (self.management.get("profiles", {}) or {}) if isinstance(self.management, dict) else {}
        thesis_exit = (self.management.get("thesis_exit", {}) or {}) if isinstance(self.management, dict) else {}
        self.thesis_exit_enabled = bool(thesis_exit.get("enabled", True))
        self.thesis_exit_countertrend_hold_minutes = float(thesis_exit.get("countertrend_hold_minutes", 20) or 20)
        self.thesis_exit_min_progress_pct = float(thesis_exit.get("min_progress_pct", 18) or 18)
        self.thesis_exit_min_mfe_points = float(thesis_exit.get("min_mfe_points", 35) or 35)
        self.thesis_exit_reclaim_points = float(thesis_exit.get("reclaim_points", 12) or 12)
        self.thesis_exit_opposing_poi_enabled = bool(thesis_exit.get("opposing_poi_enabled", True))
        self.thesis_exit_opposing_poi_buffer_points = float(thesis_exit.get("opposing_poi_buffer_points", 18) or 18)
        self.thesis_exit_opposing_poi_reclaim_points = float(thesis_exit.get("opposing_poi_reclaim_points", 12) or 12)
        # Agent vote on the candle-triggered exit.
        #
        # The candle rule (_continuation_trigger_against_trade) is a single
        # source of evidence, and two live trades proved it cannot carry the
        # decision alone: both produced a byte-identical trigger, yet
        # a4911dee was a genuine regime change (the exit saved 314 points)
        # and 5f383b5c was noise (the exit cost 107, and the planner
        # republished the very same zone as an A+ map hours later).
        #
        # No threshold separates them, because the defect is the input. The
        # same cycle already computes a six-agent read; this asks for it.
        agent_vote = (thesis_exit.get("agent_vote", {}) or {}) if isinstance(thesis_exit, dict) else {}
        self.thesis_exit_agent_vote_enabled = bool(agent_vote.get("enabled", True))
        self.thesis_exit_agent_min_confidence = float(
            agent_vote.get("agent_min_confidence")
            or (self.config.get("signal_requirements", {}) or {}).get("agent_min_confidence", 70)
            or 70
        )
        self.thesis_exit_min_defenders = int(agent_vote.get("min_defenders_to_hold", 2) or 2)
        self.thesis_exit_min_opponents = int(agent_vote.get("min_opponents_to_exit", 2) or 2)
        self.thesis_exit_silent_action = str(agent_vote.get("silent_action", "SCALE_OUT")).upper()
        self.thesis_exit_silent_scale_fraction = float(agent_vote.get("silent_scale_fraction", 0.5) or 0.5)
        # When the agent book turns against a winning trade but the candle has
        # not broken, the exit correctly holds -- a thesis should not die on an
        # opinion the price action has not confirmed. But holding used to mean
        # holding at the full trailing gap: a +191 pt runner protected only
        # 41 pts, leaving 150 on the table while five qualified agents read the
        # other way.
        #
        # Tighten the trail instead of closing the trade. The stop still only
        # moves in the profitable direction, so this can never add risk; it
        # only decides how much of an existing gain is defended.
        self.thesis_exit_tighten_trail_on_reversal = bool(
            agent_vote.get("tighten_trail_on_reversal", True)
        )
        self.thesis_exit_reversal_trail_points = float(
            agent_vote.get("reversal_trail_distance_points", 60) or 60
        )
        self.pending_governor = PendingGovernor(self.config)
        self.scenario_governor = ScenarioGovernor(self.config)

    def _trade_management_profile(self, trade: Dict[str, Any]) -> str:
        snapshot = trade.get("signal_snapshot") or {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        risk = snapshot.get("risk") or {}
        if isinstance(risk, dict) and risk.get("management_profile"):
            return str(risk.get("management_profile"))
        signal = snapshot.get("signal") or {}
        if isinstance(signal, dict) and signal.get("management_profile"):
            return str(signal.get("management_profile"))
        setup_type = str(snapshot.get("setup_type") or (snapshot.get("setup_context") or {}).get("setup_type") or "").upper()
        if setup_type in {"LIQUIDITY_REVERSAL", "REVERSAL_ATTEMPT"}:
            return "reversal_profile"
        if setup_type in {"ORDER_BLOCK_PULLBACK", "STRUCTURE_CONTINUATION", "TREND_CONTINUATION", "PULLBACK_ENTRY"}:
            return "continuation_profile"
        if setup_type in {"RANGE_FADE", "SMC_CONTEXT", "MIXED_ALIGNMENT"}:
            return "range_profile"
        return "default_profile"

    def _management_params(self, trade: Dict[str, Any], symbol: str | None = None) -> Dict[str, Any]:
        profile = self._trade_management_profile(trade)
        symbol = symbol or str(trade.get("symbol") or self.config.get("symbol", "XAU/USD"))
        params = {
            "profile": profile,
            "near_tp1_progress": self.near_tp1_progress,
            "time_warning_hours": self.time_warning_hours,
            "expire_after_hours": self.expire_after_hours,
            "keep_protected_winners_open": self.keep_protected_winners_open,
            "auto_be": self.auto_be,
            "partial_close_at_tp1": self.partial_close_at_tp1,
            "partial_close_fraction": self.partial_close_fraction,
            "trailing_enabled": self.trailing_enabled,
            "trailing_distance_points": self.trailing_distance,
            "trailing_step_points": self.trailing_step,
            "trailing_min_profit_lock_points": self.trailing_min_profit_lock,
            "early_breakeven_points": self.early_breakeven_points,
            "min_breakeven_rr": self.min_breakeven_rr,
        }
        # Precedence: an explicit root setting beats a profile default.
        #
        # The profile used to be applied unconditionally. Because config.json's
        # default_profile repeats every root key verbatim, the root values were
        # dead: driving trailing_distance_points from 60 to 400 produced
        # byte-identical exits, since 150 was reinstated on every evaluation.
        # That is the dead-gate pattern in config form -- a real setting that
        # nothing reads -- and it makes any attempt to measure or tune exits
        # report pure noise.
        #
        # The rule is deliberately narrow: the root outranks default_profile
        # and nothing else.
        #
        # default_profile means "the ordinary case", which is precisely what
        # the root already expresses, so a duplicate there can only shadow.
        # The specialised profiles are different in kind: reversal_profile
        # setting early_breakeven_points to 100 against a root of 150 is a
        # deliberate statement that reversals deserve earlier protection, and
        # a broader presence rule would silently delete that behaviour --
        # which it did, until two existing tests caught it.
        override = self.profile_overrides.get(profile) or {}
        root = self.management if isinstance(self.management, dict) else {}
        # Root keys that carry a different name from the parameter they set.
        root_aliases = {
            "trailing_enabled": "trailing_stop_enabled",
            "auto_be": "auto_move_sl_to_entry_after_tp1",
            "trailing_min_profit_lock_points": "trailing_min_profit_lock",
        }
        root_outranks = profile == "default_profile"
        for key in list(params.keys()):
            if key not in override:
                continue
            if root_outranks and (key in root or root_aliases.get(key) in root):
                continue
            params[key] = override[key]
        # 2026-08-07: trailing (gap/step/enabled/early-BE) is decided ONCE in
        # utils.trading_rules with precedence canonical > profile > legacy.
        _trail = _tr.trailing_params(self.config, profile)
        params["trailing_enabled"] = bool(_trail["enabled"])
        params["trailing_distance_points"] = float(_trail["distance_points"])
        params["trailing_step_points"] = float(_trail["step_points"])
        params["early_breakeven_points"] = float(_trail["early_breakeven_points"])
        params["symbol"] = symbol
        return params

    def _trade_snapshot(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = trade.get("signal_snapshot") or {}
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except Exception:
                snapshot = {}
        return snapshot if isinstance(snapshot, dict) else {}

    def _execution_leg_label_from_context(self, role: str, setup: Dict[str, Any], plan: Dict[str, Any], direction: str) -> str | None:
        direct = str(setup.get("execution_leg_label") or "").strip()
        if direct:
            return direct
        manual_plan = (plan.get("manual_plan") or {}) if isinstance(plan, dict) else {}
        side_word = "BUY" if direction == "BUY" else "SELL" if direction == "SELL" else "TRADE"
        main_label = str(manual_plan.get("main_area_label") or f"MAIN {side_word} AREA")
        add_label = str(manual_plan.get("add_area_label") or f"ADD {side_word} AREA")
        mapping = {
            "PRIMARY": main_label,
            "STANDBY": add_label,
            "STARTER": f"STARTER inside {main_label}",
            "ADD_ON": f"ADD-ON from {add_label}",
        }
        return mapping.get(str(role or "").upper())

    def _plan_execution_context(
        self,
        trade: Dict[str, Any],
        evaluation: Dict[str, Any],
        open_trades: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        snapshot = self._trade_snapshot(trade)
        setup = (snapshot.get("setup_context") or {}) if isinstance(snapshot, dict) else {}
        setup = setup if isinstance(setup, dict) else {}
        plan = (snapshot.get("session_plan") or {}) if isinstance(snapshot, dict) else {}
        plan = plan if isinstance(plan, dict) else {}
        role = str(setup.get("pending_plan_role") or setup.get("selection_role") or "").upper()
        direction = str(trade.get("type") or trade.get("side") or setup.get("direction") or plan.get("session_bias") or "").upper()
        scenario_id = str(plan.get("scenario_id") or setup.get("scenario_id") or "").strip()
        leg_label = self._execution_leg_label_from_context(role, setup, plan, direction)
        if not scenario_id and not role and not leg_label:
            return {}

        sibling_roles: List[str] = []
        pending_sibling_roles: List[str] = []
        live_sibling_roles: List[str] = []
        for sibling in (open_trades or []):
            if str(sibling.get("id") or "") == str(trade.get("id") or ""):
                continue
            sibling_snapshot = self._trade_snapshot(sibling)
            sibling_setup = sibling_snapshot.get("setup_context") or {}
            sibling_setup = sibling_setup if isinstance(sibling_setup, dict) else {}
            sibling_plan = sibling_snapshot.get("session_plan") or {}
            sibling_plan = sibling_plan if isinstance(sibling_plan, dict) else {}
            sibling_scenario = str(sibling_plan.get("scenario_id") or sibling_setup.get("scenario_id") or "").strip()
            if not scenario_id or sibling_scenario != scenario_id:
                continue
            sibling_role = str(sibling_setup.get("pending_plan_role") or sibling_setup.get("selection_role") or "").upper()
            sibling_status = str(sibling.get("status") or "").upper()
            if sibling_role:
                sibling_roles.append(sibling_role)
                if sibling_status == "PENDING":
                    pending_sibling_roles.append(sibling_role)
                if sibling_status in self.OPEN_STATUSES:
                    live_sibling_roles.append(sibling_role)

        events = set(evaluation.get("events") or [])
        result = str((evaluation.get("updates") or {}).get("result") or "").upper()
        has_secondary_defined = bool(plan.get("standby_poi")) or str(plan.get("execution_preference") or "").upper() == "SPLIT_EXECUTION_WATCH"
        story = None
        if "ORDER_FILLED" in events:
            if role == "PRIMARY":
                story = "Main area filled."
                if pending_sibling_roles:
                    story += " Secondary area is no longer needed and will be cancelled."
            elif role == "STANDBY":
                story = "Add area activated after price reached the deeper backup zone."
            elif role == "STARTER":
                story = "Starter leg activated inside the main mapped area."
            elif role == "ADD_ON":
                story = "Add-on leg activated from the deeper mapped area."
        elif "PENDING_CANCELLED" in events:
            reasons = " | ".join(str(x) for x in ((evaluation.get("updates") or {}).get("reasons") or []))
            if role in {"STANDBY", "ADD_ON"}:
                story = "Add area cancelled — mapped conditions are no longer valid."
                if "Scenario governor" in reasons:
                    story = "Add area cancelled because the map reprioritized another family leg."
            elif role in {"PRIMARY", "STARTER"}:
                # The story has to follow the reason that actually fired.
                #
                # 2026-07-31: order 6e31ddf6 was cancelled with the reason
                # "market covered 61% of target path without fill", and the
                # card announced "the day map lost validity". The map had not
                # lost validity -- it was right, price reached the level it
                # predicted and the live trade took TP2 there. The order was
                # simply never reachable. Telling the user the map failed on
                # the day it worked teaches him to distrust a correct map.
                lowered = reasons.lower()
                if "target path" in lowered or "without fill" in lowered:
                    story = (
                        "Main mapped execution was cancelled because the market reached "
                        "the plan's objective without ever returning to the entry."
                    )
                elif "waiting too long" in lowered or "expired" in lowered:
                    story = (
                        "Main mapped execution was cancelled because it waited past its "
                        "allowed window without activating."
                    )
                elif "moved" in lowered and "without fill" in lowered:
                    story = (
                        "Main mapped execution was cancelled because price travelled away "
                        "from the entry without filling it."
                    )
                else:
                    story = "Main mapped execution was cancelled before activation because the day map lost validity."
        elif events.intersection({"TP1_HIT", "TRAILING_SL_UPDATED", "TP2_HIT"}):
            if role == "STARTER" and has_secondary_defined and not pending_sibling_roles and not live_sibling_roles:
                story = "Starter survived — add-on is not needed right now."
            elif role == "PRIMARY" and has_secondary_defined and not pending_sibling_roles and not live_sibling_roles:
                story = "Main area is delivering — add area is not needed right now."
        elif "SL_HIT" in events and result == "LOSS":
            if role in {"PRIMARY", "STARTER"}:
                story = "Main day-map execution failed from the mapped area."
            elif role in {"STANDBY", "ADD_ON"}:
                story = "Secondary mapped execution failed from the deeper area."
        elif "BE_HIT" in events:
            if role in {"PRIMARY", "STARTER"}:
                story = "Main mapped execution did not expand; protection closed it at breakeven."
            elif role in {"STANDBY", "ADD_ON"}:
                story = "Secondary mapped execution stalled and closed at breakeven."

        return {
            "scenario_id": scenario_id or None,
            "role": role or None,
            "leg_label": leg_label,
            "pending_sibling_roles": pending_sibling_roles,
            "live_sibling_roles": live_sibling_roles,
            "story": story,
        }

    def update_trades(
        self,
        open_trades: List[Dict[str, Any]],
        current_price: float,
        database: Any | None = None,
        telegram: Any | None = None,
        now: datetime | None = None,
        candle_high: float | None = None,
        candle_low: float | None = None,
        recent_candles: List[Dict[str, Any]] | None = None,
        news_blocked: bool = False,
        news_context: Dict[str, Any] | None = None,
        market_data_source: str | None = None,
        agent_details: Dict[str, Any] | None = None,
        candle_time: str | None = None,
    ) -> List[Dict[str, Any]]:
        """Evaluate all open trades, persist updates and send Telegram events.

        ``current_price`` is the latest candle close. ``candle_high``/``candle_low``
        are optional intrabar extremes from the same update candle. When supplied,
        TP/SL/BE/fill checks use high/low so a level touched inside the 5-minute
        candle is not missed just because the candle closed back away from it.
        """
        evaluations: List[Dict[str, Any]] = []
        now = now or datetime.now(timezone.utc)
        for trade in open_trades:
            evaluation = self.evaluate_trade(
                trade,
                current_price,
                now=now,
                candle_high=candle_high,
                candle_low=candle_low,
                recent_candles=recent_candles,
                news_blocked=news_blocked,
                news_context=news_context,
                database=database,
                market_data_source=market_data_source,
                agent_details=agent_details,
                candle_time=candle_time,
            )
            evaluations.append(evaluation)
            trade_id = str(trade.get("id", ""))
            events = evaluation.get("events", []) or []
            notification_events = [event for event in events if event in self.NOTIFIABLE_EVENTS and event not in self.SILENT_EVENTS]
            evaluation["notification_events"] = notification_events
            evaluation["plan_execution_context"] = self._plan_execution_context(trade, evaluation, open_trades)

            # Send critical trade-management notifications BEFORE writing the DB
            # update. If Supabase has a transient/schema issue, the user still
            # receives the important event (SL moved / trailing moved / TP / SL)
            # instead of silently missing it because the DB write happened first.
            # Informational-only events are not sent to Telegram.
            if telegram and notification_events:
                delivered = False
                try:
                    # Send ONE combined message per trade per cycle instead of a
                    # separate message per material state change.
                    if hasattr(telegram, "send_trade_events"):
                        delivered = bool(
                            telegram.send_trade_events(
                                trade, notification_events, current_price, evaluation.get("pnl_points", 0), evaluation
                            )
                        )
                    else:  # backward-compatible fallback
                        delivered = all(
                            bool(telegram.send_trade_event(trade, event, current_price, evaluation.get("pnl_points", 0), evaluation))
                            for event in notification_events
                        )
                except Exception as exc:  # noqa: BLE001
                    self.logger.exception("Failed to send trade-management Telegram event(s) for %s: %s", trade_id, exc)
                    delivered = False
                evaluation["notification_delivered"] = delivered
                if not delivered:
                    self.logger.error(
                        "Mandatory trade update notification was not delivered for %s: %s",
                        trade_id,
                        ",".join(notification_events),
                    )

            if trade_id and database and evaluation.get("updates"):
                database.update_trade(trade_id, evaluation["updates"])
                if (
                    evaluation.get("old_status") == "PENDING"
                    and evaluation.get("new_status") == "OPEN"
                    and "ORDER_FILLED" in (evaluation.get("events") or [])
                ):
                    try:
                        family_action = self.scenario_governor.handle_activation(
                            trade,
                            database=database,
                            open_trades=open_trades,
                        )
                        if family_action.get("cancelled_ids"):
                            evaluation["scenario_governor"] = family_action
                            cancelled_ids = {str(tid) for tid in (family_action.get("cancelled_ids") or [])}
                            for sibling in open_trades:
                                if str(sibling.get("id") or "") in cancelled_ids:
                                    sibling["status"] = "CANCELLED"
                                    sibling["result"] = "CANCELLED"
                    except Exception as exc:  # noqa: BLE001
                        self.logger.warning("Scenario governor activation handling failed for %s: %s", trade_id, exc)
        return evaluations

    def evaluate_trade(
        self,
        trade: Dict[str, Any],
        current_price: float,
        now: datetime | None = None,
        candle_high: float | None = None,
        candle_low: float | None = None,
        recent_candles: List[Dict[str, Any]] | None = None,
        news_blocked: bool = False,
        news_context: Dict[str, Any] | None = None,
        database: Any | None = None,
        market_data_source: str | None = None,
        agent_details: Dict[str, Any] | None = None,
        candle_time: str | None = None,
    ) -> Dict[str, Any]:
        """Return updates/events for a single trade without external side effects.

        ``current_price`` remains the displayed/latest close. If the caller
        provides the latest candle high/low, hard level checks use those extremes:
        BUY targets use high, BUY stops use low; SELL targets use low, SELL stops
        use high. This catches TP/SL touches within a 5-minute candle.

        ``candle_time`` is that candle's own timestamp. When it is present and
        older than the freshness window, the candle is a price-reading failure:
        its extremes are collapsed to the live price for THIS evaluation, so a
        stale bar can neither fill an order nor stop one out.
        """
        now = now or datetime.now(timezone.utc)
        candle_dt = self._parse_dt(str(candle_time or "")) if candle_time else None
        if candle_dt is not None and (candle_high is not None or candle_low is not None):
            candle_age_minutes = (now - candle_dt).total_seconds() / 60.0
            if candle_age_minutes > self.pending_touch_max_candle_age_minutes:
                self.logger.warning(
                    "PRICE READ FAILURE for %s: latest candle is from %s (%.0f min old, "
                    "freshness window %.0f min). Its extremes are ignored this cycle; "
                    "levels are judged by the live price only.",
                    trade.get("id"), candle_time, candle_age_minutes,
                    self.pending_touch_max_candle_age_minutes,
                )
                candle_high = current_price
                candle_low = current_price
        trade_type = str(trade.get("type", "BUY")).upper()
        symbol = str(trade.get("symbol") or self.config.get("symbol", "XAU/USD"))
        old_status = str(trade.get("status", "OPEN")).upper()
        management = self._management_params(trade, symbol=symbol)
        entry = self._f(trade.get("entry_price"))
        stop_loss = self._f(trade.get("stop_loss"))
        tp1 = self._f(trade.get("tp1"))
        tp2 = self._f(trade.get("tp2"))
        updates_sent = self._updates_sent(trade.get("updates_sent", []))
        sl_moved_to_entry = self._bool(trade.get("sl_moved_to_entry", False))
        partial_close = self._bool(trade.get("partial_close", False))
        previous_mfe = self._f(trade.get("max_favorable_excursion"), 0.0)
        previous_mae = self._f(trade.get("max_adverse_excursion"), 0.0)
        high_price = self._f(candle_high, current_price)
        low_price = self._f(candle_low, current_price)
        recent_window_high, recent_window_low = self._recent_window_extremes(recent_candles)
        window_high, window_low = self._window_extremes_since(trade, recent_candles)
        if window_high is not None:
            high_price = max(high_price, window_high)
        if window_low is not None:
            low_price = min(low_price, window_low)
        if high_price < low_price:
            high_price, low_price = low_price, high_price

        # ── Missed-cycle sequential replay (operator directive 2026-08-05) ──
        # If the engine was down for a cycle, several bars are unexamined. They
        # must be replayed IN ORDER (raise trailing from an early high before a
        # later low is tested against it) rather than collapsed into one window.
        if old_status in self.OPEN_STATUSES:
            unexamined = self._ordered_unexamined_candles(trade, recent_candles)
            if len(unexamined) >= 2:
                seq = self._missed_candles_review(
                    trade=trade, trade_type=trade_type, entry=entry,
                    stop_loss=stop_loss, sl_moved_to_entry=sl_moved_to_entry,
                    partial_close=partial_close, tp1=tp1, tp2=tp2,
                    management=management, symbol=symbol, unexamined=unexamined,
                )
                if seq is not None:
                    return seq

        # ── PENDING (un-filled LIMIT/STOP) order handling ───────────────────
        # A pending order is NOT a live position: it has no PnL until price
        # actually touches the entry. Only then does it become OPEN. This fixes
        # phantom fills/profits where a far LIMIT was treated as already filled.
        if old_status == "PENDING":
            return self._evaluate_pending(
                trade,
                current_price,
                now,
                trade_type,
                entry,
                tp1,
                symbol,
                candle_high=high_price,
                candle_low=low_price,
                recent_candles=recent_candles,
                news_blocked=news_blocked,
                news_context=news_context,
                database=database,
                market_data_source=market_data_source,
                candle_time=candle_time,
            )

        pnl_points = calculate_pips(entry, current_price, trade_type, symbol)
        favorable_price = high_price if trade_type == "BUY" else low_price
        adverse_price = low_price if trade_type == "BUY" else high_price
        favorable_points = calculate_pips(entry, favorable_price, trade_type, symbol)
        adverse_points = calculate_pips(entry, adverse_price, trade_type, symbol)
        max_favorable_excursion = max(previous_mfe, pnl_points, favorable_points)
        max_adverse_excursion = min(previous_mae, pnl_points, adverse_points)
        management_phase = self._management_phase(old_status, sl_moved_to_entry, partial_close, pnl_points)
        exit_warning = self._exit_warning(trade_type, entry, stop_loss, tp1, current_price, pnl_points)

        # ═══ Price sanity gate ═══
        # A single corrupted data tick (provider glitch, wrong symbol, etc.)
        # must never close a trade. Skip hard-level evaluation when price is
        # clearly nonsensical relative to the trade's entry. We still update
        # tracking fields so the system knows we visited this trade.
        price_sane = not self._price_sanity_failed(current_price, entry, str(trade.get("id", "")))
        if not price_sane:
            return {
                "trade_id": trade.get("id"),
                "old_status": old_status,
                "new_status": old_status,
                "pnl_points": pnl_points,
                "events": ["PRICE_SANITY_FAILED"],
                "updates": {
                    "current_price": round(current_price, 2),
                    "current_pnl": round(pnl_points, 1),
                    "current_pnl_points": round(pnl_points, 1),
                    "max_favorable_excursion": round(max_favorable_excursion, 1),
                    "max_adverse_excursion": round(max_adverse_excursion, 1),
                    "management_phase": management_phase,
                    "last_updated": self._iso(now),
                },
            }
        new_status = old_status
        events: List[str] = []
        result: str | None = trade.get("result")
        close_price = None
        final_pnl = None
        new_stop_loss: float | None = None
        partial_realized_pnl: float | None = None
        partial_closed_fraction: float | None = None
        partial_scale_out_price: float | None = None

        if old_status not in self.OPEN_STATUSES:
            return {
                "trade_id": trade.get("id"),
                "old_status": old_status,
                "new_status": old_status,
                "pnl_points": pnl_points,
                "events": [],
                "updates": {
                    "current_price": round(current_price, 2),
                    "current_pnl": round(pnl_points, 1),
                    "current_pnl_points": round(pnl_points, 1),
                    "max_favorable_excursion": round(max(previous_mfe, pnl_points), 1),
                    "max_adverse_excursion": round(min(previous_mae, pnl_points), 1),
                    "management_phase": self._management_phase(old_status, sl_moved_to_entry, partial_close, pnl_points),
                    "last_updated": self._iso(now),
                },
            }

        def _target_touched(level: float) -> bool:
            if level <= 0:
                return False
            return high_price >= level if trade_type == "BUY" else low_price <= level

        def _stop_touched(level: float) -> bool:
            if level <= 0:
                return False
            return low_price <= level if trade_type == "BUY" else high_price >= level

        # A stop that was trailed into profit may only be executed by price
        # action that printed AFTER that stop existed.
        #
        # 2026-07-31, trade d917b1d5. One 5-minute bar carried the collapse:
        # high 4055.00, low 4035.45. The low drove the trailing stop from
        # 4063.00 down to 4050.45 -- and the SAME bar's high, 4055.00, was
        # then read as a touch of it. The card reported "Trailing Stop Hit ...
        # Exit 4050.45 ... +243.3 pts" while it also printed "Current Price:
        # 4042.05". Price never traded at 4050.45 after the stop moved there;
        # it was already 33 points below and still falling. The trade was
        # worth +327 at that moment and went on to 4029 (TP2).
        #
        # The same-cycle guard below (`active_protective_stop`) already refuses
        # to execute a stop the current cycle just created. It was not enough:
        # the cycle that MOVES the stop persists it, and the very next cycle
        # sees a stop that is now "old" and a window that still contains the
        # pre-move high. The guard has to follow the candle, not the cycle.
        #
        # `trailing_stop_source_time` records the newest bar that fed the
        # trailing calculation. Bars at or before it are the bars the stop was
        # derived from; OHLC cannot prove the high came after the low inside
        # any one of them, which is the same reasoning the same-cycle rule is
        # already built on. Current price always counts -- "now" is by
        # definition after the stop was set -- so a genuine move through the
        # stop still closes the trade at the stop, in the same cycle.
        #
        # Rows written before this field existed have no baseline and keep the
        # previous behaviour exactly.
        stop_source_time = self._parse_dt(str(trade.get("trailing_stop_source_time") or ""))
        adverse_after_stop = self._adverse_extreme_after(recent_candles, trade_type, stop_source_time)

        def _trailed_stop_touched(level: float) -> bool:
            if level <= 0:
                return False
            if stop_source_time is None:
                return _stop_touched(level)
            if (current_price <= level) if trade_type == "BUY" else (current_price >= level):
                return True
            if adverse_after_stop is None:
                return False
            return adverse_after_stop <= level if trade_type == "BUY" else adverse_after_stop >= level

        def _breakeven_touched() -> bool:
            return low_price <= entry if trade_type == "BUY" else high_price >= entry

        tp2_touched = _target_touched(tp2)
        tp1_touched = _target_touched(tp1)
        sl_touched = _stop_touched(stop_loss)
        be_touched = _breakeven_touched()
        hours_open = self._hours_open(trade, now)
        thesis_exit = self._thesis_exit_review(
            trade,
            trade_type=trade_type,
            symbol=symbol,
            current_price=current_price,
            recent_candles=recent_candles,
            hours_open=hours_open,
            pnl_points=pnl_points,
            max_favorable_excursion=max_favorable_excursion,
            tp1=tp1,
            entry=entry,
            partial_close=partial_close,
            agent_details=agent_details,
        )

        # A winning trade the agents have turned against defends more of its
        # gain. The exit itself still holds -- see _thesis_exit_review -- but
        # the trailing gap narrows so the profit is not handed back while the
        # book argues the other way.
        #
        # Deliberately narrow: it needs an actual agent verdict against the
        # trade, an open profit, and a stop already at or beyond breakeven, so
        # it can only ever tighten a position that is already safe. The stop
        # moves in the profitable direction only, so no risk is added.
        effective_trail_points = float(management["trailing_distance_points"])
        reversal_trail_active = False
        if (
            self.thesis_exit_tighten_trail_on_reversal
            and pnl_points > 0
            and sl_moved_to_entry
            and self.thesis_exit_reversal_trail_points > 0
            and self.thesis_exit_reversal_trail_points < effective_trail_points
        ):
            _vote = self._agent_exit_vote(agent_details, trade_type)
            if _vote.get("available") and str(_vote.get("verdict")) == "CONFIRM":
                effective_trail_points = self.thesis_exit_reversal_trail_points
                reversal_trail_active = True
                self.logger.info(
                    "Agent book turned against %s while +%.0f pts open; trailing gap "
                    "tightened %.0f -> %.0f pts to defend the gain (opponents: %s)",
                    trade.get("id"), pnl_points,
                    float(management["trailing_distance_points"]),
                    effective_trail_points,
                    ", ".join(_vote.get("opponents") or []),
                )

        # Informational age/risk markers are still recorded even if the same
        # cycle also expires the trade.
        if old_status == "OPEN" and hours_open >= float(management["time_warning_hours"]) and "LONG_RUNNING" not in updates_sent:
            events.append("LONG_RUNNING")
        if exit_warning and "EXIT_WARNING" not in updates_sent:
            events.append("EXIT_WARNING")

        # Time-expiry is a lifecycle rule and must be evaluated before any new
        # trailing movement. If keep_protected_winners_open=false, legacy
        # behavior is to expire even protected winners instead of extending them
        # via trailing.
        if float(management["expire_after_hours"]) > 0 and old_status == "OPEN" and hours_open >= float(management["expire_after_hours"]):
            protected_winner = (
                bool(management["keep_protected_winners_open"])
                and sl_moved_to_entry
                and self._beyond_breakeven_or_at(trade_type, stop_loss, entry)
                and pnl_points > 0
            )
            if not protected_winner:
                new_status = "EXPIRED"
                events.append("EXPIRED")
                result = "EXPIRED"
                close_price = current_price
                final_pnl = pnl_points

        # If a trade is already protected (TP1/BE/trailing phase), use the
        # candle's favorable extreme to ADVANCE TP2/trailing, but do NOT allow a
        # newly tightened trailing stop to be considered "hit" inside the SAME
        # OHLC candle that created it. With candle data we do not know whether
        # the rebound/high happened before or after the new favorable low/high.
        # Using the fresh stop immediately can therefore create false trailing
        # exits (exactly the "SL hit even though price never came back after the
        # stop moved" problem). So this cycle may close only on the PERSISTED
        # stop that already existed before the candle began; any tighter stop is
        # applied for the next cycle.
        protected_branch_handled = False
        protected_trade = bool(sl_moved_to_entry) and old_status in {"OPEN", "PARTIAL", "TP1_HIT"}
        if protected_trade and new_status in self.OPEN_STATUSES and "EXPIRED" not in events:
            protected_branch_handled = True
            if tp2_touched:
                new_status = "TP2_HIT"
                events.append("TP2_HIT")
                result = "WIN"
                close_price = tp2
                final_pnl = calculate_pips(entry, tp2, trade_type, symbol)
                # One bar can span both targets; TP1 must still be booked.
                _skipped = self._book_skipped_tp1_on_tp2(
                    trade, management, entry, tp1, tp2, trade_type, symbol,
                    tp1_touched, partial_close,
                )
                if _skipped is not None:
                    partial_close = True
                    events.append("TP1_HIT")
                    partial_realized_pnl, partial_closed_fraction, partial_scale_out_price = _skipped
            elif tp1_touched and old_status == "OPEN" and not partial_close:
                # TP1 can still be hit while in early-BE phase (BE done via
                # early_breakeven, not TP1). Must record partial close.
                new_status = "TP1_HIT"
                events.append("TP1_HIT")
                partial_close = True
                booked = self._book_tp1_partial(trade, management, entry, tp1, trade_type, symbol)
                if booked is not None:
                    partial_realized_pnl, partial_closed_fraction, partial_scale_out_price = booked
                # SL is already at entry from early BE — no need to move again.
            else:
                # Legacy compatibility: older rows may have sl_moved_to_entry=True
                # while stop_loss still shows the original wider SL. In that case
                # the active protective stop that existed before this candle is
                # entry, not the stale stored SL value.
                active_protective_stop = stop_loss
                if sl_moved_to_entry and not self._beyond_breakeven_or_at(trade_type, stop_loss, entry):
                    active_protective_stop = entry
                # A stop trailed into profit may only be executed by bars that
                # printed after it was set (see _trailed_stop_touched). A stop
                # at or worse than entry is the original risk and is judged on
                # the full window as before.
                if self._beyond_breakeven(trade_type, active_protective_stop, entry):
                    active_stop_touched = _trailed_stop_touched(active_protective_stop)
                elif protected_trade:
                    # A breakeven stop (at entry) on a protected trade must NOT
                    # be executed by a wick of the current/formation candle that
                    # closes far above it. Repeated phantom BE_HIT (b8ae314a:
                    # low wick 4162 vs close 4180.75) came from judging the
                    # breakeven on the raw candle low. Allow execution only by:
                    #   * the current candle CLOSING through the stop, or
                    #   * a COMPLETED candle (excluding the latest) printed after
                    #     the stop was armed whose extreme pierced it.
                    cur_through = (
                        current_price <= active_protective_stop
                        if trade_type == "BUY"
                        else current_price >= active_protective_stop
                    )
                    completed = (recent_candles or [])[:-1]
                    adverse_closed = self._adverse_extreme_after(
                        completed, trade_type, stop_source_time
                    )
                    closed_through = adverse_closed is not None and (
                        adverse_closed <= active_protective_stop
                        if trade_type == "BUY"
                        else adverse_closed >= active_protective_stop
                    )
                    active_stop_touched = cur_through or closed_through
                else:
                    active_stop_touched = _stop_touched(active_protective_stop)

                trailing_candidate = None
                if bool(management["trailing_enabled"]) and new_status in self.OPEN_STATUSES and "EXPIRED" not in events:
                    trailing_candidate = self._compute_trailing_stop(
                        trade_type,
                        favorable_price,
                        active_protective_stop,
                        entry,
                        symbol,
                        distance_points=effective_trail_points,
                        step_points=float(management["trailing_step_points"]),
                        min_profit_lock_points=float(management["trailing_min_profit_lock_points"]),
                    )
                    if trailing_candidate is not None:
                        new_stop_loss = trailing_candidate

                # IMPORTANT: only the stop that was already active before this
                # candle may close the trade this cycle. The freshly computed
                # trailing_candidate is just a next-cycle update, not a same-
                # candle executable stop.
                if self._beyond_breakeven(trade_type, active_protective_stop, entry) and active_stop_touched:
                    new_status = "SL_HIT"
                    events.append("TRAILING_SL_HIT")
                    trailing_exit_pnl = calculate_pips(entry, active_protective_stop, trade_type, symbol)
                    result = "WIN" if trailing_exit_pnl > 0 else "BREAKEVEN"
                    close_price = active_protective_stop
                    final_pnl = round(trailing_exit_pnl, 1)
                elif active_stop_touched and not self._beyond_breakeven(trade_type, active_protective_stop, entry):
                    new_status = "BE_HIT"
                    events.append("BE_HIT")
                    result = "BREAKEVEN"
                    close_price = entry
                    final_pnl = 0.0
                elif new_stop_loss is not None:
                    events.append("TRAILING_SL_UPDATED")

        # 1) Hard outcomes first using candle high/low when available.
        # Conservative ambiguity rule: if the same 5m candle touched both a
        # protective stop/breakeven and a target, close at the protective level.
        # OHLC data cannot prove which level was hit first, so this avoids
        # overstating paper-trading performance.
        if protected_branch_handled or new_status != old_status:
            pass
        elif (
            sl_moved_to_entry
            and self._beyond_breakeven(trade_type, stop_loss, entry)
            and _trailed_stop_touched(stop_loss)
        ):
            # The persisted stop_loss has been trailed past breakeven (see the
            # progressive-trailing branch below) and price has now pulled back
            # to it - this locks in the trailed profit rather than a plain
            # breakeven exit, and rather than the original far-away hard SL.
            # Applies whether the move-to-BE happened after TP1 or via the
            # early-breakeven mechanism while still OPEN.
            new_status = "SL_HIT"
            events.append("TRAILING_SL_HIT")
            trailing_exit_pnl = calculate_pips(entry, stop_loss, trade_type, symbol)
            result = "WIN" if trailing_exit_pnl > 0 else "BREAKEVEN"
            close_price = stop_loss
            final_pnl = round(trailing_exit_pnl, 1)
        elif sl_moved_to_entry and be_touched:
            new_status = "BE_HIT"
            events.append("BE_HIT")
            result = "BREAKEVEN"
            close_price = entry
            final_pnl = 0.0
        elif sl_touched:
            new_status = "SL_HIT"
            events.append("SL_HIT")
            result = "LOSS"
            close_price = stop_loss
            final_pnl = calculate_pips(entry, stop_loss, trade_type, symbol)
        elif tp2_touched:
            new_status = "TP2_HIT"
            events.append("TP2_HIT")
            result = "WIN"
            close_price = tp2
            final_pnl = calculate_pips(entry, tp2, trade_type, symbol)
            # A single bar reaching TP1 and TP2 together skipped the TP1
            # branch entirely and settled the full size at TP2. Book the half
            # that left at TP1, exactly as the two-candle path does.
            _skipped = self._book_skipped_tp1_on_tp2(
                trade, management, entry, tp1, tp2, trade_type, symbol,
                tp1_touched, partial_close,
            )
            if _skipped is not None:
                partial_close = True
                events.append("TP1_HIT")
                partial_realized_pnl, partial_closed_fraction, partial_scale_out_price = _skipped
        elif old_status == "OPEN" and tp1_touched:
            new_status = "TP1_HIT"
            events.append("TP1_HIT")
            partial_close = True
            # Actually book the TP1 fraction. Without this the "50% partial
            # close" is a label with no size behind it and TP2 settles the
            # full position at the TP2 price -- 114 points of pure fiction on
            # trade d917b1d5 alone.
            booked = self._book_tp1_partial(trade, management, entry, tp1, trade_type, symbol)
            if booked is not None:
                partial_realized_pnl, partial_closed_fraction, partial_scale_out_price = booked
            # Moving the stop to entry is only protection if the trade has
            # actually travelled relative to what it is risking. A TP1 sitting
            # 22 points away against a 133-point stop is 0.16R: price tags it
            # within a candle, the stop snaps to entry, and ordinary noise
            # closes a correct trade flat.
            #
            # The test is deliberately in R, not points. A fixed point
            # threshold would defer breakeven on a perfectly sound 1R target
            # simply because the instrument trades in smaller ranges.
            if bool(management["auto_be"]):
                min_be_rr = float(management.get("min_breakeven_rr") or 0.0)
                # Measure against the stop the trade was opened with. The live
                # stop may already have been trailed, which would understate
                # the original risk and let a tiny target qualify.
                risk_reference = (
                    self._f(trade.get("initial_stop_loss"), 0.0) or stop_loss
                )
                risk_points = abs(
                    calculate_pips(entry, risk_reference, trade_type, symbol)
                ) if risk_reference else 0.0
                travelled = abs(calculate_pips(entry, current_price, trade_type, symbol))
                travelled_rr = (travelled / risk_points) if risk_points > 0 else 0.0
                if min_be_rr > 0 and risk_points > 0 and travelled_rr < min_be_rr:
                    self.logger.info(
                        "Breakeven deferred for %s: TP1 touched at only %.2fR "
                        "(needs %.2fR) — keeping the structural stop so the "
                        "trade is not stopped out flat by noise",
                        trade.get("id"), travelled_rr, min_be_rr,
                    )
                else:
                    sl_moved_to_entry = True
                    new_stop_loss = entry  # actually persist breakeven, not just the flag
                    events.append("MOVE_SL_TO_BE")
        elif thesis_exit.get("scale_out"):
            new_status = "PARTIAL"
            partial_close = True
            events.append("THESIS_SCALE_OUT")
            # Book the closed half at the price it was actually closed at.
            #
            # `partial_close` used to be a label and nothing more: no code
            # anywhere read partial_close_percentage, so a "50% scale-out"
            # left the full size running and the final PnL was computed
            # entirely at the last price. The reduction has to be recorded
            # when it happens, or the number reported at the end describes a
            # trade that was never held.
            scale_fraction = min(max(self._f(thesis_exit.get("scale_fraction"), 0.5), 0.0), 1.0)
            already_closed = min(max(self._f(trade.get("closed_fraction"), 0.0), 0.0), 1.0)
            newly_closed = min(scale_fraction, max(0.0, 1.0 - already_closed))
            if newly_closed > 0:
                realized_before = self._f(trade.get("realized_pnl_points"), 0.0)
                scale_out_realized = round(pnl_points * newly_closed, 1)
                partial_realized_pnl = round(realized_before + scale_out_realized, 1)
                partial_closed_fraction = round(already_closed + newly_closed, 4)
                partial_scale_out_price = round(current_price, 2)
            # Breakeven protection is only protection when breakeven is behind
            # the market. If price is offside, entry sits between the market
            # and the current stop, so "moving to breakeven" tightens the stop
            # THROUGH the live price and closes the position instantly.
            #
            # This is a second line of defence: _thesis_exit_review already
            # refuses to scale a losing position, but any future caller that
            # sets scale_out must not be able to reintroduce the fault.
            if not sl_moved_to_entry and self._beyond_breakeven_or_at(trade_type, current_price, entry):
                sl_moved_to_entry = True
                new_stop_loss = entry
                events.append("MOVE_SL_TO_BE")
            elif not sl_moved_to_entry:
                self.logger.info(
                    "Breakeven not applied on scale-out for %s: price %.2f is on "
                    "the wrong side of entry %.2f, so the move would close the "
                    "trade instead of protecting it",
                    trade.get("id"), current_price, entry,
                )
        elif thesis_exit.get("exit_now"):
            new_status = "THESIS_EXIT"
            events.append("THESIS_EXIT")
            close_price = current_price
            final_pnl = round(pnl_points, 1)
            result = "WIN" if pnl_points > 0 else "LOSS" if pnl_points < 0 else "BREAKEVEN"
        else:
            # 2) Informational events only if no status-changing event happened.
            progress = self._progress_to_tp1(trade_type, entry, tp1, current_price)
            if old_status == "OPEN" and progress >= float(management["near_tp1_progress"]) and "NEAR_TP1" not in updates_sent:
                events.append("NEAR_TP1")
            if old_status == "OPEN" and hours_open >= float(management["time_warning_hours"]) and "LONG_RUNNING" not in updates_sent and "LONG_RUNNING" not in events:
                events.append("LONG_RUNNING")
            if exit_warning and "EXIT_WARNING" not in updates_sent and "EXIT_WARNING" not in events:
                events.append("EXIT_WARNING")
            if float(management["expire_after_hours"]) > 0 and old_status == "OPEN" and hours_open >= float(management["expire_after_hours"]):
                # Don't force-close a WINNING trade whose stop is already locked
                # at/above breakeven — let the (trailing) stop ride instead of
                # capping a runner by the clock. Only expire if it's not safely
                # protected in profit. Controlled by keep_protected_winners_open.
                protected_winner = (
                    bool(management["keep_protected_winners_open"])
                    and sl_moved_to_entry
                    and self._beyond_breakeven_or_at(trade_type, stop_loss, entry)
                    and pnl_points > 0
                )
                if not protected_winner:
                    new_status = "EXPIRED"
                    events.append("EXPIRED")
                    result = "EXPIRED"
                    close_price = current_price
                    final_pnl = pnl_points

            # 2b) EARLY BREAKEVEN: once the trade is +N points in profit, move the
            # stop to entry WITHOUT waiting for TP1. Independent of partial close.
            # Uses favorable_points (intrabar best price: low for SELL, high for BUY)
            # instead of pnl_points (close price) so a level touched during the 5m
            # candle is not missed just because the candle closed back away from it.
            # This keeps breakeven consistent with TP/SL detection, which already
            # uses the candle high/low.
            if (
                float(management["early_breakeven_points"]) > 0
                and not sl_moved_to_entry
                and new_status in self.OPEN_STATUSES
                and "EXPIRED" not in events
                and favorable_points >= float(management["early_breakeven_points"])
            ):
                sl_moved_to_entry = True
                new_stop_loss = entry  # persist the breakeven stop
                if "MOVE_SL_TO_BE" not in updates_sent:
                    events.append("MOVE_SL_TO_BE")

            # 3) Progressive trailing once breakeven is locked (either via TP1 or
            # via early breakeven above), and only when nothing status-changing
            # happened this run. Works while OPEN or TP1_HIT.
            if (
                bool(management["trailing_enabled"])
                and sl_moved_to_entry
                and new_status in self.OPEN_STATUSES
                and "EXPIRED" not in events
            ):
                base_stop = new_stop_loss if new_stop_loss is not None else stop_loss
                # Use the BEST price the trade has ever seen — all-time MFE from DB,
                # not just the current candle. Without this, trailing only sees the
                # current 5m candle and misses multi-candle favorable moves.
                # SELL: best = lowest price ever   |  BUY: best = highest price ever
                best_from_mfe = favorable_price
                if trade_type == "SELL" and max_favorable_excursion > 0:
                    all_time_low = entry - (max_favorable_excursion / 10.0)
                    if best_from_mfe <= 0 or all_time_low < best_from_mfe:
                        best_from_mfe = all_time_low
                elif trade_type == "BUY" and max_favorable_excursion > 0:
                    all_time_high = entry + (max_favorable_excursion / 10.0)
                    if best_from_mfe <= 0 or all_time_high > best_from_mfe:
                        best_from_mfe = all_time_high
                trailing_candidate = self._compute_trailing_stop(
                    trade_type,
                    best_from_mfe,
                    base_stop,
                    entry,
                    symbol,
                    distance_points=effective_trail_points,
                    step_points=float(management["trailing_step_points"]),
                    min_profit_lock_points=float(management["trailing_min_profit_lock_points"]),
                )
                if trailing_candidate is not None:
                    new_stop_loss = trailing_candidate
                    if "TRAILING_SL_UPDATED" not in events:
                        events.append("TRAILING_SL_UPDATED")

        # Avoid repeating informational events already sent. Status events are naturally one-time after status changes.
        closing_events = {"TP2_HIT", "SL_HIT", "TRAILING_SL_HIT", "BE_HIT", "THESIS_EXIT", "MANUAL_CLOSE"}
        if any(event in closing_events for event in events):
            events = [event for event in events if event not in {"NEAR_TP1", "LONG_RUNNING", "EXIT_WARNING"}]
        filtered_events: List[str] = []
        for event in events:
            if event in {"NEAR_TP1", "LONG_RUNNING", "MOVE_SL_TO_BE", "EXIT_WARNING"} and event in updates_sent:
                continue
            if event not in filtered_events:
                filtered_events.append(event)
        events = filtered_events
        updates_sent = self._append_updates_sent(updates_sent, events)

        updates: Dict[str, Any] = {
            "current_price": round(current_price, 2),
            "last_candle_high": round(high_price, 2),
            "last_candle_low": round(low_price, 2),
            "recent_30m_high": round(recent_window_high, 2) if recent_window_high is not None else None,
            "recent_30m_low": round(recent_window_low, 2) if recent_window_low is not None else None,
            "current_pnl": round(pnl_points, 1),
            "current_pnl_points": round(pnl_points, 1),
            "max_favorable_excursion": round(max_favorable_excursion, 1),
            "max_adverse_excursion": round(max_adverse_excursion, 1),
            "management_phase": management_phase,
            "exit_warning": exit_warning,
            "status": new_status,
            "sl_moved_to_entry": sl_moved_to_entry,
            "partial_close": partial_close,
            "updates_sent": updates_sent,
            "last_updated": self._iso(now),
        }
        if new_stop_loss is not None:
            updates["stop_loss"] = round(new_stop_loss, 2)
        # Re-stamp the newest bar EVERY cycle (operator directive 2026-08-05:
        # "measure only the previous candle"). A trailed stop is executable only
        # by bars newer than the stamp; re-stamping each cycle shrinks that window
        # to the current bar, so a low printed several bars back -- when the stop
        # sat lower -- can never execute a stop that has since been raised. This is
        # what produced the repeated phantom trailing exits (exit 4176.13 while
        # price stood at 4189.02).
        _stamp = self._newest_candle_time(recent_candles)
        if _stamp is not None:
            updates["trailing_stop_source_time"] = self._iso(_stamp)
        # Publish the gap and step that were actually used this cycle.
        #
        # The Telegram card used to state "150-point gap / 40-point step" as a
        # hardcoded string while the trade ran on its profile's own numbers --
        # continuation_profile is 170/45 -- so the message contradicted both
        # the plan card and the arithmetic behind the stop it was reporting.
        # A tightened reversal trail would have made it wrong a third way.
        updates["trailing_distance_points"] = effective_trail_points
        updates["trailing_step_points"] = float(management["trailing_step_points"])
        if reversal_trail_active:
            updates["reversal_trail_active"] = True
            updates["reversal_trail_points"] = effective_trail_points
        if partial_realized_pnl is not None:
            updates["realized_pnl_points"] = partial_realized_pnl
            updates["closed_fraction"] = partial_closed_fraction
            updates["scale_out_price"] = partial_scale_out_price
        if result is not None:
            updates["result"] = result

        # Composite settlement for a position that was scaled before it closed.
        #
        # `final_pnl` above is computed as if the whole position ran to the
        # closing price. Once part of it was booked earlier at a different
        # price that is simply untrue, and it is the number the scoreboard,
        # the weekly report and the learning service all consume.
        #
        # Settle what is left on the remaining fraction and add back what was
        # already realized, so the reported result is the one the account
        # actually experienced.
        closed_fraction_so_far = min(
            max(self._f(updates.get("closed_fraction", trade.get("closed_fraction")), 0.0), 0.0), 1.0
        )
        realized_so_far = self._f(
            updates.get("realized_pnl_points", trade.get("realized_pnl_points")), 0.0
        )

        # A booked partial must survive a missing field.
        #
        # The composite needs BOTH numbers. If either is absent -- an older
        # row, or a Supabase write that dropped an unknown column
        # (services/database.py:1127 drops-and-retries) -- the settlement
        # silently reverted to the remaining leg alone and the money already
        # taken off the table vanished from the record.
        #
        # 2026-08-03, trade bdde9a5f: half was booked at +3.0 on the scale-out
        # and the Breakeven card then reported "Actual PnL: +0.0 pts". The
        # realized half was real; the zero was not.
        #
        # `partial_close` is the flag every scale-out path sets, so it is the
        # honest signal that a reduction happened. When it is set but the
        # numbers are missing, fall back to the configured fraction rather
        # than to zero -- an approximation of a real event beats an exact
        # record of one that is untrue.
        # `scale_out_price` is the evidence, and it is deliberately the ONLY
        # trigger. `partial_close` alone is ambiguous: TP1 sets it too, and an
        # earlier version of this reconstruction keyed on the flag and halved
        # every legacy TP1 trade (700 -> 350, 243.3 -> 121.7). Two existing
        # tests caught that immediately and they were right. A thesis
        # scale-out always records the price its half left at; a TP1 partial
        # does not.
        scale_price = self._f(updates.get("scale_out_price", trade.get("scale_out_price")), 0.0)
        if partial_close and scale_price > 0 and (closed_fraction_so_far <= 0 or realized_so_far == 0.0):
            recovered_fraction = closed_fraction_so_far
            if recovered_fraction <= 0:
                recovered_fraction = min(max(self.thesis_exit_silent_scale_fraction, 0.0), 1.0)
            recovered_realized = realized_so_far
            if recovered_fraction > 0 and recovered_realized == 0.0:
                recovered_realized = round(
                    calculate_pips(entry, scale_price, trade_type, symbol) * recovered_fraction, 1
                )
            if recovered_fraction != closed_fraction_so_far or recovered_realized != realized_so_far:
                self.logger.warning(
                    "Trade %s scaled out at %.2f but its booked numbers were "
                    "incomplete (fraction=%s, realized=%s); reconstructed %.0f%% at "
                    "%.1f pts so the closed half is not lost from the result",
                    trade.get("id"), scale_price,
                    updates.get("closed_fraction", trade.get("closed_fraction")),
                    updates.get("realized_pnl_points", trade.get("realized_pnl_points")),
                    recovered_fraction * 100, recovered_realized,
                )
            closed_fraction_so_far = recovered_fraction
            realized_so_far = recovered_realized

        if final_pnl is not None and closed_fraction_so_far > 0 and new_status in self.CLOSED_STATUSES:
            remaining = max(0.0, 1.0 - closed_fraction_so_far)
            composite = round(realized_so_far + final_pnl * remaining, 1)
            self.logger.info(
                "Composite settlement for %s: %.1f realized on %.0f%% + %.1f on the "
                "remaining %.0f%% = %.1f pts",
                trade.get("id"), realized_so_far, closed_fraction_so_far * 100,
                final_pnl, remaining * 100, composite,
            )
            final_pnl = composite
            updates["closed_fraction"] = 1.0
            updates["realized_pnl_points"] = composite
        if thesis_exit.get("exit_now") or thesis_exit.get("scale_out"):
            updates["reasons"] = [str(thesis_exit.get("reason") or "Automatic thesis exit")]
        if close_price is not None:
            updates["close_price"] = round(close_price, 2)
            updates["closed_at"] = self._iso(now)
            updates["close_time"] = self._iso(now)
        if final_pnl is not None:
            # Keep both names synchronized. Some report/dashboard code or older
            # schemas may read one or the other; stale current_pnl_points must not
            # override the final realized result after a trailing SL+ exit.
            updates["final_pnl"] = round(final_pnl, 1)
            updates["final_pnl_points"] = round(final_pnl, 1)

        return {
            "trade_id": trade.get("id"),
            "old_status": old_status,
            "new_status": new_status,
            "pnl_points": round(pnl_points, 1),
            "events": events,
            "updates": updates,
            "progress_to_tp1": round(self._progress_to_tp1(trade_type, entry, tp1, current_price), 3),
            "hours_open": round(self._hours_open(trade, now), 2),
            "max_favorable_excursion": round(max_favorable_excursion, 1),
            "max_adverse_excursion": round(max_adverse_excursion, 1),
            "management_phase": management_phase,
            "exit_warning": exit_warning,
        }

    def create_trade_record(self, decision: Dict[str, Any], trade_id: str | None = None) -> Dict[str, Any]:
        """Build a trade dict from a decision; useful for tests or JSON fallback."""
        signal = decision.get("signal", {}) or {}
        entry = signal.get("entry", {}) or {}
        entry_price = self._f(entry.get("price"), (self._f(entry.get("low")) + self._f(entry.get("high"))) / 2)
        now_iso = self._iso(datetime.now(timezone.utc))
        return {
            "id": trade_id or f"TRADE_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            "type": decision.get("decision", signal.get("type")),
            "entry_price": round(entry_price, 2),
            "entry_time": now_iso,
            "stop_loss": round(self._f(signal.get("stop_loss")), 2),
            "initial_stop_loss": round(self._f(signal.get("stop_loss")), 2),
            "tp1": round(self._f(signal.get("tp1")), 2),
            "tp2": round(self._f(signal.get("tp2")), 2),
            "status": "OPEN",
            "current_price": round(self._f(decision.get("current_price"), entry_price), 2),
            "current_pnl": 0,
            "current_pnl_points": 0,
            "max_favorable_excursion": 0,
            "max_adverse_excursion": 0,
            "management_phase": "INITIAL",
            "exit_warning": None,
            "sl_moved_to_entry": False,
            "partial_close": False,
            "updates_sent": [],
            "result": None,
            "created_at": now_iso,
            "close_time": None,
            "close_price": None,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Compatibility wrapper for the BaseAgent-style interface."""
        trades = data.get("open_trades", [])
        current_price = self._f(data.get("current_price"))
        evaluations = [self.evaluate_trade(trade, current_price) for trade in trades]
        return {"agent": self.name, "evaluated": len(evaluations), "results": evaluations, "summary": f"Evaluated {len(evaluations)} open trade(s)"}

    # ── Price sanity gate ──
    # Data providers occasionally return corrupted ticks (e.g. 3366 for XAU/USD
    # when the real price is ~4150). A single bad tick must never trigger false
    # TP/SL hits. We reject any current_price more than 15% away from the
    # entry_price of the trade being evaluated. For gold, 15% ≈ $600 — far wider
    # than any realistic intraday move.
    _PRICE_SANITY_MAX_DEVIATION = 0.15  # 15 % of entry price

    def _price_sanity_failed(self, current_price: float, entry_price: float, trade_id: str = "") -> bool:
        """Return True if current_price is clearly corrupt relative to entry."""
        if entry_price <= 0 or current_price <= 0:
            return True
        deviation = abs(current_price - entry_price) / entry_price
        if deviation > self._PRICE_SANITY_MAX_DEVIATION:
            self.logger.warning(
                "PRICE SANITY FAILED for %s: current=%.2f vs entry=%.2f (deviation=%.1f%% > %.0f%%). "
                "Skipping level evaluation — possible data provider glitch.",
                trade_id or "unknown",
                current_price,
                entry_price,
                deviation * 100,
                self._PRICE_SANITY_MAX_DEVIATION * 100,
            )
            return True
        return False

    def _window_extremes_since(self, trade: Dict[str, Any], recent_candles: List[Dict[str, Any]] | None) -> tuple[float | None, float | None]:
        if not recent_candles:
            return None, None
        # A live position may only be judged by price action it was actually
        # exposed to.
        #
        # The baseline used to prefer `last_updated`, falling back to
        # `created_at` -- the moment the PLAN was written, which for a pending
        # order can be hours before it filled. Any 5m bar from that window was
        # then treated as this trade's own excursion.
        #
        # On 2026-07-30 that closed 8d6ad198 at "breakeven" while price stood
        # at 4075.69, 110 points above entry and rising: an older bar printed
        # before the fill had dipped to the entry level, and `be_touched` read
        # it as a live touch. The card contradicted itself in two lines --
        # "Current Price: 4075.69" next to "Exit Price: 4064.69".
        #
        # Fill time is the only honest floor. `entry_time` is written the
        # moment the order activates; `last_updated` may still be consulted
        # afterwards, but never to reach back before the fill.
        fill_time = self._parse_dt(str(trade.get("entry_time") or ""))
        last_seen = self._parse_dt(str(trade.get("last_updated") or ""))
        baseline = last_seen
        if fill_time and (baseline is None or baseline < fill_time):
            baseline = fill_time
        if baseline is None:
            baseline = self._parse_dt(str(trade.get("created_at") or ""))
        highs: List[float] = []
        lows: List[float] = []
        for candle in recent_candles:
            if not isinstance(candle, dict):
                continue
            dt = self._parse_dt(str(candle.get("time") or ""))
            if baseline and dt and dt <= baseline:
                continue
            # A bar with no readable timestamp cannot be proved to belong to
            # this trade's lifetime. When a baseline exists, skip it rather
            # than let it widen the excursion on trust.
            if baseline and dt is None:
                continue
            high = self._f(candle.get("high"), 0.0)
            low = self._f(candle.get("low"), 0.0)
            if high > 0:
                highs.append(high)
            if low > 0:
                lows.append(low)
        if not highs or not lows:
            return None, None
        return max(highs), min(lows)

    def _book_tp1_partial(
        self,
        trade: Dict[str, Any],
        management: Dict[str, Any],
        entry: float,
        tp1: float,
        trade_type: str,
        symbol: str,
    ) -> tuple[float, float, float] | None:
        """Realize the TP1 fraction at the TP1 price.

        Returns ``(realized_total, closed_fraction_total, tp1_price)`` or None
        when partial closing is disabled or the position is already fully
        booked. Booking at ``tp1`` rather than the current price matters: TP1
        is a limit order, so the half leaves at exactly that level.
        """
        if not bool(management.get("partial_close_at_tp1")):
            return None
        fraction = float(management.get("partial_close_fraction") or 0.0)
        if fraction <= 0 or tp1 <= 0:
            return None
        already_closed = min(max(self._f(trade.get("closed_fraction"), 0.0), 0.0), 1.0)
        newly_closed = min(fraction, max(0.0, 1.0 - already_closed))
        if newly_closed <= 0:
            return None
        realized_before = self._f(trade.get("realized_pnl_points"), 0.0)
        booked = calculate_pips(entry, tp1, trade_type, symbol) * newly_closed
        return (
            round(realized_before + booked, 1),
            round(already_closed + newly_closed, 4),
            round(tp1, 2),
        )

    def _book_skipped_tp1_on_tp2(
        self,
        trade: Dict[str, Any],
        management: Dict[str, Any],
        entry: float,
        tp1: float,
        tp2: float,
        trade_type: str,
        symbol: str,
        tp1_touched: bool,
        partial_close: bool,
    ) -> tuple[float, float, float] | None:
        """Book the TP1 half when one candle reached TP1 and TP2 together.

        The outcome branches are an if/elif chain and ``tp2_touched`` is
        tested before ``tp1_touched``, so a single 5-minute bar that spans
        both targets jumps straight to TP2 and TP1 never runs. The position
        is then settled at full size on the TP2 price.

        2026-07-31, trade d917b1d5: one bar fell 4055 -> 4023, crossing TP1
        4051.98 and TP2 4029.17. The card read "OPEN -> TP2_HIT ... +456.1
        pts" -- no TP1 event, no half booked at 4051.98, and 114 points that
        the account never received.

        Price cannot reach TP2 without passing through TP1, so the TP1 fill
        is not an assumption: it is the only order in which those two levels
        can be touched. Booking it here keeps the arithmetic identical to the
        two-candle path, where TP1 fires on its own bar.

        Returns ``(realized_total, closed_fraction_total, tp1_price)`` or None
        when this does not apply.
        """
        if not tp1_touched or partial_close or tp1 <= 0 or tp2 <= 0:
            return None
        # Only when TP1 genuinely sits between the entry and TP2. A TP1 on the
        # far side, or beyond TP2, is a malformed plan and is left untouched.
        tp1_travel = calculate_pips(entry, tp1, trade_type, symbol)
        tp2_travel = calculate_pips(entry, tp2, trade_type, symbol)
        if tp1_travel <= 0 or tp2_travel <= tp1_travel:
            return None
        return self._book_tp1_partial(trade, management, entry, tp1, trade_type, symbol)

    def _adverse_extreme_after(
        self,
        recent_candles: List[Dict[str, Any]] | None,
        trade_type: str,
        after: datetime | None,
    ) -> float | None:
        """Worst price printed strictly after ``after``.

        Used to decide whether a trailed stop was genuinely traded through.
        For a SELL the adverse extreme is the highest high; for a BUY it is
        the lowest low. Bars at or before ``after`` are the bars the stop was
        derived from, so they cannot also execute it. A bar with no readable
        timestamp is skipped rather than trusted, matching
        ``_window_extremes_since``.
        """
        if not recent_candles or after is None:
            return None
        values: List[float] = []
        for candle in recent_candles:
            if not isinstance(candle, dict):
                continue
            dt = self._parse_dt(str(candle.get("time") or ""))
            if dt is None or dt <= after:
                continue
            key = "low" if trade_type == "BUY" else "high"
            value = self._f(candle.get(key), 0.0)
            if value > 0:
                values.append(value)
        if not values:
            return None
        return min(values) if trade_type == "BUY" else max(values)

    def _newest_candle_time(self, recent_candles: List[Dict[str, Any]] | None) -> datetime | None:
        """Timestamp of the newest bar that fed a trailing calculation."""
        if not recent_candles:
            return None
        stamps: List[datetime] = []
        for candle in recent_candles:
            if not isinstance(candle, dict):
                continue
            dt = self._parse_dt(str(candle.get("time") or ""))
            if dt is not None:
                stamps.append(dt)
        return max(stamps) if stamps else None

    def _recent_window_extremes(self, recent_candles: List[Dict[str, Any]] | None) -> tuple[float | None, float | None]:
        if not recent_candles:
            return None, None
        highs: List[float] = []
        lows: List[float] = []
        for candle in recent_candles:
            if not isinstance(candle, dict):
                continue
            high = self._f(candle.get("high"), 0.0)
            low = self._f(candle.get("low"), 0.0)
            if high > 0:
                highs.append(high)
            if low > 0:
                lows.append(low)
        if not highs or not lows:
            return None, None
        return max(highs), min(lows)

    def _ordered_unexamined_candles(
        self, trade: Dict[str, Any], recent_candles: List[Dict[str, Any]] | None
    ) -> List[Dict[str, Any]]:
        """Candles printed after the last examined bar, oldest first.

        `trailing_stop_source_time` is re-stamped to the newest bar every cycle,
        so on a healthy run this list holds only the current bar (len 1) and the
        sequential reviewer stays out of the way. After a missed cycle (downtime)
        it holds every unexamined bar, and they must be replayed IN ORDER so a
        trailing stop is raised by an early high before a later low is tested
        against it (operator directive 2026-08-05).
        """
        stamp = self._parse_dt(str(trade.get("trailing_stop_source_time") or ""))
        if stamp is None or not recent_candles:
            return []
        out: List[tuple] = []
        for candle in recent_candles:
            if not isinstance(candle, dict):
                continue
            dt = self._parse_dt(str(candle.get("time") or ""))
            if dt is not None and dt > stamp:
                out.append((dt, candle))
        out.sort(key=lambda item: item[0])
        return [c for _, c in out]

    def _missed_candles_review(
        self,
        *,
        trade: Dict[str, Any],
        trade_type: str,
        entry: float,
        stop_loss: float,
        sl_moved_to_entry: bool,
        partial_close: bool,
        tp1: float,
        tp2: float,
        management: Dict[str, Any],
        symbol: str,
        unexamined: List[Dict[str, Any]],
    ) -> Dict[str, Any] | None:
        """Replay unexamined bars in chronological order and decide the outcome.

        Within each bar the protective stop is tested against the stop level that
        existed BEFORE this bar's high could raise it (a stop ratcheted by this
        bar cannot be executed by this bar), then trailing ratchets from the high,
        then TP1/TP2 are tested. Returns a full evaluation dict on a terminal or
        state-changing result, else None to defer to the normal per-cycle path.
        """
        buy = trade_type == "BUY"
        trail_enabled = bool(management.get("trailing_enabled"))
        trail_gap = self._f(management.get("trailing_distance_points"), 0.0)
        # Start from the PERSISTED protective stop (breakeven or an already
        # trailed level). Initialising from the entry -- as an earlier version
        # did -- rebuilt the trail from scratch and could LOWER a previously
        # higher trailing stop (7ebe8906: 4250.14 -> 4238.80), giving back
        # locked profit. The replay may only ratchet UP from the current stop.
        stop = stop_loss if stop_loss > 0 else entry
        sl_moved = sl_moved_to_entry
        partial = partial_close
        events: List[str] = []
        realized = closed_fraction = scale_price = None
        terminal = None  # (status, event, close_price, result)

        for candle in unexamined:
            high = self._f(candle.get("high"), 0.0)
            low = self._f(candle.get("low"), 0.0)
            if high <= 0 or low <= 0:
                continue
            prev_stop = stop
            # 1) protective stop tested against the level that existed before
            #    this bar's high could raise it.
            hit = (low <= prev_stop) if buy else (high >= prev_stop)
            # 2026-08-07 (trailing unification audit): replay must honour the
            # same wick protection as the live cycle (b8ae314a). A breakeven
            # stop at entry is NOT executed by a bar that only wicks through
            # it and closes back in favour; require an adverse close.
            if hit and sl_moved and abs(prev_stop - entry) < 1e-9                     and candle is unexamined[-1]:
                # Only the freshest bar (the live "current" candle) gets the
                # wick protection; earlier COMPLETED bars execute on their
                # adverse extreme exactly like the live cycle does.
                close = self._f(candle.get("close"), 0.0)
                if close > 0 and not ((close <= prev_stop) if buy else (close >= prev_stop)):
                    hit = False
            if hit:
                if sl_moved:
                    pnl = calculate_pips(entry, prev_stop, trade_type, symbol)
                    beyond = (prev_stop > entry) if buy else (prev_stop < entry)
                    terminal = (
                        "SL_HIT" if beyond else "BE_HIT",
                        "TRAILING_SL_HIT" if beyond else "BE_HIT",
                        prev_stop,
                        ("WIN" if pnl > 0 else "BREAKEVEN"),
                    )
                else:
                    terminal = ("SL_HIT", "SL_HIT", stop_loss, "LOSS")
                break
            # 2) trailing ratchet from this bar's high (only once protected).
            if sl_moved and trail_enabled and trail_gap > 0:
                # BUY ratchets up from the bar high; SELL ratchets down from the
                # bar low (the favorable extreme for each side).
                cand = (high - points_to_price(trail_gap, symbol)) if buy else (
                    low + points_to_price(trail_gap, symbol)
                )
                if (cand > stop) if buy else (cand < stop):
                    stop = cand
            # 3) TP1 books the half and arms breakeven.
            tp1_hit = (high >= tp1) if buy else (low <= tp1)
            if tp1_hit and not partial and tp1 > 0:
                partial = True
                sl_moved = True
                stop = max(stop, entry) if buy else min(stop, entry)
                events.append("TP1_HIT")
                booked = self._book_tp1_partial(trade, management, entry, tp1, trade_type, symbol)
                if booked is not None:
                    realized, closed_fraction, scale_price = booked
            # 4) TP2 settles the rest.
            tp2_hit = (high >= tp2) if buy else (low <= tp2)
            if tp2_hit and tp2 > 0:
                terminal = ("TP2_HIT", "TP2_HIT", tp2, "WIN")
                break

        if terminal is None and not events and abs(stop - stop_loss) < 1e-9 and sl_moved == sl_moved_to_entry:
            return None

        status, event, close_price, result = terminal if terminal else (None, None, None, None)
        if terminal and event not in events:
            events.append(event)
        updates: Dict[str, Any] = {
            "status": status if status else ("TP1_HIT" if partial else trade.get("status")),
            "sl_moved_to_entry": sl_moved,
            "partial_close": partial,
            "last_updated": self._iso(datetime.now(timezone.utc)),
        }
        if terminal:
            updates.update({
                "result": result,
                "close_price": round(close_price, 2),
                "close_time": self._iso(datetime.now(timezone.utc)),
                "closed_at": self._iso(datetime.now(timezone.utc)),
                "final_pnl": round(calculate_pips(entry, close_price, trade_type, symbol), 1),
                "final_pnl_points": round(calculate_pips(entry, close_price, trade_type, symbol), 1),
            })
        if stop != stop_loss or sl_moved:
            updates["stop_loss"] = round(stop, 2)
        if realized is not None:
            updates["realized_pnl_points"] = realized
            updates["closed_fraction"] = closed_fraction
            updates["scale_out_price"] = scale_price
        stamp = self._newest_candle_time(unexamined)
        if stamp is not None:
            updates["trailing_stop_source_time"] = self._iso(stamp)
        return {
            "trade_id": trade.get("id"),
            "old_status": str(trade.get("status", "OPEN")),
            "new_status": updates["status"],
            "pnl_points": self._f(updates.get("final_pnl_points"), 0.0),
            "events": events,
            "updates": updates,
            "progress_to_tp1": 0.0,
            "hours_open": 0.0,
        }

    def _zone_width_points(self, trade: Dict[str, Any], entry: float, symbol: str) -> float:
        snapshot = self._trade_snapshot(trade)
        signal = snapshot.get("signal") or {}
        zone = signal.get("entry") or {}
        low = self._f(zone.get("low"), 0.0)
        high = self._f(zone.get("high"), 0.0)
        if low > 0 and high > 0:
            return abs(calculate_pips(low, high, "BUY", symbol))
        return 0.0

    def _has_planner_pending_context(self, trade: Dict[str, Any]) -> bool:
        snapshot = self._trade_snapshot(trade)
        setup = snapshot.get("setup_context") or {}
        plan = snapshot.get("session_plan") or {}
        if isinstance(setup, dict) and (
            setup.get("pending_plan_role")
            or setup.get("selection_role")
            or setup.get("scenario_id")
            or str(setup.get("id") or "").startswith("DAYMAP::")
        ):
            return True
        if isinstance(plan, dict) and (
            plan.get("scenario_id")
            or plan.get("plan_id")
            or plan.get("planner_confidence") is not None
            or plan.get("session_bias")
        ):
            return True
        return False

    def _planned_rr_value(self, trade: Dict[str, Any]) -> float:
        try:
            direct = float(trade.get("planned_rr"))
            if direct > 0:
                return direct
        except (TypeError, ValueError):
            pass
        snapshot = self._trade_snapshot(trade)
        signal = snapshot.get("signal") or {}
        for key in ("rr_ratio", "tp2_rr"):
            try:
                value = float(signal.get(key))
                if value > 0:
                    return value
            except (TypeError, ValueError):
                continue
        return 0.0

    def _session_plan_row_payload(self, row: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(row, dict):
            return {}
        payload = row.get("payload")
        if isinstance(payload, dict) and payload:
            return dict(payload)
        return dict(row)

    def _session_plan_reference_time(self, row: Dict[str, Any] | None) -> datetime | None:
        payload = self._session_plan_row_payload(row)
        for key in ("analysis_run_at", "plan_created_at", "created_at", "updated_at"):
            parsed = self._parse_dt(str((row or {}).get(key) or payload.get(key) or ""))
            if parsed:
                return parsed
        return None

    def _opposite_ready_plan_cancel_reason(
        self,
        trade: Dict[str, Any],
        *,
        database: Any | None,
        symbol: str,
        trade_type: str,
    ) -> str | None:
        if database is None or not hasattr(database, "get_latest_session_plan"):
            return None
        try:
            latest_row = database.get_latest_session_plan(symbol=symbol, plan_ready_only=True)
        except Exception:
            return None
        if not isinstance(latest_row, dict) or not latest_row:
            return None
        plan = self._session_plan_row_payload(latest_row)
        latest_bias = str(plan.get("session_bias") or latest_row.get("session_bias") or "").upper()
        if latest_bias not in {"BUY", "SELL"} or latest_bias == trade_type:
            return None
        if not bool(plan.get("plan_ready", latest_row.get("plan_ready", False))):
            return None
        latest_status = str(plan.get("plan_status") or latest_row.get("plan_status") or "").upper()
        if latest_status and latest_status != "READY":
            return None
        authority_state = str(plan.get("authority_state") or latest_row.get("authority_state") or "").upper()
        if authority_state in {"BLOCKED", "ERROR"}:
            return None
        latest_time = self._session_plan_reference_time(latest_row)
        trade_time = self._parse_dt(str(trade.get("created_at") or trade.get("entry_time") or trade.get("opened_at") or ""))
        if latest_time and trade_time and latest_time <= trade_time:
            return None
        return f"newer opposite ready session plan ({latest_bias}) replaced this planner pending"

    def _near_miss_review(
        self,
        trade: Dict[str, Any],
        *,
        trade_type: str,
        order_type: str,
        entry: float,
        stop_loss: float,
        tp2: float,
        current_price: float,
        high_price: float,
        low_price: float,
        recent_window_high: float | None,
        recent_window_low: float | None,
        symbol: str,
        target_progress_pct: float,
    ) -> tuple[bool, str | None, float | None]:
        if not self.near_miss_enabled:
            return False, None, None
        if self.near_miss_require_planner_context:
            snapshot = self._trade_snapshot(trade)
            setup = snapshot.get("setup_context") or {}
            if not isinstance(setup, dict) or not (setup.get("pending_plan_role") or setup.get("selection_role")):
                return False, None, None
        if not str(order_type or "").upper().endswith("LIMIT"):
            return False, None, None
        if stop_loss <= 0 or tp2 <= 0 or entry <= 0:
            return False, None, None
        if target_progress_pct > self.near_miss_max_target_progress_pct:
            return False, None, None

        zone_width_points = self._zone_width_points(trade, entry, symbol)
        recent_range_points = abs(calculate_pips(recent_window_low or low_price, recent_window_high or high_price, "BUY", symbol)) if recent_window_high and recent_window_low else abs(calculate_pips(low_price, high_price, "BUY", symbol))
        halo_points = min(
            self.near_miss_max_halo_points,
            max(
                self.near_miss_min_halo_points,
                zone_width_points * self.near_miss_zone_width_multiplier,
                recent_range_points * self.near_miss_recent_range_multiplier,
            ),
        )
        confirm_points = max(self.near_miss_min_confirmation_points, halo_points * 0.5)

        if trade_type == "SELL":
            approach_price = recent_window_high if recent_window_high is not None else high_price
            if approach_price <= 0 or approach_price >= entry:
                return False, None, None
            missed_by_points = abs(calculate_pips(approach_price, entry, "BUY", symbol))
            move_away_points = max(0.0, calculate_pips(current_price, approach_price, "BUY", symbol))
            if not (0 < missed_by_points <= halo_points and move_away_points >= confirm_points):
                return False, None, None
        else:
            approach_price = recent_window_low if recent_window_low is not None else low_price
            if approach_price <= 0 or approach_price <= entry:
                return False, None, None
            missed_by_points = abs(calculate_pips(entry, approach_price, "BUY", symbol))
            move_away_points = max(0.0, calculate_pips(approach_price, current_price, "BUY", symbol))
            if not (0 < missed_by_points <= halo_points and move_away_points >= confirm_points):
                return False, None, None

        risk = abs(stop_loss - current_price)
        reward = abs(tp2 - current_price)
        if risk <= 0:
            return False, None, None
        remaining_rr = reward / risk
        if remaining_rr < self.near_miss_min_remaining_rr:
            return False, None, None
        reason = f"Near-miss market conversion: missed entry by {missed_by_points:.0f} pts within halo {halo_points:.0f}, then confirmed away by {move_away_points:.0f} pts"
        return True, reason, round(halo_points, 1)

    def _entry_zone_bounds(self, trade: Dict[str, Any]) -> tuple[float, float]:
        """The published entry area, as low/high prices. (0, 0) when absent."""
        snapshot = self._trade_snapshot(trade)
        signal = snapshot.get("signal") or {}
        zone = signal.get("entry") or {}
        low = self._f(zone.get("low"), 0.0)
        high = self._f(zone.get("high"), 0.0)
        if low <= 0 or high <= 0:
            return 0.0, 0.0
        if low > high:
            low, high = high, low
        return low, high

    def _zone_touch_review(
        self,
        trade: Dict[str, Any],
        *,
        trade_type: str,
        order_type: str,
        entry: float,
        stop_loss: float,
        tp2: float,
        current_price: float,
        candle_high: float,
        candle_low: float,
        recent_window_high: float | None,
        recent_window_low: float | None,
        symbol: str,
        runtime: Dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> Dict[str, Any]:
        """Fill a mapped order that traded inside its own entry zone.

        The reference entry is one price inside an area; the area is the
        thesis. When price enters the zone and then leaves it in the trade's
        favour, the setup happened -- the order simply sat a few points too
        deep to be touched.

        Two conditions keep this honest:

        - the exit must be IN FAVOUR. A wick into the zone followed by a
          collapse is not an entry signal, and at the moment of the touch the
          two are indistinguishable. Requiring a close beyond the far edge is
          what separates "price worked through the area" from "price rejected
          it".
        - the risk must not grow. Filling at the edge without moving the stop
          would silently widen risk and drop RR under the configured floor.
          The stop travels the same distance as the entry, so the trade keeps
          the exact risk the map planned for it.
        """
        blank = {"activate": False}
        if not self.zone_touch_enabled:
            return blank
        if not str(order_type or "").upper().endswith("LIMIT"):
            return blank
        if entry <= 0 or stop_loss <= 0:
            return blank
        if self.zone_touch_require_planner_context and not self._has_planner_pending_context(trade):
            return blank

        zone_low, zone_high = self._entry_zone_bounds(trade)
        if zone_low <= 0 or zone_high <= 0 or zone_high <= zone_low:
            return blank

        high = max(self._f(candle_high, current_price), self._f(recent_window_high, 0.0))
        low = self._f(candle_low, current_price)
        window_low = self._f(recent_window_low, 0.0)
        if window_low > 0:
            low = min(low, window_low)
        if high <= 0 or low <= 0:
            return blank

        # Did price trade inside the published area at all?
        touched = low <= zone_high and high >= zone_low
        runtime = runtime if isinstance(runtime, dict) else {}
        first_seen = self._parse_dt(str(runtime.get("zone_first_touch_at") or ""))
        # A departure recorded on an earlier cycle stays true. Price leaving
        # the area in favour is the event; the cycle that happens to observe
        # it, and where price sits by then, are not part of the test.
        left_before = bool(runtime.get("zone_left_in_favour"))

        if not touched and not left_before:
            return blank

        planned_risk = abs(entry - stop_loss)
        if planned_risk <= 0:
            return blank

        if trade_type == "BUY":
            fill = zone_high
            # Already fillable at the reference entry: leave it to the normal
            # touch path rather than filling worse than the map asked for.
            if low <= entry:
                return blank
            left_now = high > zone_high
            left = left_now or (left_before and self.zone_touch_allow_after_return)
            if self.zone_touch_require_exit_in_favour and not left:
                return {
                    "activate": False,
                    "reason": "price has not yet left the zone upward",
                    "touched": touched,
                }
            new_stop = fill - planned_risk if self.zone_touch_preserve_planned_risk else stop_loss
            reward = tp2 - fill
        else:
            fill = zone_low
            if high >= entry:
                return blank
            left_now = low < zone_low
            left = left_now or (left_before and self.zone_touch_allow_after_return)
            if self.zone_touch_require_exit_in_favour and not left:
                return {
                    "activate": False,
                    "reason": "price has not yet left the zone downward",
                    "touched": touched,
                }
            new_stop = fill + planned_risk if self.zone_touch_preserve_planned_risk else stop_loss
            reward = fill - tp2

        # The mapped price gets first refusal. Only after price has been in
        # the area this long without filling is the order judged too deep.
        if self.zone_touch_grace_minutes > 0:
            reference = now or datetime.now(timezone.utc)
            if first_seen is None:
                return {
                    "activate": False,
                    "reason": "waiting for the mapped entry to fill first",
                    "touched": touched,
                }
            waited = (reference - first_seen).total_seconds() / 60.0
            if waited < self.zone_touch_grace_minutes:
                return {
                    "activate": False,
                    "reason": (
                        f"mapped entry still has time ({waited:.0f} of "
                        f"{self.zone_touch_grace_minutes:.0f} min)"
                    ),
                    "touched": touched,
                }

        # How much of the fill -> TP1 path has price already covered?
        tp1 = self._f(trade.get("tp1"), 0.0)
        if tp1 > 0:
            span = abs(tp1 - fill)
            travelled = (current_price - fill) if trade_type == "BUY" else (fill - current_price)
            if span > 0 and travelled > 0:
                progress_pct = travelled / span * 100.0
                if progress_pct > self.zone_touch_max_target_progress_pct:
                    return {
                        "activate": False,
                        "reason": (
                            f"price already covered {progress_pct:.0f}% of the path to TP1 "
                            f"(limit {self.zone_touch_max_target_progress_pct:.0f}%)"
                        ),
                    }

        risk = abs(fill - new_stop)
        if risk <= 0:
            return blank
        remaining_rr = reward / risk if reward > 0 else 0.0
        if remaining_rr < self.zone_touch_min_remaining_rr:
            return {
                "activate": False,
                "reason": f"zone-edge RR {remaining_rr:.2f} below {self.zone_touch_min_remaining_rr:.2f}",
            }

        missed_by = abs(calculate_pips(entry, low if trade_type == "BUY" else high, "BUY", symbol))
        return {
            "activate": True,
            "fill_price": round(fill, 2),
            "stop_loss": round(new_stop, 2),
            "remaining_rr": round(remaining_rr, 2),
            "reason": (
                f"Zone-touch activation: price traded into the mapped area "
                f"({zone_low:.2f}-{zone_high:.2f}) within {missed_by:.0f} pts of entry "
                f"and left it in favour; filled at the zone edge {fill:.2f} with the "
                f"planned {abs(calculate_pips(fill, new_stop, 'BUY', symbol)):.0f} pt risk preserved"
            ),
        }

    def _management_phase(self, status: str, sl_moved_to_entry: bool, partial_close: bool, pnl_points: float) -> str:
        if status == "TP1_HIT" or partial_close:
            return "POST_TP1_TRAILING" if sl_moved_to_entry else "POST_TP1"
        if pnl_points > 0:
            return "IN_PROFIT"
        if pnl_points < 0:
            return "DEFENSIVE"
        return "INITIAL"

    def _exit_warning(self, trade_type: str, entry: float, stop_loss: float, tp1: float, current_price: float, pnl_points: float) -> str | None:
        if not stop_loss or not entry:
            return None
        risk = abs(entry - stop_loss)
        if risk <= 0:
            return None
        adverse_distance = abs(current_price - stop_loss)
        if adverse_distance <= risk * 0.25:
            return "NEAR_STOP_LOSS"
        if pnl_points < -risk * 0.65:
            return "ADVERSE_MOVE_DEEP"
        # If trade moved more than halfway to TP1 then returned close to entry.
        if tp1 and abs(current_price - entry) <= risk * 0.15:
            return None
        return None

    def _objective_alignment(self, trade: Dict[str, Any]) -> str:
        snapshot = self._trade_snapshot(trade)
        plan = (snapshot.get("session_plan") or {}) if isinstance(snapshot, dict) else {}
        alignment = str((plan or {}).get("objective_alignment") or "").upper()
        if alignment:
            return alignment
        objective_dir = str((plan or {}).get("market_objective_direction") or "").upper()
        trade_type = str(trade.get("type") or trade.get("side") or "").upper()
        if objective_dir in {"BUY", "SELL"} and trade_type in {"BUY", "SELL"}:
            return "ALIGNED_WITH_MARKET_OBJECTIVE" if objective_dir == trade_type else "COUNTER_OBJECTIVE_REVERSAL_CONFIRMED"
        return ""

    def _continuation_trigger_against_trade(
        self,
        trade_type: str,
        recent_candles: List[Dict[str, Any]] | None,
        symbol: str,
    ) -> str | None:
        if not recent_candles or len(recent_candles) < 2:
            return None
        prev = recent_candles[-2] if isinstance(recent_candles[-2], dict) else {}
        last = recent_candles[-1] if isinstance(recent_candles[-1], dict) else {}
        prev_high = self._f(prev.get("high"), 0.0)
        prev_low = self._f(prev.get("low"), 0.0)
        prev_close = self._f(prev.get("close"), 0.0)
        last_open = self._f(last.get("open"), 0.0)
        last_close = self._f(last.get("close"), 0.0)
        if prev_high <= 0 or prev_low <= 0 or last_close <= 0 or last_open <= 0:
            return None
        reclaim = points_to_price(self.thesis_exit_reclaim_points, symbol)
        if trade_type == "SELL":
            if last_close > prev_high + reclaim and last_close > prev_close and last_close > last_open:
                return "bullish continuation reclaimed the breakdown"
        else:
            if last_close < prev_low - reclaim and last_close < prev_close and last_close < last_open:
                return "bearish continuation reclaimed the breakout"
        return None

    def _opposing_poi_levels(self, trade: Dict[str, Any], trade_type: str) -> List[tuple[str, float]]:
        snapshot = self._trade_snapshot(trade)
        plan = (snapshot.get("session_plan") or {}) if isinstance(snapshot, dict) else {}
        if not isinstance(plan, dict):
            return []
        liquidity_map = (plan.get("liquidity_map") or {}) if isinstance(plan.get("liquidity_map"), dict) else {}
        raw_levels: List[tuple[str, Any]] = []
        if trade_type == "SELL":
            raw_levels.extend([
                ("target_liquidity", plan.get("target_liquidity")),
                ("previous_day_low", liquidity_map.get("previous_day_low")),
                ("session_low", liquidity_map.get("session_low")),
            ])
        else:
            raw_levels.extend([
                ("target_liquidity", plan.get("target_liquidity")),
                ("previous_day_high", liquidity_map.get("previous_day_high")),
                ("session_high", liquidity_map.get("session_high")),
            ])
        levels: List[tuple[str, float]] = []
        seen: set[float] = set()
        for label, value in raw_levels:
            price = self._f(value, 0.0)
            if price <= 0:
                continue
            marker = round(price, 2)
            if marker in seen:
                continue
            seen.add(marker)
            levels.append((label, marker))
        return levels

    def _poi_exit_held_by_agents(
        self,
        vote: Dict[str, Any],
        hold_on_silence: bool,
        defended: bool,
        label: str,
        level: float,
    ) -> Dict[str, Any] | None:
        """Refuse a POI-rejection exit the agent book does not support.

        Returns a hold verdict, or None to let the exit proceed. Silence is
        only honoured when a book was actually supplied: an absent book
        (`available` False) keeps the legacy behaviour rather than changing
        outcomes on evidence nobody gathered.
        """
        if defended:
            names = ", ".join(vote.get("defenders") or [])
            return {
                "exit_now": False,
                "scale_out": False,
                "kind": "OPPOSING_POI_VETOED_BY_AGENTS",
                "reason": (
                    f"Thesis exit held: price rejected {label} near {level:.2f}, but "
                    f"{len(vote.get('defenders') or [])} qualified agents ({names}) "
                    f"still support the trade"
                ),
                "agent_vote": vote,
            }
        if hold_on_silence:
            return {
                "exit_now": False,
                "scale_out": False,
                "kind": "OPPOSING_POI_HELD_SILENT_BOOK",
                "reason": (
                    f"Thesis exit held: price rejected {label} near {level:.2f}, but the "
                    f"agent book is undecided ({len(vote.get('defenders') or [])} defending, "
                    f"{len(vote.get('opponents') or [])} opposing) and silent_action is "
                    f"HOLD; the existing stop still governs"
                ),
                "agent_vote": vote,
            }
        return None

    def _opposing_poi_exit_review(
        self,
        trade: Dict[str, Any],
        *,
        trade_type: str,
        symbol: str,
        current_price: float,
        recent_candles: List[Dict[str, Any]] | None,
        entry: float,
        tp1: float,
        partial_close: bool,
        agent_details: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Close or reduce when price rejects an opposing point of interest.

        This path is subject to the same agent rule as the candle trigger:
        a POI rejection is one piece of evidence, and one piece of evidence
        may not close a position on its own.

        Under ``silent_action = HOLD`` an undecided book holds the trade here
        too. Without that, switching the setting would have silenced the
        candle path while this one kept closing positions unopposed -- the
        rule would look applied and not be.
        """
        if not self.thesis_exit_opposing_poi_enabled or not recent_candles or len(recent_candles) < 2:
            return {"exit_now": False, "scale_out": False}
        if tp1 > 0:
            progress_pct = self._progress_to_tp1(trade_type, entry, tp1, current_price) * 100.0 if entry > 0 else 0.0
            if progress_pct >= 100.0:
                return {"exit_now": False, "scale_out": False}
        prev = recent_candles[-2] if isinstance(recent_candles[-2], dict) else {}
        last = recent_candles[-1] if isinstance(recent_candles[-1], dict) else {}
        prev_close = self._f(prev.get("close"), 0.0)
        last_open = self._f(last.get("open"), 0.0)
        last_close = self._f(last.get("close"), 0.0)
        if last_close <= 0 or last_open <= 0:
            return {"exit_now": False, "scale_out": False}
        touch_buffer = points_to_price(self.thesis_exit_opposing_poi_buffer_points, symbol)
        reclaim = points_to_price(self.thesis_exit_opposing_poi_reclaim_points, symbol)
        alignment = self._objective_alignment(trade)
        # Ask the book once, before walking the levels.
        poi_vote = self._agent_exit_vote(agent_details, trade_type)
        hold_on_silence = (
            self.thesis_exit_silent_action == "HOLD"
            and poi_vote.get("available")
            and str(poi_vote.get("verdict")) == "SILENT"
        )
        defended = poi_vote.get("available") and str(poi_vote.get("verdict")) == "DEFEND"
        for label, level in self._opposing_poi_levels(trade, trade_type):
            if trade_type == "SELL":
                touched = self._f(last.get("low"), 0.0) <= level + touch_buffer
                rejected = touched and last_close > level + reclaim and last_close > prev_close and last_close > last_open
                if rejected:
                    held = self._poi_exit_held_by_agents(
                        poi_vote, hold_on_silence, defended, label, level
                    )
                    if held:
                        return held
                    if alignment == "ALIGNED_WITH_MARKET_OBJECTIVE" and not partial_close:
                        return {
                            "exit_now": False,
                            "scale_out": True,
                            "reason": f"Automatic thesis scale-out: opposing BUY POI rejection from {label} near {level:.2f}",
                            "kind": "OPPOSING_POI_SCALE_OUT",
                        }
                    return {
                        "exit_now": True,
                        "scale_out": False,
                        "reason": f"Automatic thesis exit: opposing BUY POI rejection from {label} near {level:.2f}",
                        "kind": "OPPOSING_POI_REJECTION",
                    }
            else:
                touched = self._f(last.get("high"), 0.0) >= level - touch_buffer
                rejected = touched and last_close < level - reclaim and last_close < prev_close and last_close < last_open
                if rejected:
                    held = self._poi_exit_held_by_agents(
                        poi_vote, hold_on_silence, defended, label, level
                    )
                    if held:
                        return held
                    if alignment == "ALIGNED_WITH_MARKET_OBJECTIVE" and not partial_close:
                        return {
                            "exit_now": False,
                            "scale_out": True,
                            "reason": f"Automatic thesis scale-out: opposing SELL POI rejection from {label} near {level:.2f}",
                            "kind": "OPPOSING_POI_SCALE_OUT",
                        }
                    return {
                        "exit_now": True,
                        "scale_out": False,
                        "reason": f"Automatic thesis exit: opposing SELL POI rejection from {label} near {level:.2f}",
                        "kind": "OPPOSING_POI_REJECTION",
                    }
        return {"exit_now": False, "scale_out": False}

    AGENT_VOTE_AGENTS = ("technical", "classical", "smc", "price_action", "multitimeframe")

    def _agent_exit_vote(self, agent_details: Dict[str, Any] | None, trade_type: str) -> Dict[str, Any]:
        """Ask the live agent book whether the trade's thesis still stands.

        Returns one of three verdicts:

          CONFIRM  the book agrees the trade is finished
          DEFEND   qualified agents still argue the trade's own direction
          SILENT   no usable majority either way (also when no book is given)

        SILENT is deliberately the fallback for a missing or empty book, so a
        caller that cannot supply agents behaves exactly as before.
        """
        if not self.thesis_exit_agent_vote_enabled or not isinstance(agent_details, dict) or not agent_details:
            return {"verdict": "SILENT", "available": False, "defenders": [], "opponents": []}

        opposite = "BUY" if trade_type == "SELL" else "SELL"
        defenders: List[str] = []
        opponents: List[str] = []
        for name in self.AGENT_VOTE_AGENTS:
            detail = agent_details.get(name)
            if not isinstance(detail, dict):
                continue
            direction = str(detail.get("direction") or detail.get("signal") or "WAIT").upper()
            if self._f(detail.get("confidence"), 0.0) < self.thesis_exit_agent_min_confidence:
                continue
            if direction == trade_type:
                defenders.append(name)
            elif direction == opposite:
                opponents.append(name)

        if len(defenders) >= self.thesis_exit_min_defenders and len(defenders) > len(opponents):
            verdict = "DEFEND"
        elif len(opponents) >= self.thesis_exit_min_opponents and not defenders:
            verdict = "CONFIRM"
        else:
            verdict = "SILENT"
        return {
            "verdict": verdict,
            "available": True,
            "defenders": defenders,
            "opponents": opponents,
        }

    def _thesis_exit_review(
        self,
        trade: Dict[str, Any],
        *,
        trade_type: str,
        symbol: str,
        current_price: float,
        recent_candles: List[Dict[str, Any]] | None,
        hours_open: float,
        pnl_points: float,
        max_favorable_excursion: float,
        tp1: float,
        entry: float,
        partial_close: bool,
        agent_details: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        if not self.thesis_exit_enabled or trade_type not in {"BUY", "SELL"}:
            return {"exit_now": False, "scale_out": False}
        opposite_continuation = self._continuation_trigger_against_trade(trade_type, recent_candles, symbol)
        if opposite_continuation:
            vote = self._agent_exit_vote(agent_details, trade_type)
            verdict = str(vote.get("verdict"))
            defenders = vote.get("defenders") or []
            opponents = vote.get("opponents") or []

            # The agents still argue the trade's own case. On 2026-07-30 this
            # was Classical 71, SMC 90 and Multi-Timeframe 83 defending a SELL
            # that the candle rule closed for -39.2 -- a trade the planner
            # then republished as its A+ map of the day, and which the market
            # went on to pay.
            if verdict == "DEFEND":
                return {
                    "exit_now": False,
                    "scale_out": False,
                    "kind": "OPPOSITE_CONTINUATION_VETOED_BY_AGENTS",
                    "reason": (
                        f"Thesis exit held: {len(defenders)} qualified agents "
                        f"({', '.join(defenders)}) still support the {trade_type}"
                    ),
                    "agent_vote": vote,
                }

            # An absent book is not a silent book.
            #
            # `available` is False when no agent read was supplied at all --
            # the emergency run_trade_updates.py path, older callers, tests
            # that predate this vote. Softening the exit there would change
            # behaviour on the strength of evidence nobody gathered, so the
            # legacy full exit stands. Scaling is reserved for the case the
            # operator actually chose: agents consulted, and undecided.
            if not vote.get("available"):
                return {
                    "exit_now": True,
                    "reason": f"Automatic thesis exit: {opposite_continuation}",
                    "kind": "OPPOSITE_CONTINUATION",
                    "agent_vote": vote,
                }

            # Already scaled once on this trigger. A candle that keeps
            # printing the same shape is not new evidence, and repeating the
            # reduction would grind the position away cycle by cycle -- worse
            # than the full exit it was meant to soften. Only a CHANGE in the
            # agent book escalates from here.
            if verdict == "SILENT" and partial_close:
                return {
                    "exit_now": False,
                    "scale_out": False,
                    "kind": "OPPOSITE_CONTINUATION_ALREADY_SCALED",
                    "reason": (
                        "Thesis exit already scaled this position; the stop sits at "
                        "breakeven and the agent book has not changed"
                    ),
                    "agent_vote": vote,
                }

            if verdict == "SILENT" and self.thesis_exit_silent_action == "HOLD":
                # The weakest verdict must not close anything.
                #
                # SILENT means the qualified agents neither confirmed the exit
                # nor defended the trade -- the book has no majority either
                # way. Under HOLD that absence of evidence changes nothing:
                # the position keeps running on the stop it already has.
                #
                # This branch is checked BEFORE the SCALE_OUT branch and
                # before the CONFIRM fall-through beneath it. Without it,
                # setting silent_action to anything other than "SCALE_OUT"
                # dropped SILENT straight into the full-exit path at the
                # bottom of this block -- so the configuration meant to make
                # the system gentler would have made it close every position
                # the candle rule fired on, whatever the agents said.
                #
                # The candle trigger alone is one piece of evidence. On
                # 2026-07-30 it closed a SELL for -39.2 while Classical 71,
                # SMC 90 and Multi-Timeframe 83 still argued the trade, and
                # the planner republished that same zone as its A+ map hours
                # later. CONFIRM (two qualified opponents, no defender) is
                # still honoured below; only the undecided case holds.
                return {
                    "exit_now": False,
                    "scale_out": False,
                    "kind": "OPPOSITE_CONTINUATION_HELD_SILENT_BOOK",
                    "reason": (
                        f"Thesis exit held: {opposite_continuation}, but the agent "
                        f"book is undecided ({len(defenders)} defending, "
                        f"{len(opponents)} opposing) and silent_action is HOLD; "
                        f"the existing stop still governs"
                    ),
                    "agent_vote": vote,
                }

            if verdict == "SILENT" and self.thesis_exit_silent_action == "SCALE_OUT":
                # Never scale a position that is under water.
                #
                # Scaling out carries the stop to breakeven. On a losing trade
                # breakeven sits on the WRONG side of the market, so the move
                # does not protect anything -- it executes the position at
                # once. On 2026-07-31 a SELL entered at 4074.78 was scaled at
                # 4081.45, 67 points offside; the stop jumped from 4089.78 to
                # 4074.78 and was taken out by the very next tick at 4081.71.
                # Price then fell to 4044: a +308 pt trade booked as -33.4.
                #
                # A SILENT book is the weakest verdict there is -- the agents
                # neither confirm nor defend. It must not be allowed to close
                # a trade the stop was still holding. Leave the original stop
                # to do its job and let the market decide.
                if pnl_points <= 0:
                    return {
                        "exit_now": False,
                        "scale_out": False,
                        "kind": "OPPOSITE_CONTINUATION_HELD_WHILE_OFFSIDE",
                        "reason": (
                            f"Thesis scale-out withheld: the position is "
                            f"{abs(pnl_points):.0f} pts offside, so moving the stop to "
                            f"breakeven would close it rather than protect it; "
                            f"the original stop still governs"
                        ),
                        "agent_vote": vote,
                    }

                # A profit too small to survive noise is not a profit worth
                # protecting -- and protecting it is what kills the trade.
                #
                # Scaling out carries the stop to breakeven. When the trade has
                # travelled only a few points, breakeven sits inside the spread
                # and ordinary noise takes it out immediately. The position is
                # closed not because the thesis failed but because it had not
                # yet moved.
                #
                # 2026-08-03, trade bdde9a5f: a BUY filled at 4067.02 was
                # scaled at 4067.63 -- SIX POINTS of open profit, 1% of the way
                # to TP1 -- the stop jumped to entry, and ten minutes later
                # price touched 4065.33 and closed the whole position flat.
                # Half was booked at +3.0 points. That is not risk management;
                # it is a coin flip charged to the account.
                #
                # `min_mfe_points` (35) already expresses this idea and already
                # guards the countertrend branch below. It was never applied
                # here, so the weakest verdict in the system -- a SILENT agent
                # book -- could close a trade that had barely left the gate.
                #
                # Measured on the trade's best excursion, not its current
                # price, so a position that genuinely ran and gave the move
                # back is still protected.
                min_travel = self.thesis_exit_min_mfe_points
                if min_travel > 0 and max_favorable_excursion < min_travel:
                    return {
                        "exit_now": False,
                        "scale_out": False,
                        "kind": "OPPOSITE_CONTINUATION_HELD_TOO_EARLY",
                        "reason": (
                            f"Thesis scale-out withheld: the position has only "
                            f"travelled {max_favorable_excursion:.0f} pts (needs "
                            f"{min_travel:.0f}), so moving the stop to breakeven "
                            f"would close it on noise rather than protect a gain"
                        ),
                        "agent_vote": vote,
                    }
                return {
                    "exit_now": False,
                    "scale_out": True,
                    "scale_fraction": self.thesis_exit_silent_scale_fraction,
                    "kind": "OPPOSITE_CONTINUATION_SCALE_OUT",
                    "reason": (
                        f"Automatic thesis scale-out: {opposite_continuation}, "
                        f"unconfirmed by the agent book"
                    ),
                    "agent_vote": vote,
                }

            reason = f"Automatic thesis exit: {opposite_continuation}"
            if verdict == "CONFIRM" and opponents:
                reason += f", confirmed by {len(opponents)} qualified agents ({', '.join(opponents)})"
            return {
                "exit_now": True,
                "reason": reason,
                "kind": "OPPOSITE_CONTINUATION",
                "agent_vote": vote,
            }
        opposing_poi = self._opposing_poi_exit_review(
            trade,
            trade_type=trade_type,
            symbol=symbol,
            current_price=current_price,
            recent_candles=recent_candles,
            entry=entry,
            tp1=tp1,
            partial_close=partial_close,
            agent_details=agent_details,
        )
        if opposing_poi.get("exit_now") or opposing_poi.get("scale_out"):
            return opposing_poi
        # A held POI rejection is a decision too: surface it so the reason is
        # visible, rather than silently falling through to the next check.
        if opposing_poi.get("kind") in {
            "OPPOSING_POI_VETOED_BY_AGENTS", "OPPOSING_POI_HELD_SILENT_BOOK",
        }:
            return opposing_poi
        alignment = self._objective_alignment(trade)
        progress_pct = self._progress_to_tp1(trade_type, entry, tp1, current_price) * 100.0 if tp1 > 0 and entry > 0 else 0.0
        if (
            alignment == "COUNTER_OBJECTIVE_REVERSAL_CONFIRMED"
            and hours_open >= (self.thesis_exit_countertrend_hold_minutes / 60.0)
            and pnl_points <= 0
            and max_favorable_excursion < self.thesis_exit_min_mfe_points
            and progress_pct < self.thesis_exit_min_progress_pct
        ):
            return {
                "exit_now": True,
                "reason": "Automatic thesis exit: counter-objective reversal failed to follow through quickly",
                "kind": "COUNTERTREND_NO_FOLLOW_THROUGH",
            }
        return {"exit_now": False, "scale_out": False}

    def _order_filled(
        self,
        order_type: str,
        trade_type: str,
        entry: float,
        current_price: float,
        candle_high: float | None = None,
        candle_low: float | None = None,
    ) -> bool:
        """True when a pending LIMIT/STOP order would fill by candle touch.

        LIMIT: price returns to a better level than market at signal time.
          BUY_LIMIT  fills when low falls to/through entry   (low <= entry)
          SELL_LIMIT fills when high rises to/through entry  (high >= entry)
        STOP: price breaks beyond entry in the trade direction.
          BUY_STOP   fills when high rises to/through entry  (high >= entry)
          SELL_STOP  fills when low falls to/through entry   (low <= entry)
        Falls back to current_price when high/low are not provided.
        """
        high = self._f(candle_high, current_price)
        low = self._f(candle_low, current_price)
        if high < low:
            high, low = low, high
        ot = str(order_type or "").upper()
        if not ot or ot.endswith("MARKET"):
            return True
        if ot == "BUY_LIMIT":
            return low <= entry
        if ot == "SELL_LIMIT":
            return high >= entry
        if ot == "BUY_STOP":
            return high >= entry
        if ot == "SELL_STOP":
            return low <= entry
        # Unknown -> infer from kind via trade direction (treat as LIMIT pullback).
        if trade_type == "BUY":
            return low <= entry
        return high >= entry

    def _evaluate_pending(
        self,
        trade,
        current_price,
        now,
        trade_type,
        entry,
        tp1,
        symbol,
        candle_high: float | None = None,
        candle_low: float | None = None,
        recent_candles: List[Dict[str, Any]] | None = None,
        news_blocked: bool = False,
        news_context: Dict[str, Any] | None = None,
        database: Any | None = None,
        market_data_source: str | None = None,
        candle_time: str | None = None,
    ):
        """Activate a pending order on touch, else keep it waiting (no PnL).

        Extra behavior:
        - If touched during a news blackout, do NOT activate.
        - Freeze the order in a news-hold state and re-check it after the block.
        - If still structurally valid and within allowed drift, convert to MARKET.
        - Otherwise cancel it safely.
        """
        order_type = str(trade.get("order_type") or trade.get("order_kind") or "").upper()
        high_price = self._f(candle_high, current_price)
        low_price = self._f(candle_low, current_price)
        recent_window_high, recent_window_low = self._recent_window_extremes(recent_candles)
        if high_price < low_price:
            high_price, low_price = low_price, high_price
        market_source = str(market_data_source or trade.get("market_data_source") or "")
        touch_source_reliable = market_source not in {"swissquote_spot_quote_fallback", "synthetic_demo", "quote"}
        candle_dt = self._parse_dt(str(candle_time or "")) if candle_time else None
        if candle_dt is not None:
            candle_age_minutes = (now - candle_dt).total_seconds() / 60.0
            if candle_age_minutes > self.pending_touch_max_candle_age_minutes:
                # A stale "latest candle" is a price-reading failure. Its
                # extremes (collapsed to the live price upstream) must not
                # activate anything: pending orders wait for a fresh bar,
                # exactly as they wait out the quote-fallback sources.
                touch_source_reliable = False
                self.logger.warning(
                    "Pending %s for %s: touch detection degraded — latest candle %s "
                    "is %.0f min old (window %.0f min); waiting for a fresh bar",
                    order_type or trade_type, trade.get("id"), candle_time,
                    candle_age_minutes, self.pending_touch_max_candle_age_minutes,
                )
        theoretical_touch = self._order_filled(order_type, trade_type, entry, current_price, high_price, low_price)
        filled_touch = theoretical_touch if touch_source_reliable else False

        # A pending order can only be filled by price action that happened
        # after it existed. When the filling candle carries its own timestamp
        # (operator directive, 2026-08-05), judge by the timestamp, not by an
        # age proxy: a bar whose window ENDED before the order existed cannot
        # fill it whatever its extremes show, and a bar that STRADDLES the
        # creation moment is judged by the live price alone, because its
        # extreme may belong to the minutes before the order.
        if filled_touch and candle_dt is not None:
            created = self._parse_dt(str(trade.get("created_at") or trade.get("entry_time") or ""))
            if created is not None:
                candle_end = candle_dt + timedelta(minutes=self.pending_touch_bar_minutes)
                predates = candle_end <= created
                straddles = candle_dt < created < candle_end
                if predates or straddles:
                    live_touch = self._order_filled(
                        order_type, trade_type, entry, current_price, current_price, current_price
                    )
                    if not live_touch:
                        self.logger.info(
                            "Pending %s for %s not activated: the filling candle %s "
                            "%s the order (created %s); only the live price may fill it "
                            "(entry %.2f, price now %.2f)",
                            order_type or trade_type, trade.get("id"), candle_time,
                            "predates" if predates else "straddles", created.isoformat(),
                            entry, current_price,
                        )
                        filled_touch = False
        elif filled_touch and not self._touch_is_after_creation(trade, now):
            # Legacy path: no candle timestamp available. Age is the only
            # proxy: once the order has outlived the bar interval, any extreme
            # in the current bar is necessarily from after it was placed.
            # Below that age, the live price alone decides. (The live case:
            # BUY STOP at 4028.77 filled 111 points below its trigger by a
            # high printed earlier in the same bar.)
            live_touch = self._order_filled(
                order_type, trade_type, entry, current_price, current_price, current_price
            )
            if not live_touch:
                self.logger.info(
                    "Pending %s for %s not activated: the bar's extreme predates the order "
                    "(entry %.2f, price now %.2f)",
                    order_type or trade_type, trade.get("id"), entry, current_price,
                )
                filled_touch = False
        base_updates = {
            "current_price": round(current_price, 2),
            "last_candle_high": round(high_price, 2),
            "last_candle_low": round(low_price, 2),
            "recent_30m_high": round(recent_window_high, 2) if recent_window_high is not None else None,
            "recent_30m_low": round(recent_window_low, 2) if recent_window_low is not None else None,
            "last_updated": self._iso(now),
            "market_data_source": market_source or None,
        }
        dist_pts = abs(calculate_pips(current_price, entry, trade_type, symbol))
        hours_open = self._hours_open(trade, now)

        snapshot = trade.get("signal_snapshot") or {}
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except Exception:
                snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        runtime = dict(snapshot.get("pending_runtime") or {})
        hold_active = bool(runtime.get("news_hold_active", False))
        touch_time = self._parse_dt(str(runtime.get("touch_time") or ""))
        stop_loss = self._f(trade.get("stop_loss"), 0.0)
        tp1_price = self._f(trade.get("tp1"), 0.0)
        tp2 = self._f(trade.get("tp2"), 0.0)
        min_rr_ratio = float((self.config.get("risk_settings", {}) or {}).get("min_rr_ratio", 1.5) or 1.5)
        recent_trades = database.get_recent_trades(limit=50) if database and hasattr(database, "get_recent_trades") else []

        def _persist_runtime(**kwargs):
            runtime.update(kwargs)
            snapshot["pending_runtime"] = runtime
            base_updates["signal_snapshot"] = snapshot

        # Anchor the market price at the moment this pending order was first
        # evaluated. Staleness is measured against this anchor, so it survives
        # across cycles and cannot be confused with distance-to-entry.
        _persist_runtime(
            touch_detection_source=market_source or None,
            touch_detection_source_reliable=touch_source_reliable,
            touch_detection_waiting_for_reliable_ohlc=bool(theoretical_touch and not touch_source_reliable),
            creation_price=self._f(runtime.get("creation_price"), 0.0) or round(current_price, 2),
        )

        def _invalidated() -> bool:
            if stop_loss <= 0:
                return False
            if trade_type == "BUY":
                return current_price <= stop_loss
            return current_price >= stop_loss

        def _rr_ok(market_entry: float) -> bool:
            if not self.pending_news_require_rr_recheck:
                return True
            if stop_loss <= 0 or tp2 <= 0:
                return True
            risk = abs(stop_loss - market_entry)
            reward = abs(tp2 - market_entry)
            if risk <= 0:
                return False
            return (reward / risk) >= min_rr_ratio

        def _drift_limit_points() -> float:
            if order_type.endswith("LIMIT"):
                return self.pending_news_limit_max_drift_points
            if order_type.endswith("STOP"):
                return self.pending_news_stop_max_drift_points
            return self.pending_news_limit_max_drift_points

        def _delay_elapsed() -> bool:
            if not touch_time:
                return True
            mins = (now - touch_time).total_seconds() / 60.0
            return mins >= self.pending_news_reactivation_delay_minutes

        def _conversion_allowed(market_entry: float) -> tuple[bool, str | None]:
            review = self.pending_governor.allow_market_conversion(
                trade,
                recent_trades,
                current_price=market_entry,
                now=now,
            )
            return bool(review.get("allow", True)), (str(review.get("reason")) if review.get("reason") else None)

        def _pending_freshness() -> tuple[str, bool, list[str], float, float, str]:
            tz_name = str((self.config.get("schedule", {}) or {}).get("timezone") or (self.config.get("trading_hours", {}) or {}).get("timezone") or "Asia/Hebron")
            if not self.pending_freshness_enabled:
                return "FRESH", False, [], 0.0, 0.0, canonical_session_label(now, tz_name)
            created_session = str(
                runtime.get("created_session_label")
                or ((snapshot.get("session_info") or {}).get("current_session"))
                or canonical_session_label(self._parse_dt(str(trade.get("entry_time") or trade.get("created_at") or "")) or now, tz_name)
            )
            current_session = canonical_session_label(now, tz_name)
            # How far price has travelled AWAY from us since the order was
            # created. This is the staleness signal: it means the market moved
            # on without filling us.
            #
            # It must NOT be measured from the entry price. Distance-to-entry
            # is a property of where the order was placed, not of market
            # movement, so measuring it that way made every LIMIT order beyond
            # the threshold "stale" the instant it was created -- a far pullback
            # order was cancelled on its first evaluation cycle in a flat
            # market, reporting hundreds of points of movement that never
            # happened.
            creation_price = self._f(runtime.get("creation_price"), 0.0)
            if creation_price <= 0:
                creation_price = current_price
            drift_points = abs(calculate_pips(creation_price, current_price, trade_type, symbol))
            max_excursion_points = max(self._f(runtime.get("max_excursion_points"), 0.0), drift_points)
            # Distance still to travel before the order can fill. Used only for
            # target-path progress, never as a staleness trigger.
            distance_to_entry_points = max(0.0, calculate_pips(entry, current_price, trade_type, symbol))
            planned_target_points = 0.0
            for target in (tp1_price, tp2):
                if target > 0:
                    planned_target_points = abs(calculate_pips(entry, target, trade_type, symbol))
                    if planned_target_points > 0:
                        break
            # Target-path progress: how much of the planned move the market has
            # already completed without us, measured from where the order was
            # created toward the target.
            #
            # Distance-to-entry is explicitly NOT progress. For a pullback
            # LIMIT, price sitting far above a BUY entry means the move has not
            # started, yet treating that gap as "62% of the path covered"
            # cancelled the order for the very setup it was waiting for.
            path_travelled_points = 0.0
            if planned_target_points > 0:
                first_target = next((t for t in (tp1_price, tp2) if t > 0), 0.0)
                if first_target > 0:
                    path_travelled_points = max(0.0, calculate_pips(creation_price, current_price, trade_type, symbol))
            progress_pct = (path_travelled_points / planned_target_points * 100.0) if planned_target_points > 0 else 0.0
            plan = snapshot.get("session_plan") or {}
            plan_expiry = self._parse_dt(str((plan.get("plan_expires_at") if isinstance(plan, dict) else None) or runtime.get("plan_expires_at") or ""))
            reasons: List[str] = []
            state = "FRESH"
            revalidation_required = False
            if plan_expiry and now >= plan_expiry:
                state = "REVALIDATION_REQUIRED"
                revalidation_required = True
                reasons.append("session plan expired")
            # A session rollover used to mark the order REVALIDATION_REQUIRED,
            # which the cancellation branch below treats as a death sentence.
            #
            # On 2026-07-29 that killed a SELL LIMIT 42 minutes after it was
            # placed: 103 points from activation, 0% of the target path
            # covered, six hours of allowance untouched. Its only offence was
            # that the clock crossed from "London + New York Afternoon" into
            # "New York Evening". Price later traded back to within four
            # points of the entry that no longer existed.
            #
            # Staleness is a statement about the market, not about the hour.
            # The three checks below measure it directly -- waiting time, how
            # far price travelled without filling, and how much of the planned
            # path was covered -- and an expired plan is handled above. A
            # boundary on the clock adds nothing they do not already cover.
            elif hours_open >= self.pending_freshness_stale_after_hours:
                state = "STALE"
                revalidation_required = True
                reasons.append(f"waiting too long ({hours_open:.1f}h)")
            elif max_excursion_points >= self.pending_freshness_stale_after_excursion_points:
                state = "STALE"
                revalidation_required = True
                # Operator 2026-08-07: state the baseline explicitly. The
                # excursion is measured from the CREATION price (market
                # movement), not from the entry (order depth) -- the card
                # reader must see both numbers to avoid reading a
                # contradiction (368-pts-deep order vs 256-pts movement).
                born_gap = abs(calculate_pips(entry, creation_price, trade_type, symbol))
                reasons.append(
                    f"market moved {max_excursion_points:.0f} pts from the "
                    f"{creation_price:.2f} creation price without fill; the order "
                    f"was born {born_gap:.0f} pts from entry and is still "
                    f"{distance_to_entry_points:.0f} pts away"
                )
            elif progress_pct >= self.pending_freshness_stale_after_target_progress_pct:
                state = "STALE"
                revalidation_required = True
                reasons.append(f"market covered {progress_pct:.0f}% of target path without fill")
            elif (
                hours_open >= self.pending_freshness_aging_after_hours
                or max_excursion_points >= self.pending_freshness_stale_after_excursion_points * 0.5
                or progress_pct >= self.pending_freshness_stale_after_target_progress_pct * 0.5
            ):
                state = "AGING"
                if hours_open >= self.pending_freshness_aging_after_hours:
                    reasons.append(f"waiting {hours_open:.1f}h")
                if max_excursion_points >= self.pending_freshness_stale_after_excursion_points * 0.5:
                    reasons.append(f"market already moved {max_excursion_points:.0f} pts")
                if progress_pct >= self.pending_freshness_stale_after_target_progress_pct * 0.5:
                    reasons.append(f"market covered {progress_pct:.0f}% of target path")
            return state, revalidation_required, reasons, round(max_excursion_points, 1), round(min(progress_pct, 999.0), 1), current_session

        freshness_state, revalidation_required, freshness_reasons, max_excursion_points, target_progress_pct, current_session_label = _pending_freshness()
        _persist_runtime(
            created_session_label=str(
                runtime.get("created_session_label")
                or ((snapshot.get("session_info") or {}).get("current_session"))
                or current_session_label
            ),
            last_session_label=current_session_label,
            freshness_state=freshness_state,
            revalidation_required=revalidation_required,
            freshness_reasons=freshness_reasons,
            max_excursion_points=max_excursion_points,
            target_progress_pct=target_progress_pct,
            plan_expires_at=str(((snapshot.get("session_plan") or {}).get("plan_expires_at")) or runtime.get("plan_expires_at") or ""),
        )
        planner_pending = self._has_planner_pending_context(trade)
        planned_rr = self._planned_rr_value(trade)

        def _late_touch_required() -> bool:
            return self.pending_touch_revalidation_enabled and freshness_state in {"STALE", "REVALIDATION_REQUIRED"}

        def _late_touch_review(market_entry: float) -> tuple[bool, str | None]:
            if not _late_touch_required():
                return True, None
            reasons: List[str] = []
            drift_pts = abs(calculate_pips(market_entry, entry, trade_type, symbol))
            confirm_threshold = points_to_price(self.pending_touch_revalidation_min_confirmation_points, symbol)
            if order_type.endswith("STOP"):
                drift_limit = self.pending_touch_revalidation_stop_max_drift_points
            else:
                drift_limit = self.pending_touch_revalidation_limit_max_drift_points
            if trade_type == "SELL":
                confirmed = market_entry <= entry - confirm_threshold
            else:
                confirmed = market_entry >= entry + confirm_threshold
            if not confirmed:
                reasons.append("late touch lacked fresh confirmation")
            if drift_pts > drift_limit:
                reasons.append(f"late touch drift {drift_pts:.0f} pts exceeded {drift_limit:.0f} pts")
            if _invalidated():
                reasons.append("structure invalidated before delayed activation")
            if not _rr_ok(market_entry):
                reasons.append("RR degraded after delayed touch")
            if reasons:
                return False, "; ".join(reasons)
            return True, f"Delayed touch revalidated ({freshness_state})"

        # Hard expiry for stale pending orders.
        if not filled_touch and self.pending_expire_after_hours > 0 and hours_open >= self.pending_expire_after_hours:
            base_updates.update({
                "status": "EXPIRED",
                "result": "EXPIRED",
                "closed_at": self._iso(now),
                "close_time": self._iso(now),
            })
            return {
                "trade_id": trade.get("id"),
                "old_status": "PENDING",
                "new_status": "EXPIRED",
                "pnl_points": 0.0,
                "events": ["EXPIRED"],
                "updates": base_updates,
                "progress_to_tp1": 0.0,
                "hours_open": hours_open,
                "pending_distance_points": dist_pts,
            }

        opposite_plan_reason = None
        if not filled_touch and planner_pending:
            opposite_plan_reason = self._opposite_ready_plan_cancel_reason(
                trade,
                database=database,
                symbol=symbol,
                trade_type=trade_type,
            )
        if opposite_plan_reason:
            _persist_runtime(
                cancelled_by_opposite_ready_plan=True,
                opposite_ready_plan_cancel_reason=opposite_plan_reason,
                opposite_ready_plan_cancelled_at=self._iso(now),
            )
            base_updates.update({
                "status": "CANCELLED",
                "result": "CANCELLED",
                "closed_at": self._iso(now),
                "close_time": self._iso(now),
                "reasons": [f"Planner pending cancelled by opposite plan guard: {opposite_plan_reason}"],
            })
            return {
                "trade_id": trade.get("id"),
                "old_status": "PENDING",
                "new_status": "CANCELLED",
                "pnl_points": 0.0,
                "events": ["PENDING_CANCELLED"],
                "cancel_reason_code": "OPPOSITE_PLAN_GUARD",
                "updates": base_updates,
                "progress_to_tp1": 0.0,
                "hours_open": hours_open,
                "pending_distance_points": dist_pts,
            }

        # Professional planner hygiene: if a mapped pending order is already
        # structurally stale, do not leave it hanging until a late touch or a
        # 24h expiry. Once the market already showed the map is old, cancel it.
        if not filled_touch and planner_pending and freshness_state in {"STALE", "REVALIDATION_REQUIRED"}:
            stale_reason = "; ".join(str(x) for x in freshness_reasons if str(x).strip()) or f"freshness state {freshness_state.lower()}"
            _persist_runtime(
                cancelled_as_stale=True,
                stale_cancel_reason=stale_reason,
                stale_cancelled_at=self._iso(now),
            )
            base_updates.update({
                "status": "CANCELLED",
                "result": "CANCELLED",
                "closed_at": self._iso(now),
                "close_time": self._iso(now),
                "reasons": [f"Planner pending cancelled as stale: {stale_reason}"],
            })
            return {
                "trade_id": trade.get("id"),
                "old_status": "PENDING",
                "new_status": "CANCELLED",
                "pnl_points": 0.0,
                "events": ["PENDING_CANCELLED"],
                "cancel_reason_code": "PLAN_STALE",
                "updates": base_updates,
                "progress_to_tp1": 0.0,
                "hours_open": hours_open,
                "pending_distance_points": dist_pts,
            }

        # Safety kill-switch: a planner pending order with sub-minimum RR must
        # not stay live even if it slipped in from an older/legacy map.
        if not filled_touch and planner_pending and planned_rr > 0 and planned_rr < min_rr_ratio:
            rr_reason = f"planned RR {planned_rr:.2f} below minimum {min_rr_ratio:.2f}"
            _persist_runtime(
                cancelled_for_low_rr=True,
                low_rr_cancel_reason=rr_reason,
                low_rr_cancelled_at=self._iso(now),
            )
            base_updates.update({
                "status": "CANCELLED",
                "result": "CANCELLED",
                "closed_at": self._iso(now),
                "close_time": self._iso(now),
                "reasons": [f"Planner pending cancelled by RR guard: {rr_reason}"],
            })
            return {
                "trade_id": trade.get("id"),
                "old_status": "PENDING",
                "new_status": "CANCELLED",
                "pnl_points": 0.0,
                "events": ["PENDING_CANCELLED"],
                "cancel_reason_code": "RR_BELOW_MINIMUM",
                "updates": base_updates,
                "progress_to_tp1": 0.0,
                "hours_open": hours_open,
                "pending_distance_points": dist_pts,
            }

        # Zone-touch activation runs before the near-miss path. Both answer
        # "price came close but did not fill", but this one fills at the edge
        # of the mapped area with the planned risk intact, while near-miss
        # converts to market and therefore has to refuse anything that would
        # widen risk. When price genuinely traded inside the published zone,
        # the zone edge is the truer price.
        if (not filled_touch) and (not news_blocked) and freshness_state in {"FRESH", "AGING"}:
            zone_review = self._zone_touch_review(
                trade,
                trade_type=trade_type,
                order_type=order_type,
                entry=entry,
                stop_loss=stop_loss,
                tp2=tp2,
                current_price=current_price,
                candle_high=high_price,
                candle_low=low_price,
                recent_window_high=recent_window_high,
                recent_window_low=recent_window_low,
                symbol=symbol,
                runtime=runtime,
                now=now,
            )
            # Remember the visit and the departure. The grace period is
            # measured from the first time price was seen inside the area, and
            # a departure must survive the cycle that observed it -- otherwise
            # a pullback would erase an activation that had already earned
            # itself between two five-minute checks.
            _zone_low, _zone_high = self._entry_zone_bounds(trade)
            if _zone_low > 0 and _zone_high > 0:
                _inside = low_price <= _zone_high and high_price >= _zone_low
                if _inside and not runtime.get("zone_first_touch_at"):
                    _persist_runtime(zone_first_touch_at=self._iso(now))
                _left = (high_price > _zone_high) if trade_type == "BUY" else (low_price < _zone_low)
                if _left and runtime.get("zone_first_touch_at") and not runtime.get("zone_left_in_favour"):
                    _persist_runtime(
                        zone_left_in_favour=True,
                        zone_left_at=self._iso(now),
                        zone_left_edge=round(_zone_high if trade_type == "BUY" else _zone_low, 2),
                    )
            if zone_review.get("activate"):
                allowed, gov_reason = _conversion_allowed(current_price)
                if allowed:
                    fill_price = self._f(zone_review.get("fill_price"), 0.0)
                    new_stop = self._f(zone_review.get("stop_loss"), 0.0)
                    activation_reason = str(zone_review.get("reason") or "Zone-touch activation")
                    _persist_runtime(
                        zone_touch_activation=True,
                        zone_touch_reason=activation_reason,
                        zone_touch_fill_price=fill_price,
                        zone_touch_original_stop=round(stop_loss, 2),
                        zone_touch_remaining_rr=zone_review.get("remaining_rr"),
                        activation_reason=activation_reason,
                    )
                    base_updates.update({
                        "status": "OPEN",
                        "entry_time": self._iso(now),
                        "entry_price": fill_price,
                        "stop_loss": new_stop,
                        "current_pnl": 0,
                        "current_pnl_points": 0,
                        "pending_cycles": 0,
                        "activation_reason": activation_reason,
                    })
                    self.logger.info(
                        "Zone-touch activation for %s: filled at %.2f, stop moved %.2f -> %.2f "
                        "to preserve the planned risk",
                        trade.get("id"), fill_price, stop_loss, new_stop,
                    )
                    return {
                        "trade_id": trade.get("id"),
                        "old_status": "PENDING",
                        "new_status": "OPEN",
                        "pnl_points": 0.0,
                        "events": ["ORDER_FILLED"],
                        "updates": base_updates,
                        "progress_to_tp1": 0.0,
                        "hours_open": 0.0,
                        "pending_distance_points": 0.0,
                    }
                _persist_runtime(zone_touch_blocked_reason=gov_reason)

        if (not filled_touch) and (not news_blocked) and freshness_state in {"FRESH", "AGING"}:
            near_miss_ok, near_miss_reason, near_miss_halo = self._near_miss_review(
                trade,
                trade_type=trade_type,
                order_type=order_type,
                entry=entry,
                stop_loss=stop_loss,
                tp2=tp2,
                current_price=current_price,
                high_price=high_price,
                low_price=low_price,
                recent_window_high=recent_window_high,
                recent_window_low=recent_window_low,
                symbol=symbol,
                target_progress_pct=target_progress_pct,
            )
            if near_miss_ok:
                allowed, reason = _conversion_allowed(current_price)
                if allowed:
                    # Re-pricing the entry must not re-price the risk.
                    #
                    # The stop travels with the fill so the planned distance
                    # is exactly preserved. It only ever moves in the safer
                    # direction here: the conversion happens after price has
                    # moved AWAY from the entry in the trade's favour, so the
                    # new stop is nearer the market than the mapped one, never
                    # further.
                    converted_stop = stop_loss
                    planned_risk = abs(entry - stop_loss)
                    if self.near_miss_preserve_planned_risk and planned_risk > 0:
                        candidate = (
                            current_price + planned_risk if trade_type == "SELL"
                            else current_price - planned_risk
                        )
                        # Never widen: if the mapped stop is already tighter
                        # than the planned distance would allow, keep it.
                        if trade_type == "SELL":
                            converted_stop = min(stop_loss, candidate)
                        else:
                            converted_stop = max(stop_loss, candidate)
                        if abs(converted_stop - stop_loss) >= 0.01:
                            self.logger.info(
                                "Near-miss conversion for %s: entry re-priced %.2f -> "
                                "%.2f, stop carried %.2f -> %.2f so the planned %.0f pt "
                                "risk is preserved",
                                trade.get("id"), entry, current_price,
                                stop_loss, converted_stop,
                                abs(calculate_pips(entry, stop_loss, trade_type, symbol)),
                            )
                    _persist_runtime(
                        near_miss_activation=True,
                        near_miss_reason=near_miss_reason,
                        near_miss_halo_points=near_miss_halo,
                        activation_reason=near_miss_reason,
                        near_miss_planned_risk_preserved=bool(
                            self.near_miss_preserve_planned_risk
                        ),
                    )
                    base_updates.update({
                        "status": "OPEN",
                        "entry_time": self._iso(now),
                        "entry_price": round(current_price, 2),
                        "stop_loss": round(converted_stop, 2),
                        "initial_stop_loss": round(converted_stop, 2),
                        "current_pnl": 0,
                        "current_pnl_points": 0,
                        "pending_cycles": 0,
                        "activation_reason": near_miss_reason,
                    })
                    return {
                        "trade_id": trade.get("id"),
                        "old_status": "PENDING",
                        "new_status": "OPEN",
                        "pnl_points": 0.0,
                        "events": ["ORDER_FILLED"],
                        "updates": base_updates,
                        "progress_to_tp1": 0.0,
                        "hours_open": 0.0,
                        "pending_distance_points": 0.0,
                    }
                _persist_runtime(near_miss_block_reason=reason or "near_miss_conversion_blocked")

        # If the order touched during a blocked-news window, freeze it instead of activating.
        if self.pending_news_hold_enabled and filled_touch and news_blocked:
            if not hold_active:
                _persist_runtime(
                    news_hold_active=True,
                    touch_time=self._iso(now),
                    touch_price=round(current_price, 2),
                    hold_reason="news_blackout_touch",
                    blocked_context=(news_context or {}),
                )
                base_updates["pending_cycles"] = int(self._f(trade.get("pending_cycles", 0)))
                return {
                    "trade_id": trade.get("id"),
                    "old_status": "PENDING",
                    "new_status": "PENDING",
                    "pnl_points": 0.0,
                    "events": ["NEWS_HOLD"],
                    "updates": base_updates,
                    "progress_to_tp1": 0.0,
                    "hours_open": hours_open,
                    "pending_distance_points": dist_pts,
                }
            _persist_runtime(news_hold_active=True)
            return {
                "trade_id": trade.get("id"),
                "old_status": "PENDING",
                "new_status": "PENDING",
                "pnl_points": 0.0,
                "events": [],
                "updates": base_updates,
                "progress_to_tp1": 0.0,
                "hours_open": hours_open,
                "pending_distance_points": dist_pts,
            }

        # News hold release path: after the blocked window ends, light revalidation only.
        if hold_active and not news_blocked:
            if not _delay_elapsed():
                _persist_runtime(news_hold_active=True)
                return {
                    "trade_id": trade.get("id"),
                    "old_status": "PENDING",
                    "new_status": "PENDING",
                    "pnl_points": 0.0,
                    "events": [],
                    "updates": base_updates,
                    "progress_to_tp1": 0.0,
                    "hours_open": hours_open,
                    "pending_distance_points": dist_pts,
                }
            if _invalidated() or (self.pending_news_cancel_if_drift_exceeds and dist_pts > _drift_limit_points()) or not _rr_ok(current_price):
                _persist_runtime(news_hold_active=False, released_at=self._iso(now), cancelled_after_hold=True)
                base_updates.update({
                    "status": "CANCELLED",
                    "result": "CANCELLED",
                    "closed_at": self._iso(now),
                    "close_time": self._iso(now),
                })
                return {
                    "trade_id": trade.get("id"),
                    "old_status": "PENDING",
                    "new_status": "CANCELLED",
                    "pnl_points": 0.0,
                    "events": ["PENDING_CANCELLED"],
                    "cancel_reason_code": "POST_NEWS_REVALIDATION_FAILED",
                    "updates": base_updates,
                    "progress_to_tp1": 0.0,
                    "hours_open": hours_open,
                    "pending_distance_points": dist_pts,
                }
            if _late_touch_required():
                ok, late_reason = _late_touch_review(current_price)
                if not ok and self.pending_touch_revalidation_cancel_on_failed:
                    _persist_runtime(
                        news_hold_active=False,
                        released_at=self._iso(now),
                        cancelled_after_hold=True,
                        conversion_block_reason=late_reason,
                        delayed_touch_revalidation_passed=False,
                    )
                    base_updates.update({
                        "status": "CANCELLED",
                        "result": "CANCELLED",
                        "closed_at": self._iso(now),
                        "close_time": self._iso(now),
                        "reasons": [f"Delayed touch revalidation failed: {late_reason}"] if late_reason else ["Delayed touch revalidation failed"],
                    })
                    return {
                        "trade_id": trade.get("id"),
                        "old_status": "PENDING",
                        "new_status": "CANCELLED",
                        "pnl_points": 0.0,
                        "events": ["PENDING_CANCELLED"],
                        "cancel_reason_code": "LATE_TOUCH_REVALIDATION_FAILED",
                        "updates": base_updates,
                        "progress_to_tp1": 0.0,
                        "hours_open": hours_open,
                        "pending_distance_points": dist_pts,
                    }
            allowed, reason = _conversion_allowed(current_price)
            if not allowed:
                _persist_runtime(
                    news_hold_active=False,
                    released_at=self._iso(now),
                    cancelled_after_hold=True,
                    conversion_block_reason=reason,
                )
                base_updates.update({
                    "status": "CANCELLED",
                    "result": "CANCELLED",
                    "closed_at": self._iso(now),
                    "close_time": self._iso(now),
                    "reasons": [f"Market conversion blocked: {reason}"] if reason else ["Market conversion blocked"],
                })
                return {
                    "trade_id": trade.get("id"),
                    "old_status": "PENDING",
                    "new_status": "CANCELLED",
                    "pnl_points": 0.0,
                    "events": ["PENDING_CANCELLED"],
                    "cancel_reason_code": "MARKET_CONVERSION_BLOCKED",
                    "updates": base_updates,
                    "progress_to_tp1": 0.0,
                    "hours_open": hours_open,
                    "pending_distance_points": dist_pts,
                }
            _persist_runtime(
                news_hold_active=False,
                released_at=self._iso(now),
                activated_after_hold=True,
                delayed_touch_revalidation_passed=(not _late_touch_required()) or True,
                activation_reason=(late_reason if _late_touch_required() else "Post-news controlled market conversion"),
            )
            base_updates.update({
                "status": "OPEN",
                "entry_time": self._iso(now),
                "entry_price": round(current_price, 2),
                "current_pnl": 0,
                "current_pnl_points": 0,
                "pending_cycles": 0,
            })
            if _late_touch_required() and late_reason:
                base_updates["activation_reason"] = late_reason
            return {
                "trade_id": trade.get("id"),
                "old_status": "PENDING",
                "new_status": "OPEN",
                "pnl_points": 0.0,
                "events": ["ORDER_FILLED"],
                "updates": base_updates,
                "progress_to_tp1": 0.0,
                "hours_open": 0.0,
                "pending_distance_points": 0.0,
            }

        # Hybrid mode: auto-convert stale PENDING to MARKET.
        # IMPORTANT: planner / day-map pending orders must NOT be auto-promoted
        # just because a cycle counter elapsed. They should activate only by
        # real touch, explicit near-miss logic, or controlled news-hold
        # reactivation. Otherwise keep them pending until stale/opposite-plan
        # governance cancels them.
        if not filled_touch and self.entry_style == "hybrid" and self.pending_order_max_cycles > 0:
            pending_cycles = int(self._f(trade.get("pending_cycles", 0)))
            pending_cycles += 1
            if planner_pending:
                _persist_runtime(
                    auto_market_conversion_disabled_for_planner=True,
                    last_pending_cycles=pending_cycles,
                )
                base_updates["pending_cycles"] = int(pending_cycles)
            else:
                if pending_cycles >= self.pending_order_max_cycles:
                    if _late_touch_required() or freshness_state in {"STALE", "REVALIDATION_REQUIRED"}:
                        reason = "; ".join(freshness_reasons) if freshness_reasons else f"pending classified as {freshness_state}"
                        _persist_runtime(auto_conversion_block_reason=reason, auto_conversion_blocked_at=self._iso(now))
                        base_updates.update({
                            "status": "CANCELLED",
                            "result": "CANCELLED",
                            "closed_at": self._iso(now),
                            "close_time": self._iso(now),
                            "pending_cycles": 0,
                            "reasons": [f"Auto market conversion blocked: {reason}"],
                        })
                        return {
                            "trade_id": trade.get("id"),
                            "old_status": "PENDING",
                            "new_status": "CANCELLED",
                            "pnl_points": 0.0,
                            "events": ["PENDING_CANCELLED"],
                            "cancel_reason_code": "AUTO_CONVERSION_BLOCKED",
                            "updates": base_updates,
                            "progress_to_tp1": 0.0,
                            "hours_open": hours_open,
                            "pending_distance_points": dist_pts,
                        }
                    allowed, reason = _conversion_allowed(current_price)
                    if not allowed:
                        _persist_runtime(auto_conversion_block_reason=reason, auto_conversion_blocked_at=self._iso(now))
                        base_updates.update({
                            "status": "CANCELLED",
                            "result": "CANCELLED",
                            "closed_at": self._iso(now),
                            "close_time": self._iso(now),
                            "pending_cycles": 0,
                            "reasons": [f"Auto market conversion blocked: {reason}"] if reason else ["Auto market conversion blocked"],
                        })
                        return {
                            "trade_id": trade.get("id"),
                            "old_status": "PENDING",
                            "new_status": "CANCELLED",
                            "pnl_points": 0.0,
                            "events": ["PENDING_CANCELLED"],
                            "cancel_reason_code": "AUTO_CONVERSION_BLOCKED",
                            "updates": base_updates,
                            "progress_to_tp1": 0.0,
                            "hours_open": hours_open,
                            "pending_distance_points": dist_pts,
                        }
                    activation_reason = f"Auto market conversion after waiting {pending_cycles} cycles without fill"
                    _persist_runtime(auto_market_conversion_used=True, activation_reason=activation_reason)
                    base_updates.update({
                        "status": "OPEN",
                        "entry_time": self._iso(now),
                        "entry_price": round(current_price, 2),
                        "current_pnl": 0,
                        "current_pnl_points": 0,
                        "pending_cycles": 0,
                        "activation_reason": activation_reason,
                    })
                    return {
                        "trade_id": trade.get("id"),
                        "old_status": "PENDING",
                        "new_status": "OPEN",
                        "pnl_points": 0.0,
                        "events": ["ORDER_FILLED"],
                        "updates": base_updates,
                        "progress_to_tp1": 0.0,
                        "hours_open": 0.0,
                    }
                base_updates["pending_cycles"] = int(pending_cycles)

        if filled_touch:
            if _late_touch_required():
                ok, late_reason = _late_touch_review(current_price)
                if not ok and self.pending_touch_revalidation_cancel_on_failed:
                    _persist_runtime(
                        delayed_touch_revalidation_passed=False,
                        delayed_touch_revalidation_reason=late_reason,
                        cancelled_on_touch=True,
                    )
                    base_updates.update({
                        "status": "CANCELLED",
                        "result": "CANCELLED",
                        "closed_at": self._iso(now),
                        "close_time": self._iso(now),
                        "reasons": [f"Delayed touch revalidation failed: {late_reason}"] if late_reason else ["Delayed touch revalidation failed"],
                    })
                    return {
                        "trade_id": trade.get("id"),
                        "old_status": "PENDING",
                        "new_status": "CANCELLED",
                        "pnl_points": 0.0,
                        "events": ["PENDING_CANCELLED"],
                        "cancel_reason_code": "LATE_TOUCH_REVALIDATION_FAILED",
                        "updates": base_updates,
                        "progress_to_tp1": 0.0,
                        "hours_open": hours_open,
                        "pending_distance_points": dist_pts,
                    }
                _persist_runtime(
                    delayed_touch_revalidation_passed=True,
                    delayed_touch_revalidation_reason=late_reason,
                    activated_after_touch_revalidation=True,
                )
            _persist_runtime(
                # THE FILLING CANDLE, NAMED. Before this, a phantom activation
                # could not be audited: the row kept the extreme that fired it
                # but not which candle printed it. (TRADE_..._2e09b05c: filled
                # at 4090.25 while every displayed price stayed above 4093.)
                fill_candle_time=str(candle_time or "") or None,
                fill_judged_by="candle_timestamp" if candle_dt is not None else "legacy_age_proxy",
            )
            base_updates.update({
                "status": "OPEN",
                "entry_time": self._iso(now),
                "entry_price": round(current_price, 2) if _late_touch_required() else round(entry, 2),
                "current_pnl": 0,
                "current_pnl_points": 0,
            })
            if _late_touch_required() and late_reason:
                base_updates["activation_reason"] = late_reason
            return {
                "trade_id": trade.get("id"),
                "old_status": "PENDING",
                "new_status": "OPEN",
                "pnl_points": 0.0,
                "events": ["ORDER_FILLED"],
                "updates": base_updates,
                "progress_to_tp1": 0.0,
                "hours_open": 0.0,
            }

        if self.entry_style == "fixed_risk":
            pass

        return {
            "trade_id": trade.get("id"),
            "old_status": "PENDING",
            "new_status": "PENDING",
            "pnl_points": 0.0,
            "events": [],
            "updates": base_updates,
            "progress_to_tp1": 0.0,
            "hours_open": hours_open,
            "pending_distance_points": round(dist_pts, 1),
        }

    def _hit_tp1(self, trade_type: str, current_price: float, tp1: float) -> bool:
        if tp1 <= 0:
            return False
        return current_price >= tp1 if trade_type == "BUY" else current_price <= tp1

    def _hit_tp2(self, trade_type: str, current_price: float, tp2: float) -> bool:
        if tp2 <= 0:
            return False
        return current_price >= tp2 if trade_type == "BUY" else current_price <= tp2

    def _hit_sl(self, trade_type: str, current_price: float, stop_loss: float) -> bool:
        if stop_loss <= 0:
            return False
        return current_price <= stop_loss if trade_type == "BUY" else current_price >= stop_loss

    def _hit_break_even(self, trade_type: str, current_price: float, entry: float) -> bool:
        return current_price <= entry if trade_type == "BUY" else current_price >= entry

    def _beyond_breakeven(self, trade_type: str, stop_loss: float, entry: float) -> bool:
        """True once the persisted stop_loss has been trailed past pure breakeven
        (i.e. progressive trailing has actually locked in extra profit, not just
        the initial entry-level break-even move)."""
        epsilon = 1e-6
        if trade_type == "BUY":
            return stop_loss > entry + epsilon
        return stop_loss < entry - epsilon

    def _beyond_breakeven_or_at(self, trade_type: str, stop_loss: float, entry: float) -> bool:
        """True when the stop is at entry (breakeven) or better — i.e. the trade
        can no longer turn into a loss. Used to decide whether a time-expired
        winner is safe to keep open under its protective stop."""
        epsilon = 1e-6
        if trade_type == "BUY":
            return stop_loss >= entry - epsilon
        return stop_loss <= entry + epsilon

    def _compute_trailing_stop(
        self,
        trade_type: str,
        current_price: float,
        current_stop_loss: float,
        entry: float,
        symbol: str | None = None,
        distance_points: float | None = None,
        step_points: float | None = None,
        min_profit_lock_points: float | None = None,
    ) -> float | None:
        """Progressive trailing stop, only ever moving in the profitable direction.

        trailing_distance/trailing_step/min_profit_lock are configured in
        points (matching calculate_pips' convention: 10 points = $1.0 on
        XAU/USD), so they're converted to price units here before use.

        Only returns a new value once price has moved favorably by at least
        trailing_step beyond the current stop_loss, to avoid near-constant
        tiny updates every run. Never moves the stop below the configured
        min_profit_lock above/below entry.
        """
        distance = points_to_price(self.trailing_distance if distance_points is None else distance_points, symbol)
        step = points_to_price(self.trailing_step if step_points is None else step_points, symbol)
        min_lock = points_to_price(self.trailing_min_profit_lock if min_profit_lock_points is None else min_profit_lock_points, symbol)
        epsilon = 1e-9
        if trade_type == "BUY":
            candidate = current_price - distance
            candidate = max(candidate, entry + min_lock)
            # Move exactly on the configured step too: +30 pts should move 30 pts,
            # not require +31 due to a strict > comparison.
            if candidate >= current_stop_loss + step - epsilon:
                return candidate
        else:
            candidate = current_price + distance
            candidate = min(candidate, entry - min_lock)
            if candidate <= current_stop_loss - step + epsilon:
                return candidate
        return None

    def _progress_to_tp1(self, trade_type: str, entry: float, tp1: float, current_price: float) -> float:
        target_distance = abs(tp1 - entry)
        if target_distance <= 0:
            return 0.0
        favorable_move = (current_price - entry) if trade_type == "BUY" else (entry - current_price)
        return max(0.0, favorable_move / target_distance)

    def _touch_is_after_creation(self, trade: Dict[str, Any], now: datetime) -> bool:
        """Has a full bar closed since this order was created?

        The manager receives only "the latest candle", with no timestamp of
        its own, so it cannot tell whether that bar's high belongs to price
        action the order witnessed. Age is the reliable proxy: once the order
        has outlived the bar interval, any extreme in the current bar is
        necessarily from after it was placed.
        """
        created = self._parse_dt(str(trade.get("created_at") or trade.get("entry_time") or ""))
        if created is None:
            # Unknown age: fall back to the permissive path rather than
            # blocking a legitimate fill on missing metadata.
            return True
        minutes = max(0.0, (now - created).total_seconds() / 60.0)
        return minutes >= self.pending_touch_min_age_minutes

    def _hours_open(self, trade: Dict[str, Any], now: datetime) -> float:
        opened = self._parse_dt(str(trade.get("entry_time") or trade.get("created_at") or ""))
        if opened is None:
            return 0.0
        return max(0.0, (now - opened).total_seconds() / 3600)

    def _updates_sent(self, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except json.JSONDecodeError:
                return [value] if value else []
        return []

    def _append_updates_sent(self, updates_sent: List[str], events: List[str]) -> List[str]:
        result = list(updates_sent)
        for event in events:
            if event not in result:
                result.append(event)
        return result

    def _parse_dt(self, value: str) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _iso(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

    def _bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(value)

    def _f(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
