"""Database service using Supabase with local JSON fallback.

GitHub Actions runners are stateless, so production persistence should use
Supabase. The local fallback lets tests and manual dry-runs work without
credentials, but it will not persist between separate GitHub Actions runs.
"""

from __future__ import annotations

import logging
import os
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

    def save_trade(self, decision: Dict[str, Any]) -> str:
        """Save a new trade from a decision dictionary."""
        trade_id = f"TRADE_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
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

    def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get OPEN/TP1_HIT trades."""
        if self.use_supabase and self.client:
            try:
                response = self.client.table("trades").select("*").in_("status", ["OPEN", "PARTIAL", "TP1_HIT"]).execute()
                return list(response.data or [])
            except Exception as exc:  # noqa: BLE001
                if self._strict_supabase():
                    raise RuntimeError(f"Failed to fetch open trades from Supabase in production: {exc}") from exc
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


    def save_trade_review(self, review: Dict[str, Any]) -> str:
        """Persist an AI review for a closed trade."""
        trade_id = str(review.get("trade_id") or "unknown")
        reviewed_at = str(review.get("reviewed_at") or datetime.now(timezone.utc).replace(microsecond=0).isoformat())
        review_id = review.get("id") or f"REVIEW_{trade_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        payload = {
            "id": review_id,
            "trade_id": trade_id,
            "reviewed_at": reviewed_at,
            "provider": review.get("provider"),
            "model": review.get("model"),
            "tokens_used": review.get("tokens_used", 0),
            "review": review.get("review", {}),
            "created_at": reviewed_at,
        }

        if self.use_supabase and self.client:
            try:
                self.client.table("ai_trade_reviews").upsert(payload).execute()
                return str(review_id)
            except Exception as exc:  # noqa: BLE001
                self.logger.error("Failed to save AI trade review in Supabase, falling back local: %s", exc)

        reviews_path = self.local_path.parent / "trade_reviews.json"
        reviews = load_trades(reviews_path)
        existing = [r for r in reviews if str(r.get("id")) != str(review_id)]
        existing.append(payload)
        save_trades(existing, reviews_path)
        return str(review_id)


    def get_recent_trade_reviews(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent AI trade reviews."""
        reviews_path = self.local_path.parent / "trade_reviews.json"
        if self.use_supabase and self.client:
            try:
                response = self.client.table("ai_trade_reviews").select("*").order("reviewed_at", desc=True).limit(limit).execute()
                return list(response.data or [])
            except Exception as exc:  # noqa: BLE001
                self.logger.error("Failed to fetch AI trade reviews from Supabase: %s", exc)
        reviews = load_trades(reviews_path)
        return sorted(reviews, key=lambda item: str(item.get("reviewed_at", item.get("created_at", ""))), reverse=True)[:limit]


    def save_memory_rule(self, rule: Dict[str, Any]) -> str:
        """Persist an AI memory rule."""
        rule_id = str(rule.get("id") or f"MEM_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
        payload = {
            "id": rule_id,
            "rule_text": rule.get("rule_text", ""),
            "category": rule.get("category", "AI_REVIEW_LESSON"),
            "applies_to": rule.get("applies_to", "BOTH"),
            "confidence": int(rule.get("confidence", 70) or 70),
            "source_trade_id": rule.get("source_trade_id"),
            "source": rule.get("source", "ai_trade_review"),
            "active": bool(rule.get("active", True)),
            "times_triggered": int(rule.get("times_triggered", 0) or 0),
            "metadata": rule.get("metadata", {}),
            "created_at": rule.get("created_at") or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "updated_at": rule.get("updated_at") or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }

        if self.use_supabase and self.client:
            try:
                self.client.table("ai_memory_rules").upsert(payload).execute()
                return rule_id
            except Exception as exc:  # noqa: BLE001
                self.logger.error("Failed to save AI memory rule in Supabase, falling back local: %s", exc)

        rules_path = self.local_path.parent / "memory_rules.json"
        rules = load_trades(rules_path)
        existing = [r for r in rules if str(r.get("id")) != rule_id]
        existing.append(payload)
        save_trades(existing, rules_path)
        return rule_id

    def save_memory_rules(self, rules: List[Dict[str, Any]]) -> List[str]:
        """Persist multiple memory rules."""
        return [self.save_memory_rule(rule) for rule in rules]

    def get_active_memory_rules(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return active AI memory rules ordered by confidence/update time."""
        rules_path = self.local_path.parent / "memory_rules.json"
        if self.use_supabase and self.client:
            try:
                response = (
                    self.client.table("ai_memory_rules")
                    .select("*")
                    .eq("active", True)
                    .order("confidence", desc=True)
                    .order("updated_at", desc=True)
                    .limit(limit)
                    .execute()
                )
                return list(response.data or [])
            except Exception as exc:  # noqa: BLE001
                self.logger.error("Failed to fetch AI memory rules from Supabase: %s", exc)
        rules = [r for r in load_trades(rules_path) if r.get("active", True)]
        return sorted(rules, key=lambda r: (float(r.get("confidence", 0) or 0), str(r.get("updated_at", ""))), reverse=True)[:limit]

    def _missing_column(self, exc: Exception, column: str) -> bool:
        """Return True for Supabase/PostgREST missing-column errors."""
        text = str(exc).lower()
        return "42703" in text and column.lower() in text and "does not exist" in text

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

    def get_today_trades(self) -> List[Dict[str, Any]]:
        """Return trades created/opened/updated today UTC, supporting legacy schemas."""
        today_text = date.today().isoformat()
        if self.use_supabase and self.client:
            try:
                response = self.client.table("trades").select("*").gte("created_at", today_text).execute()
                return list(response.data or [])
            except Exception as exc:  # noqa: BLE001
                if self._missing_column(exc, "created_at"):
                    try:
                        response = self.client.table("trades").select("*").gte("updated_at", today_text).execute()
                        return list(response.data or [])
                    except Exception as fallback_exc:  # noqa: BLE001
                        if self._strict_supabase():
                            raise RuntimeError(f"Failed to fetch today trades using legacy updated_at fallback: {fallback_exc}") from fallback_exc
                        self.logger.error("Failed legacy today trades fallback from Supabase: %s", fallback_exc)
                elif self._strict_supabase():
                    raise RuntimeError(f"Failed to fetch today trades from Supabase in production: {exc}") from exc
                else:
                    self.logger.error("Failed to fetch today trades from Supabase: %s", exc)
        return [trade for trade in load_trades(self.local_path) if self._trade_time_text(trade).startswith(today_text)]

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
            "side",
            "entry_price",
            "stop_loss",
            "tp1",
            "tp2",
            "confidence",
            "status",
            "current_price",
            "current_pnl",
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
