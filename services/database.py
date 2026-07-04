"""Database service using Supabase with local JSON fallback.

GitHub Actions runners are stateless, so production persistence should use
Supabase. The local fallback lets tests and manual dry-runs work without
credentials, but it will not persist between separate GitHub Actions runs.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Dict, List

try:  # Supabase is optional during local tests.
    from supabase import Client, create_client
except Exception:  # pragma: no cover - dependency may be absent in local Python
    Client = Any  # type: ignore[misc,assignment]
    create_client = None  # type: ignore[assignment]

from utils.helpers import load_config, load_trades, save_trades
from utils.instruments import price_decimals, price_to_points
from utils.sessions import session_label_from_utc, SESSION_ORDER


class DatabaseService:
    """Persist and retrieve trades from Supabase or local JSON fallback."""

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        self.logger = logging.getLogger(self.__class__.__name__)
        db_config = self.config.get("database", {})
        self.url = os.environ.get("SUPABASE_URL") or db_config.get("url")
        self.key = os.environ.get("SUPABASE_KEY") or db_config.get("key")
        if isinstance(self.url, str) and self.url.startswith("ENV:"):
            self.url = os.environ.get(self.url.replace("ENV:", "", 1))
        if isinstance(self.key, str) and self.key.startswith("ENV:"):
            self.key = os.environ.get(self.key.replace("ENV:", "", 1))
        fallback = db_config.get("local_fallback_file", "storage/trades.json")
        self.local_path = Path(__file__).resolve().parents[1] / fallback
        self.client: Client | None = None
        self.use_supabase = False

        if self.url and self.key and create_client is not None:
            try:
                self.client = create_client(str(self.url), str(self.key))
                self.use_supabase = True
                self.logger.info("Database connected: Supabase")
            except Exception as exc:  # noqa: BLE001
                if self._strict_supabase():
                    raise RuntimeError(f"Supabase init failed in production: {exc}") from exc
                self.logger.warning("Supabase init failed, using local fallback: %s", exc)
        else:
            if self._strict_supabase():
                raise RuntimeError("Supabase credentials missing in production GitHub Actions")
            self.logger.warning("Supabase credentials missing, using local fallback JSON")

    def _strict_supabase(self) -> bool:
        """Require Supabase only for production workflows or explicit requests.

        GitHub Actions sets GITHUB_ACTIONS=true for every workflow, including the
        Tests workflow. Unit tests must still be allowed to use the local JSON
        fallback. Production jobs set REQUIRE_SUPABASE=true explicitly in their
        workflow env, so they continue to fail fast if Supabase is unavailable.
        """
        explicit = str(os.environ.get("REQUIRE_SUPABASE", "")).strip().lower()
        if explicit in {"1", "true", "yes", "y"}:
            return True
        if explicit in {"0", "false", "no", "n"}:
            return False
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        if os.environ.get("GITHUB_ACTIONS") != "true":
            return False
        return bool(self.config.get("github_actions", {}).get("require_supabase", False))

    @staticmethod
    def new_trade_id() -> str:
        """Generate a canonical trade id.

        Exposed so callers (e.g. run_analysis) can mint the *real* id BEFORE the
        Telegram message is sent, instead of showing a temporary 'PENDING_...'
        placeholder and only assigning the real id at save time.
        """
        return f"TRADE_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"

    def save_trade(self, decision: Dict[str, Any]) -> str:
        """Save a new trade from a decision dictionary."""
        # Reuse a pre-assigned id (set by the caller before sending Telegram) so
        # the id shown in the signal message matches the stored trade exactly.
        trade_id = (
            decision.get("trade_id")
            or (decision.get("signal", {}) or {}).get("trade_id")
            or self.new_trade_id()
        )
        if str(trade_id).startswith("PENDING_") or not str(trade_id).startswith("TRADE_"):
            trade_id = self.new_trade_id()
        signal = decision.get("signal", {})
        entry = signal.get("entry", {})
        symbol = str(decision.get("symbol") or signal.get("symbol") or self.config.get("symbol", "XAU/USD"))
        decimals = price_decimals(symbol)
        entry_price = float(entry.get("price") or ((float(entry.get("low", 0)) + float(entry.get("high", 0))) / 2) or 0)
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        stop_loss = round(float(signal.get("stop_loss", 0)), decimals)

        # ── Market vs pending (LIMIT/STOP) order ────────────────────────────
        # A smart entry may place the order AWAY from the current price
        # (a LIMIT pullback or STOP breakout). Such an order is NOT filled yet:
        # it must wait until price actually touches entry_price. Storing it as
        # OPEN immediately created phantom fills/profits (e.g. a SELL LIMIT at
        # 4101 while price was 4068 and never traded up to 4101). So we persist
        # it as PENDING and let the trade manager activate it on touch.
        order_kind = str(signal.get("entry_kind") or entry.get("kind") or "MARKET").upper()
        current_price = float(decision.get("current_price", entry_price) or entry_price)
        initial_status = "PENDING" if order_kind in {"LIMIT", "STOP"} and abs(entry_price - current_price) > 0.01 else "OPEN"
        trade_data = {
            "id": trade_id,
            "type": decision.get("decision", signal.get("type")),
            "symbol": symbol,
            "entry_price": round(entry_price, decimals),
            "entry_time": now_iso,
            "stop_loss": stop_loss,
            "initial_stop_loss": stop_loss,
            "tp1": round(float(signal.get("tp1", 0)), decimals),
            "tp2": round(float(signal.get("tp2", 0)), decimals),
            "confidence": int(decision.get("confidence", 0)),
            "trading_mode": decision.get("trading_mode", "paper"),
            "paper_trading": bool(decision.get("paper_trading", True)),
            "paper_balance_start": decision.get("paper_config", {}).get("starting_balance"),
            "paper_lot_size": decision.get("paper_config", {}).get("default_lot_size"),
            "status": initial_status,
            "order_kind": order_kind,
            "order_type": signal.get("order_type") or entry.get("order_type"),
            "current_price": round(current_price, decimals),
            "current_pnl": 0,
            "current_pnl_points": 0,
            "sl_moved_to_entry": False,
            "partial_close": False,
            "pending_cycles": 0,  # hybrid mode: how many cycles a PENDING order has survived
            "updates_sent": [],
            "result": None,
            "created_at": now_iso,
            "closed_at": None,
            "close_time": None,
            "close_price": None,
            "final_pnl": None,
            "reasons": decision.get("reasons", []),
            "signal_snapshot": decision,
            "last_updated": now_iso,
        }

        # Backwards-compatible: set 'side' alongside 'type' for clearer naming
        trade_data["side"] = trade_data.get("type")
        trade_data.update(self._entry_enrichment(decision, signal, symbol, entry_price, stop_loss))

        if self.use_supabase and self.client:
            try:
                self._insert_trade_supabase(trade_data)
                return trade_id
            except Exception as exc:  # noqa: BLE001
                if self._strict_supabase():
                    raise RuntimeError(f"Failed to save trade in Supabase in production: {exc}") from exc
                self.logger.error("Failed to save trade in Supabase, falling back local: %s", exc)

        trades = load_trades(self.local_path)
        trades.append(trade_data)
        save_trades(trades, self.local_path)
        return trade_id

    @staticmethod
    def _build_regime_composite(tech_regime: dict) -> str:
        """Build a composite regime label from volatility_regime + market_phase.

        Examples: 'NORMAL TRENDING', 'HIGH RANGING', 'SQUEEZE', etc.
        Returns 'UNKNOWN' if neither dimension is available.
        """
        vol = str(tech_regime.get("volatility_regime") or "").strip().upper()
        phase = str(tech_regime.get("market_phase") or "").strip().upper()
        if phase == "SQUEEZE":
            return "SQUEEZE"
        if vol and phase:
            return f"{vol} {phase}"
        if vol:
            return vol
        if phase:
            return phase
        return "UNKNOWN"

    def _entry_enrichment(
        self,
        decision: Dict[str, Any],
        signal: Dict[str, Any],
        symbol: str,
        entry_price: float,
        stop_loss: float,
    ) -> Dict[str, Any]:
        """Best-effort Phase 5 metadata persisted with each trade.

        Older Supabase schemas may not have all columns; insert retry logic drops
        unknown columns while keeping this data in signal_snapshot. When columns
        exist, reports can query directly without parsing JSON snapshots.
        """
        now = datetime.now(timezone.utc)
        try:
            local = now.astimezone(ZoneInfo(str(self.config.get("schedule", {}).get("timezone") or self.config.get("trading_hours", {}).get("timezone") or "Asia/Hebron")))
        except Exception:  # noqa: BLE001
            local = now
        side = str(decision.get("decision") or signal.get("type") or "").upper()
        tp2 = signal.get("tp2")
        try:
            planned_risk_points = abs(price_to_points(float(entry_price) - float(stop_loss), symbol=symbol))
        except Exception:  # noqa: BLE001
            planned_risk_points = 0.0
        try:
            planned_tp2_points = abs(price_to_points(float(tp2) - float(entry_price), symbol=symbol)) if tp2 is not None else 0.0
        except Exception:  # noqa: BLE001
            planned_tp2_points = 0.0
        planned_rr = float(signal.get("rr_ratio") or signal.get("tp2_rr") or 0) or (planned_tp2_points / planned_risk_points if planned_risk_points else 0.0)
        session_info = decision.get("session_info") or {}
        news_context = decision.get("news_context") or {}
        market_context = decision.get("market_context") or {}
        news_rule = news_context.get("rule_based", {}) if isinstance(news_context, dict) else {}
        tech_regime = market_context.get("technical_regime", {}) if isinstance(market_context, dict) else {}
        if not isinstance(tech_regime, dict):
            tech_regime = {}
        # Session label: prefer the already-classified name from
        # TradingSessionAgent (which now uses classify_session), fall back to
        # computing from the current UTC timestamp so we never store a raw
        # config name like "Main Trading Session".
        stored_session = (
            session_info.get("current_session")
            or session_info.get("session")
            or session_info.get("session_name")
        )
        session_label = (
            stored_session if stored_session in SESSION_ORDER
            else session_label_from_utc(now)
        )
        return {
            "planned_risk_points": round(planned_risk_points, 1),
            "planned_tp2_points": round(planned_tp2_points, 1),
            "planned_rr": round(planned_rr, 2),
            "session_label": session_label,
            "session_quality": session_info.get("session_quality") or session_info.get("quality"),
            "entry_day_of_week": local.strftime("%A"),
            "entry_hour_local": int(local.hour),
            "news_status_at_entry": news_rule.get("market_status") or news_rule.get("status"),
            "news_risk_at_entry": news_rule.get("risk_level") or news_rule.get("risk"),
            "volatility_regime": tech_regime.get("volatility_regime"),
            "market_phase": tech_regime.get("market_phase"),
            "regime_composite": self._build_regime_composite(tech_regime),
            "trend_strength": tech_regime.get("trend_strength"),
            "daily_bias_at_entry": (decision.get("daily_bias") or {}).get("bias") if isinstance(decision.get("daily_bias"), dict) else None,
            "primary_entry_driver": (decision.get("entry_attribution") or {}).get("primary_entry_driver") if isinstance(decision.get("entry_attribution"), dict) else None,
            "entry_failure_mode": (decision.get("entry_attribution") or {}).get("failure_mode") if isinstance(decision.get("entry_attribution"), dict) else None,
            "macro_bias_at_entry": ((decision.get("market_context") or {}).get("macro_direction") or {}).get("bias") if isinstance((decision.get("market_context") or {}).get("macro_direction"), dict) else None,
        }

    # Statuses the trade manager must still evaluate each cycle. PENDING is
    # included so a not-yet-filled LIMIT/STOP order can be activated on touch
    # (or expired/cancelled) — it is NOT a live position until it fills.
    ACTIVE_STATUSES = ["OPEN", "PARTIAL", "TP1_HIT", "PENDING"]

    def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get trades the manager should process: live (OPEN/PARTIAL/TP1_HIT)
        plus not-yet-filled PENDING limit/stop orders."""
        if self.use_supabase and self.client:
            try:
                response = self.client.table("trades").select("*").in_("status", self.ACTIVE_STATUSES).execute()
                return list(response.data or [])
            except Exception as exc:  # noqa: BLE001
                if self._strict_supabase():
                    raise RuntimeError(f"Failed to fetch open trades from Supabase in production: {exc}") from exc
                self.logger.error("Failed to fetch open trades from Supabase: %s", exc)
        return [trade for trade in load_trades(self.local_path) if trade.get("status") in set(self.ACTIVE_STATUSES)]

    def save_macro_context(self, context: Dict[str, Any]) -> bool:
        """Persist latest hourly macro context in Supabase when schema exists.

        The analysis workflow can read this snapshot without spending Twelve
        Data quota every 5 minutes. Missing table/columns are treated as a safe
        no-op because local storage remains available for manual runs.
        """
        if not (self.use_supabase and self.client):
            return False
        payload = {
            "id": "latest",
            "context": context,
            "source": context.get("source"),
            "generated_at": context.get("generated_at"),
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        try:
            self.client.table("macro_context").upsert(payload).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Could not save macro_context snapshot: %s", exc)
            return False

    def get_macro_context(self) -> Dict[str, Any]:
        """Load latest persisted macro context from Supabase or local file."""
        if self.use_supabase and self.client:
            try:
                response = self.client.table("macro_context").select("context").eq("id", "latest").limit(1).execute()
                rows = list(response.data or [])
                if rows and isinstance(rows[0].get("context"), dict):
                    return dict(rows[0]["context"])
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Could not read macro_context snapshot: %s", exc)
        local = Path(__file__).resolve().parents[1] / "storage" / "macro_context.json"
        if local.exists():
            try:
                import json
                data = json.loads(local.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Could not read local macro_context.json: %s", exc)
        return {}


    async def execute_query(self, query: str, params: List[Any] | None = None) -> List[Dict[str, Any]]:
        """Small compatibility layer for older services that used raw SQL.

        Supabase's Python PostgREST client does not execute arbitrary SQL directly.
        This method supports the common read/write patterns used by this project and
        falls back safely instead of crashing scheduled GitHub Actions.
        """
        params = params or []
        q = " ".join(str(query).strip().lower().split())

        # Local fallback / generic reads from trades.
        if "from trades" in q:
            trades = load_trades(self.local_path)
            if self.use_supabase and self.client:
                try:
                    response = self.client.table("trades").select("*").execute()
                    trades = list(response.data or [])
                except Exception as exc:  # noqa: BLE001
                    self.logger.error("execute_query trades fallback after Supabase error: %s", exc)
            if "where" in q and "status" in q:
                if "open" in q or "tp1_hit" in q or "partial" in q:
                    trades = [t for t in trades if str(t.get("status", "")).upper() in {"OPEN", "PARTIAL", "TP1_HIT"}]
                elif "closed_at is not null" in q or "status not in" in q:
                    trades = [t for t in trades if str(t.get("status", "")).upper() not in {"OPEN", "PARTIAL", "TP1_HIT", "PENDING"}]
            return trades

        # agent_weights read/update compatibility.
        if "from agent_weights" in q:
            if self.use_supabase and self.client:
                try:
                    response = self.client.table("agent_weights").select("*").execute()
                    return list(response.data or [])
                except Exception as exc:  # noqa: BLE001
                    self.logger.error("Failed to read agent_weights: %s", exc)
            return []

        if "insert into agent_weights" in q or "update agent_weights" in q:
            if self.use_supabase and self.client and len(params) >= 2:
                try:
                    agent_name = str(params[0])
                    weight = float(params[1])
                    self.client.table("agent_weights").upsert({"agent_name": agent_name, "weight": weight}).execute()
                except Exception as exc:  # noqa: BLE001
                    self.logger.error("Failed to upsert agent_weights: %s", exc)
            return []

        self.logger.warning("Unsupported execute_query call ignored safely: %s", str(query)[:160])
        return []

    def update_trade(self, trade_id: str, updates: Dict[str, Any]) -> None:
        """Update a trade by id."""
        if self.use_supabase and self.client:
            try:
                self._update_trade_supabase(trade_id, updates)
                return
            except Exception as exc:  # noqa: BLE001
                if self._strict_supabase():
                    raise RuntimeError(f"Failed to update Supabase trade {trade_id} in production: {exc}") from exc
                self.logger.error("Failed to update Supabase trade %s: %s", trade_id, exc)

        trades = load_trades(self.local_path)
        for trade in trades:
            if str(trade.get("id")) == trade_id:
                # Apply updates
                trade.update(updates)
                # Keep type/side in sync for backward compatibility
                if "type" in updates and "side" not in updates:
                    trade["side"] = updates.get("type")
                if "side" in updates and "type" not in updates:
                    trade["type"] = updates.get("side")
                break
        save_trades(trades, self.local_path)


    def cancel_pending_orders(self, reason: str = "Replaced by a newer signal") -> int:
        """Cancel all not-yet-filled PENDING orders. Returns how many were cancelled.

        Called before saving a new signal so a stale resting LIMIT/STOP order is
        replaced by the fresh setup instead of lingering forever.
        """
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        cancelled = 0
        if self.use_supabase and self.client:
            try:
                resp = self.client.table("trades").select("id").eq("status", "PENDING").execute()
                ids = [r.get("id") for r in (resp.data or []) if r.get("id")]
                for tid in ids:
                    self.update_trade(tid, {
                        "status": "CANCELLED", "result": "CANCELLED",
                        "closed_at": now_iso, "close_time": now_iso,
                        "reasons": [reason], "last_updated": now_iso,
                    })
                    cancelled += 1
                return cancelled
            except Exception as exc:  # noqa: BLE001
                if self._strict_supabase():
                    raise RuntimeError(f"Failed to cancel pending orders in production: {exc}") from exc
                self.logger.error("Failed to cancel pending orders from Supabase: %s", exc)

        trades = load_trades(self.local_path)
        changed = False
        for trade in trades:
            if str(trade.get("status", "")).upper() == "PENDING":
                trade.update({
                    "status": "CANCELLED", "result": "CANCELLED",
                    "closed_at": now_iso, "close_time": now_iso,
                    "reasons": [reason], "last_updated": now_iso,
                })
                cancelled += 1
                changed = True
        if changed:
            save_trades(trades, self.local_path)
        return cancelled

    def _missing_column_name(self, exc: Exception) -> str | None:
        """Extract the missing column name from a Supabase/PostgREST error.

        Handles both error styles:
          * PostgREST schema-cache: PGRST204 -> "Could not find the 'X' column
            of 'trades' in the schema cache"
          * Postgres: 42703 -> "column \"X\" does not exist"
        Returns the column name (e.g. 'exit_warning') or None.
        """
        text = str(exc)
        # PGRST204 style: ... the 'X' column ...
        m = re.search(r"the '([^']+)' column", text)
        if m:
            return m.group(1)
        # 42703 style: column "X" does not exist
        m = re.search(r'column "([^"]+)" does not exist', text)
        if m:
            return m.group(1)
        # Fallback single-quoted Postgres style: column 'X' does not exist
        m = re.search(r"column '([^']+)' does not exist", text)
        if m:
            return m.group(1)
        return None

    def _missing_column(self, exc: Exception, column: str) -> bool:
        """Return True for Supabase/PostgREST missing-column errors."""
        text = str(exc).lower()
        if "42703" in text and column.lower() in text and "does not exist" in text:
            return True
        # PGRST204 schema-cache style.
        if "pgrst204" in text or "schema cache" in text:
            return column.lower() in text
        return False

    def _trade_time_text(self, trade: Dict[str, Any]) -> str:
        """Best available timestamp across current and legacy trade schemas."""
        for key in ("created_at", "entry_time", "opened_at", "updated_at", "last_updated", "closed_at"):
            value = trade.get(key)
            if value:
                return str(value)
        return ""

    def get_today_signals_count(self) -> int:
        """Return number of trades created today UTC."""
        return len(self.get_today_trades())

    def _date_window_utc(self, report_date: str, timezone_name: str = "UTC") -> tuple[str, str]:
        """Return UTC ISO [start, end) boundaries for a local report date."""
        day = date.fromisoformat(str(report_date))
        tz = timezone.utc
        if ZoneInfo is not None:
            try:
                tz = ZoneInfo(timezone_name or "UTC")
            except Exception:  # noqa: BLE001
                tz = timezone.utc
        start_local = datetime.combine(day, time.min, tzinfo=tz)
        end_local = start_local + timedelta(days=1)
        start_utc = start_local.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        end_utc = end_local.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return start_utc, end_utc

    def get_trades_for_date(self, report_date: str | None = None, timezone_name: str = "UTC") -> List[Dict[str, Any]]:
        """Return trades for a specific local report date.

        Includes trades CREATED on that date OR trades CLOSED on that date.
        This ensures a trade opened yesterday but closed today appears in today's
        realized performance stats.
        """
        report_date = report_date or date.today().isoformat()
        start_utc, end_utc = self._date_window_utc(report_date, timezone_name)
        if self.use_supabase and self.client:
            try:
                # Query trades created OR closed within the window.
                # Using an 'or' filter in Supabase: (created_at >= start AND created_at < end) OR (closed_at >= start AND closed_at < end)
                filter_str = f"and(created_at.gte.{start_utc},created_at.lt.{end_utc}),and(closed_at.gte.{start_utc},closed_at.lt.{end_utc})"
                response = (
                    self.client.table("trades")
                    .select("*")
                    .or_(filter_str)
                    .execute()
                )
                return list(response.data or [])
            except Exception as exc:  # noqa: BLE001
                self.logger.error("Failed to fetch trades for %s from Supabase: %s", report_date, exc)

        # Local/debug fallback: check created_at or closed_at prefix
        return [
            trade for trade in load_trades(self.local_path)
            if self._trade_time_text(trade).startswith(str(report_date)) or
               str(trade.get("closed_at") or "").startswith(str(report_date))
        ]

    def get_today_trades(self) -> List[Dict[str, Any]]:
        """Return trades for today UTC/local-default, supporting legacy schemas."""
        return self.get_trades_for_date(date.today().isoformat(), "UTC")

    def get_open_trades_count(self) -> int:
        """Return open trades count."""
        return len(self.get_open_trades())

    def get_recent_trades(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent trades ordered newest first, supporting legacy schemas."""
        if self.use_supabase and self.client:
            try:
                response = self.client.table("trades").select("*").order("created_at", desc=True).limit(limit).execute()
                return list(response.data or [])
            except Exception as exc:  # noqa: BLE001
                if self._missing_column(exc, "created_at"):
                    try:
                        response = self.client.table("trades").select("*").order("updated_at", desc=True).limit(limit).execute()
                        return list(response.data or [])
                    except Exception as fallback_exc:  # noqa: BLE001
                        if self._strict_supabase():
                            raise RuntimeError(f"Failed to fetch recent trades using legacy updated_at fallback: {fallback_exc}") from fallback_exc
                        self.logger.error("Failed legacy recent trades fallback from Supabase: %s", fallback_exc)
                elif self._strict_supabase():
                    raise RuntimeError(f"Failed to fetch recent trades from Supabase in production: {exc}") from exc
                else:
                    self.logger.error("Failed to fetch recent trades from Supabase: %s", exc)
        trades = load_trades(self.local_path)
        return sorted(trades, key=self._trade_time_text, reverse=True)[:limit]

    def get_consecutive_losses(self, limit: int = 20) -> int:
        """Return consecutive losing closed trades, ignoring open trades."""
        losses = 0
        for trade in self.get_recent_trades(limit=limit):
            status = str(trade.get("status", "")).upper()
            if status in {"OPEN", "TP1_HIT"}:
                continue
            pnl = self._trade_pnl(trade)
            # SL_HIT is not automatically a loss: a trailing/breakeven stop can
            # close profitably (SL+) or at breakeven. Use PnL sign when present.
            is_loss = pnl < 0
            is_win_or_break = status in {"TP2_HIT", "BE_HIT", "MANUAL_CLOSE", "EXPIRED"} or pnl >= 0
            if is_loss:
                losses += 1
                continue
            if is_win_or_break:
                break
        return losses

    # How many unknown columns we are willing to strip one-by-one before giving
    # up and using the minimal legacy payload.
    _MAX_COLUMN_RETRIES = 12

    def _drop_missing_columns_and_retry(self, op, payload: Dict[str, Any]):
        """Run ``op(payload)``; if it fails on an unknown column, drop ONLY that
        column and retry, instead of collapsing to a tiny legacy payload.

        This preserves critical fields (stop_loss, result, sl_moved_to_entry,
        close_time, ...) that the old legacy fallback silently discarded — which
        is why trailing-stop / breakeven updates never persisted on older
        Supabase schemas. Only the genuinely missing columns are removed.
        """
        current = dict(payload)
        dropped: List[str] = []
        last_exc: Exception | None = None
        for _ in range(self._MAX_COLUMN_RETRIES):
            try:
                return op(current), dropped
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                col = self._missing_column_name(exc)
                if not col or col not in current:
                    break
                current.pop(col, None)
                dropped.append(col)
                if not current:
                    break
        # Could not resolve by dropping columns; surface for caller fallback.
        raise last_exc if last_exc else RuntimeError("Supabase operation failed")

    def _insert_trade_supabase(self, trade_data: Dict[str, Any]) -> None:
        """Insert full trade row.

        If the live schema is missing some newer columns, drop ONLY those and
        retry, so we still store as much as the schema supports. Fall back to the
        minimal legacy payload only as a last resort.
        """
        assert self.client is not None
        try:
            self.client.table("trades").insert(trade_data).execute()
            return
        except Exception as exc:  # noqa: BLE001
            try:
                _, dropped = self._drop_missing_columns_and_retry(
                    lambda p: self.client.table("trades").insert(p).execute(), trade_data
                )
                if dropped:
                    self.logger.warning(
                        "Trade insert succeeded after dropping unknown column(s): %s. "
                        "Add them to your Supabase 'trades' table (see supabase_schema.sql).",
                        ", ".join(dropped),
                    )
                return
            except Exception:  # noqa: BLE001
                legacy = self._legacy_payload(trade_data)
                if legacy == trade_data:
                    raise
                self.logger.warning("Full trade insert failed, trying legacy schema: %s", exc)
                self.client.table("trades").insert(legacy).execute()

    def _update_trade_supabase(self, trade_id: str, updates: Dict[str, Any]) -> None:
        """Update full trade row.

        Drop only unknown columns and retry (preserving stop_loss/result/etc.),
        falling back to the minimal legacy column set only if that still fails.
        """
        assert self.client is not None
        try:
            self.client.table("trades").update(updates).eq("id", trade_id).execute()
            return
        except Exception as exc:  # noqa: BLE001
            try:
                _, dropped = self._drop_missing_columns_and_retry(
                    lambda p: self.client.table("trades").update(p).eq("id", trade_id).execute(), updates
                )
                if dropped:
                    self.logger.warning(
                        "Trade %s update succeeded after dropping unknown column(s): %s. "
                        "Add them to your Supabase 'trades' table (see supabase_schema.sql).",
                        trade_id, ", ".join(dropped),
                    )
                return
            except Exception:  # noqa: BLE001
                legacy = self._legacy_payload(updates)
                if not legacy or legacy == updates:
                    raise
                self.logger.warning("Full trade update failed, trying legacy schema: %s", exc)
                self.client.table("trades").update(legacy).eq("id", trade_id).execute()

    def _legacy_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Keep only columns from the initial Supabase schema for compatibility."""
        legacy_fields = {
            "id",
            "type",
            "side",
            "entry_price",
            "stop_loss",
            "initial_stop_loss",
            "tp1",
            "tp2",
            "confidence",
            "status",
            "current_price",
            "current_pnl",
            # Critical management fields — must survive even the last-resort
            # fallback, otherwise breakeven/trailing-stop changes never persist.
            "sl_moved_to_entry",
            "result",
            "closed_at",
            "close_time",
            "close_price",
            "final_pnl",
            "planned_risk_points",
            "planned_tp2_points",
            "planned_rr",
            "session_label",
            "session_quality",
            "entry_day_of_week",
            "entry_hour_local",
            "news_status_at_entry",
            "news_risk_at_entry",
            "volatility_regime",
            "market_phase",
            "regime_composite",
            "trend_strength",
            "daily_bias_at_entry",
            "reasons",
            "last_updated",
        }
        return {key: value for key, value in data.items() if key in legacy_fields}

    def _trade_pnl(self, trade: Dict[str, Any]) -> float:
        """Extract final/current pnl safely."""
        for key in ("final_pnl", "current_pnl", "current_pnl_points"):
            value = trade.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return 0.0
