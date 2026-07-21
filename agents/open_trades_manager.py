"""Open Trades Manager Agent.

يتابع الصفقات المفتوحة في Supabase/JSON، يحسب الربح والخسارة، يرسل تحديثات
تليجرام عند الاقتراب من الهدف أو تحقق الأهداف/الوقف/التعادل، ويمنع تكرار
الرسائل عبر حقل ``updates_sent``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from services.pending_governor import PendingGovernor
from services.scenario_governor import ScenarioGovernor
from utils.helpers import calculate_pips, canonical_session_label, load_config
from utils.instruments import points_to_price


class OpenTradesManager(BaseAgent):
    """Evaluate and update open trades for the stateless GitHub Actions runner."""

    name = "open_trades_manager"
    OPEN_STATUSES = {"OPEN", "PARTIAL", "TP1_HIT"}
    CLOSED_STATUSES = {"TP2_HIT", "SL_HIT", "BE_HIT", "EXPIRED", "MANUAL_CLOSE"}
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
        "TP1_HIT",
        "TP2_HIT",
        "SL_HIT",
        "TRAILING_SL_HIT",
        "BE_HIT",
        "EXPIRED",
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

        # Trailing stop + breakeven: read from trade_management first,
        # then instruments (per-symbol override), then trailing_stop (legacy).
        # Priority: trade_management > instruments > trailing_stop > defaults.
        tm = self.management  # trade_management
        ts_config = self.config.get("trailing_stop", {})  # legacy section

        self.trailing_enabled = bool(tm.get("trailing_stop_enabled", ts_config.get("enabled", False)))
        self.trailing_distance = float(
            tm.get("trailing_distance_points", ts_config.get("trailing_distance", 150.0))
        )
        self.trailing_step = float(
            tm.get("trailing_step_points", ts_config.get("trailing_step", 40.0))
        )
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
        pf = (self.config.get("pending_freshness", {}) or {}) if isinstance(self.config, dict) else {}
        self.pending_freshness_enabled = bool(pf.get("enabled", True))
        self.pending_freshness_aging_after_hours = float(pf.get("aging_after_hours", 2) or 2)
        self.pending_freshness_stale_after_hours = float(pf.get("stale_after_hours", 6) or 6)
        self.pending_freshness_stale_after_excursion_points = float(pf.get("stale_after_excursion_points", 250) or 250)
        self.pending_freshness_stale_after_target_progress_pct = float(pf.get("stale_after_target_progress_pct", 60) or 60)
        self.pending_freshness_revalidation_on_session_change = bool(pf.get("mark_revalidation_required_on_session_change", True))
        ptr = (pf.get("touch_revalidation") or {}) if isinstance(pf, dict) else {}
        self.pending_touch_revalidation_enabled = bool(ptr.get("enabled", True))
        self.pending_touch_revalidation_min_confirmation_points = float(ptr.get("min_confirmation_points", 15) or 15)
        self.pending_touch_revalidation_limit_max_drift_points = float(ptr.get("limit_max_drift_points", 40) or 40)
        self.pending_touch_revalidation_stop_max_drift_points = float(ptr.get("stop_max_drift_points", 25) or 25)
        self.pending_touch_revalidation_cancel_on_failed = bool(ptr.get("cancel_on_failed_revalidation", True))
        self.profile_overrides = (self.management.get("profiles", {}) or {}) if isinstance(self.management, dict) else {}
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
            "trailing_enabled": self.trailing_enabled,
            "trailing_distance_points": self.trailing_distance,
            "trailing_step_points": self.trailing_step,
            "trailing_min_profit_lock_points": self.trailing_min_profit_lock,
            "early_breakeven_points": self.early_breakeven_points,
        }
        override = self.profile_overrides.get(profile) or {}
        for key in list(params.keys()):
            if key in override:
                params[key] = override[key]
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
        news_blocked: bool = False,
        news_context: Dict[str, Any] | None = None,
        market_data_source: str | None = None,
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
                news_blocked=news_blocked,
                news_context=news_context,
                database=database,
                market_data_source=market_data_source,
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
        news_blocked: bool = False,
        news_context: Dict[str, Any] | None = None,
        database: Any | None = None,
        market_data_source: str | None = None,
    ) -> Dict[str, Any]:
        """Return updates/events for a single trade without external side effects.

        ``current_price`` remains the displayed/latest close. If the caller
        provides the latest candle high/low, hard level checks use those extremes:
        BUY targets use high, BUY stops use low; SELL targets use low, SELL stops
        use high. This catches TP/SL touches within a 5-minute candle.
        """
        now = now or datetime.now(timezone.utc)
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
        if high_price < low_price:
            high_price, low_price = low_price, high_price

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
                news_blocked=news_blocked,
                news_context=news_context,
                database=database,
                market_data_source=market_data_source,
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

        def _breakeven_touched() -> bool:
            return low_price <= entry if trade_type == "BUY" else high_price >= entry

        tp2_touched = _target_touched(tp2)
        tp1_touched = _target_touched(tp1)
        sl_touched = _stop_touched(stop_loss)
        be_touched = _breakeven_touched()
        hours_open = self._hours_open(trade, now)

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
            elif tp1_touched and old_status == "OPEN" and not partial_close:
                # TP1 can still be hit while in early-BE phase (BE done via
                # early_breakeven, not TP1). Must record partial close.
                new_status = "TP1_HIT"
                events.append("TP1_HIT")
                partial_close = True
                # SL is already at entry from early BE — no need to move again.
            else:
                # Legacy compatibility: older rows may have sl_moved_to_entry=True
                # while stop_loss still shows the original wider SL. In that case
                # the active protective stop that existed before this candle is
                # entry, not the stale stored SL value.
                active_protective_stop = stop_loss
                if sl_moved_to_entry and not self._beyond_breakeven_or_at(trade_type, stop_loss, entry):
                    active_protective_stop = entry
                active_stop_touched = _stop_touched(active_protective_stop)

                trailing_candidate = None
                if bool(management["trailing_enabled"]) and new_status in self.OPEN_STATUSES and "EXPIRED" not in events:
                    trailing_candidate = self._compute_trailing_stop(
                        trade_type,
                        favorable_price,
                        active_protective_stop,
                        entry,
                        symbol,
                        distance_points=float(management["trailing_distance_points"]),
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
            and sl_touched
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
        elif old_status == "OPEN" and tp1_touched:
            new_status = "TP1_HIT"
            events.append("TP1_HIT")
            partial_close = True
            if bool(management["auto_be"]):
                sl_moved_to_entry = True
                new_stop_loss = entry  # actually persist breakeven, not just the flag
                events.append("MOVE_SL_TO_BE")
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
                    distance_points=float(management["trailing_distance_points"]),
                    step_points=float(management["trailing_step_points"]),
                    min_profit_lock_points=float(management["trailing_min_profit_lock_points"]),
                )
                if trailing_candidate is not None:
                    new_stop_loss = trailing_candidate
                    if "TRAILING_SL_UPDATED" not in events:
                        events.append("TRAILING_SL_UPDATED")

        # Avoid repeating informational events already sent. Status events are naturally one-time after status changes.
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
        if result is not None:
            updates["result"] = result
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
        news_blocked: bool = False,
        news_context: Dict[str, Any] | None = None,
        database: Any | None = None,
        market_data_source: str | None = None,
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
        if high_price < low_price:
            high_price, low_price = low_price, high_price
        market_source = str(market_data_source or trade.get("market_data_source") or "")
        touch_source_reliable = market_source not in {"swissquote_spot_quote_fallback", "synthetic_demo", "quote"}
        theoretical_touch = self._order_filled(order_type, trade_type, entry, current_price, high_price, low_price)
        filled_touch = theoretical_touch if touch_source_reliable else False
        base_updates = {
            "current_price": round(current_price, 2),
            "last_candle_high": round(high_price, 2),
            "last_candle_low": round(low_price, 2),
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

        _persist_runtime(
            touch_detection_source=market_source or None,
            touch_detection_source_reliable=touch_source_reliable,
            touch_detection_waiting_for_reliable_ohlc=bool(theoretical_touch and not touch_source_reliable),
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
            favorable_excursion_points = max(0.0, calculate_pips(entry, current_price, trade_type, symbol))
            max_excursion_points = max(self._f(runtime.get("max_excursion_points"), 0.0), favorable_excursion_points)
            planned_target_points = 0.0
            for target in (tp1_price, tp2):
                if target > 0:
                    planned_target_points = abs(calculate_pips(entry, target, trade_type, symbol))
                    if planned_target_points > 0:
                        break
            progress_pct = (max_excursion_points / planned_target_points * 100.0) if planned_target_points > 0 else 0.0
            plan = snapshot.get("session_plan") or {}
            plan_expiry = self._parse_dt(str((plan.get("plan_expires_at") if isinstance(plan, dict) else None) or runtime.get("plan_expires_at") or ""))
            reasons: List[str] = []
            state = "FRESH"
            revalidation_required = False
            if plan_expiry and now >= plan_expiry:
                state = "REVALIDATION_REQUIRED"
                revalidation_required = True
                reasons.append("session plan expired")
            elif self.pending_freshness_revalidation_on_session_change and created_session and current_session != created_session:
                state = "REVALIDATION_REQUIRED"
                revalidation_required = True
                reasons.append(f"session changed: {created_session} -> {current_session}")
            elif hours_open >= self.pending_freshness_stale_after_hours:
                state = "STALE"
                revalidation_required = True
                reasons.append(f"waiting too long ({hours_open:.1f}h)")
            elif max_excursion_points >= self.pending_freshness_stale_after_excursion_points:
                state = "STALE"
                revalidation_required = True
                reasons.append(f"market moved {max_excursion_points:.0f} pts without fill")
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

        # Hybrid mode: auto-convert stale PENDING to MARKET
        if not filled_touch and self.entry_style == "hybrid" and self.pending_order_max_cycles > 0:
            pending_cycles = self._f(trade.get("pending_cycles", 0))
            pending_cycles += 1
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
                        "updates": base_updates,
                        "progress_to_tp1": 0.0,
                        "hours_open": hours_open,
                        "pending_distance_points": dist_pts,
                    }
                base_updates.update({
                    "status": "OPEN",
                    "entry_time": self._iso(now),
                    "entry_price": round(current_price, 2),
                    "current_pnl": 0,
                    "current_pnl_points": 0,
                    "pending_cycles": 0,
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
            base_updates["pending_cycles"] = pending_cycles

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
