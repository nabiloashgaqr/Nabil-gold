"""Database service using Supabase with local JSON fallback.

GitHub Actions runners are stateless, so production persistence should use
Supabase. The local fallback lets tests and manual dry-runs work without
credentials, but it will not persist between separate GitHub Actions runs.
"""

from __future__ import annotations

import logging
import os
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
                self.logger.warning("Supabase init failed, using local fallback: %s", exc)
        else:
            self.logger.warning("Supabase credentials missing, using local fallback JSON")

    def save_trade(self, decision: Dict[str, Any]) -> str:
        """Save a new trade from a decision dictionary."""
        trade_id = f"TRADE_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        signal = decision.get("signal", {})
        entry = signal.get("entry", {})
        entry_price = float(entry.get("price") or ((float(entry.get("low", 0)) + float(entry.get("high", 0))) / 2) or 0)
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        stop_loss = round(float(signal.get("stop_loss", 0)), 2)
        trade_data = {
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
            "status": "OPEN",
            "current_price": round(float(decision.get("current_price", entry_price)), 2),
            "current_pnl": 0,
            "current_pnl_points": 0,
            "sl_moved_to_entry": False,
            "partial_close": False,
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

        if self.use_supabase and self.client:
            try:
                self._insert_trade_supabase(trade_data)
                return trade_id
            except Exception as exc:  # noqa: BLE001
                self.logger.error("Failed to save trade in Supabase, falling back local: %s", exc)

        trades = load_trades(self.local_path)
        trades.append(trade_data)
        save_trades(trades, self.local_path)
        return trade_id

    def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get OPEN/TP1_HIT trades."""
        if self.use_supabase and self.client:
            try:
                response = self.client.table("trades").select("*").in_("status", ["OPEN", "PARTIAL", "TP1_HIT"]).execute()
                return list(response.data or [])
            except Exception as exc:  # noqa: BLE001
                self.logger.error("Failed to fetch open trades from Supabase: %s", exc)
        return [trade for trade in load_trades(self.local_path) if trade.get("status") in {"OPEN", "PARTIAL", "TP1_HIT"}]


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
                self.logger.error("Failed to update Supabase trade %s: %s", trade_id, exc)

        trades = load_trades(self.local_path)
        for trade in trades:
            if str(trade.get("id")) == trade_id:
                trade.update(updates)
                break
        save_trades(trades, self.local_path)

    def get_today_signals_count(self) -> int:
        """Return number of trades created today UTC."""
        return len(self.get_today_trades())

    def get_today_trades(self) -> List[Dict[str, Any]]:
        """Return trades created today UTC."""
        today_text = date.today().isoformat()
        if self.use_supabase and self.client:
            try:
                response = self.client.table("trades").select("*").gte("created_at", today_text).execute()
                return list(response.data or [])
            except Exception as exc:  # noqa: BLE001
                self.logger.error("Failed to fetch today trades from Supabase: %s", exc)
        return [trade for trade in load_trades(self.local_path) if str(trade.get("created_at", "")).startswith(today_text)]

    def get_open_trades_count(self) -> int:
        """Return open trades count."""
        return len(self.get_open_trades())

    def get_recent_trades(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent trades ordered newest first."""
        if self.use_supabase and self.client:
            try:
                response = self.client.table("trades").select("*").order("created_at", desc=True).limit(limit).execute()
                return list(response.data or [])
            except Exception as exc:  # noqa: BLE001
                self.logger.error("Failed to fetch recent trades from Supabase: %s", exc)
        trades = load_trades(self.local_path)
        return sorted(trades, key=lambda trade: str(trade.get("created_at", "")), reverse=True)[:limit]

    def get_consecutive_losses(self, limit: int = 20) -> int:
        """Return consecutive losing closed trades, ignoring open trades."""
        losses = 0
        for trade in self.get_recent_trades(limit=limit):
            status = str(trade.get("status", "")).upper()
            if status in {"OPEN", "TP1_HIT"}:
                continue
            pnl = self._trade_pnl(trade)
            is_loss = status == "SL_HIT" or pnl < 0
            is_win_or_break = status in {"TP2_HIT", "BE_HIT", "MANUAL_CLOSE", "EXPIRED"} or pnl >= 0
            if is_loss:
                losses += 1
                continue
            if is_win_or_break:
                break
        return losses

    def _insert_trade_supabase(self, trade_data: Dict[str, Any]) -> None:
        """Insert full trade row, falling back to legacy column set if needed."""
        assert self.client is not None
        try:
            self.client.table("trades").insert(trade_data).execute()
        except Exception as exc:  # noqa: BLE001
            legacy = self._legacy_payload(trade_data)
            if legacy == trade_data:
                raise
            self.logger.warning("Full trade insert failed, trying legacy schema: %s", exc)
            self.client.table("trades").insert(legacy).execute()

    def _update_trade_supabase(self, trade_id: str, updates: Dict[str, Any]) -> None:
        """Update full trade row, falling back to legacy columns if needed."""
        assert self.client is not None
        try:
            self.client.table("trades").update(updates).eq("id", trade_id).execute()
        except Exception as exc:  # noqa: BLE001
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
            "entry_price",
            "stop_loss",
            "tp1",
            "tp2",
            "confidence",
            "status",
            "current_price",
            "current_pnl",
            "created_at",
            "closed_at",
            "close_price",
            "final_pnl",
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
