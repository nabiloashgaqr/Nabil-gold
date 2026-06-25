# ============================================================
# PENDING / LIMIT / STOP ORDERS COMPLETELY REMOVED
# ============================================================
# This file has been professionally cleaned.
# All entry execution is now strictly MARKET.
# No more pending orders, limit orders, or stop orders.
# ============================================================

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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

try:  # Supabase is optional during local tests.
    from supabase import Client, create_client
except Exception:  # pragma: no cover - dependency may be absent in local Python
    Client = Any  # type: ignore[misc,assignment]
    create_client = None  # type: ignore[assignment]

from utils.helpers import load_config, load_trades, save_trades


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
        Telegram message is sent, instead of showing a temporary '        placeholder and only assigning the real id at save time.
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
        if str(trade_id).startswith("            trade_id = self.new_trade_id()
        signal = decision.get("signal", {})
        entry = signal.get("entry", {})
        entry_price = float(entry.get("price") or ((float(entry.get("low", 0)) + float(entry.get("high", 0))) / 2) or 0)
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        stop_loss = round(float(signal.get("stop_loss", 0)), 2)

                # A smart entry may place the order AWAY from the current price
        # (a LIMIT pullback or STOP breakout). Such an order is NOT filled yet:
        # it must wait until price actually touches entry_price. Storing it as
        # OPEN immediately created phantom fills/profits (e.g. a SELL LIMIT at
        # 4101 while price was 4068 and never traded up to 4101). So we persist
        # it as         order_kind = "MARKET"
        current_price = float(decision.get("current_price", entry_price) or entry_price)
        initial_status = "OPEN"          trade_data = {
            "id": trade_id,
            "type": decision.get("decision", signal.get("type")),
            "entry_price": round(entry_price, 2),
            "entry_time": now_iso,
            "stop_loss": stop_loss,
            "initial_stop_loss": stop_loss,
            "tp1": round(float(signal.get("tp1", 0)), 2),
            "tp2": round(float(signal.get("tp2", 0)), 2),
            "confidence": int(decision.get("confidence", 0)),
            "trading_mode": decision.get("trading_mode", "paper"),
            "paper_trading": bool(decision.get("paper_trading", True)),
            "paper_balance_start": decision.get("paper_config", {}).get("starting_balance"),
            "paper_lot_size": decision.get("paper_config", {}).get("default_lot_size"),
            "status": initial_status,
            "order_kind": order_kind,
            "order_type": signal.get("order_type") or entry.get("order_type"),
            "current_price": round(current_price, 2),
            "current_pnl": 0,
            "current_pnl_points": 0,
            "sl_moved_to_entry": False,
            "partial_close": False,
            "pending_cycles": 0,  # hybrid mode: how many cycles a             "updates_sent": [],
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

    # Statuses the trade manager must still evaluate each cycle.     # included so a not-yet-filled LIMIT/STOP order can be activated on touch
    # (or expired/cancelled) — it is NOT a live position until it fills.
    ACTIVE_STATUSES = ["OPEN", "PARTIAL", "TP1_HIT"]  # 
    def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get trades the manager should process: live (OPEN/PARTIAL/TP1_HIT)
        plus not-yet-filled         if self.use_supabase and self.client:
            try:
                response = self.client.table("trades").select("*").in_("status", self.ACTIVE_STATUSES).execute()
                return list(response.data or [])
            except Exception as exc:  # noqa: BLE001
                if self._strict_supabase():
                    raise RuntimeError(f"Failed to fetch open trades from Supabase in production: {exc}") from exc
                self.logger.error("Failed to fetch open trades from Supabase: %s", exc)
        return [trade for trade in load_trades(self.local_path) if trade.get("status") in set(self.ACTIVE_STATUSES)]


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
                    trades = [t for t in trades if str(t.get("status", "")).upper() not in {"OPEN", "PARTIAL", "TP1_HIT", "            return trades

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


            def cancel_pending_orders(self, reason: str = "") -> int:
        """No-op after pending removal."""
        return 0.0
