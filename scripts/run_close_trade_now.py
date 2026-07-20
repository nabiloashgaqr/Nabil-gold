"""Manually close an open trade at the current live price.

Use this for operator intervention when you want to flatten an existing trade
immediately and book the current profit/loss into the database + Telegram.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService
from services.market_data import MarketDataService
from services.telegram_bot import TelegramService
from utils.helpers import calculate_pips, load_config, setup_logging
from utils.instruments import config_for_instrument, normalize_symbol

LIVE_CLOSEABLE_STATUSES = {"OPEN", "PARTIAL", "TP1_HIT"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Close an open trade now at current price")
    parser.add_argument("--trade-id", required=True, help="Canonical trade id, e.g. TRADE_...")
    parser.add_argument("--reason", default="Manual operator close", help="Why you are closing it")
    parser.add_argument("--send-telegram", action="store_true", default=False)
    return parser.parse_args()


def _resolve_live_price(config: Dict[str, Any], symbol: str) -> tuple[float, str]:
    symbol_config = config_for_instrument(config, {"symbol": symbol})
    market_data = MarketDataService(symbol_config)
    base_tf = symbol_config.get("data_source", {}).get("base_timeframe", "5m")
    payload = market_data.get_ohlcv(base_tf, outputsize=3)
    allow_synthetic = bool(symbol_config.get("data_source", {}).get("allow_synthetic_in_production", False))
    if payload and os.environ.get("GITHUB_ACTIONS") == "true" and payload.get("source") == "synthetic_demo" and not allow_synthetic:
        quote_payload = market_data.get_spot_quote_payload()
        if quote_payload:
            payload = quote_payload
    if not payload or not payload.get("current_price"):
        raise RuntimeError(f"Could not fetch current market price for {symbol}")
    return float(payload.get("current_price")), str(payload.get("source") or "unknown")


def close_trade_now(trade_id: str, *, reason: str, send_telegram: bool = False, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    config = config or load_config()
    db = DatabaseService(config)
    telegram = TelegramService(config)

    open_trades = db.get_open_trades()
    target = None
    for trade in open_trades:
        if str(trade.get("id") or "") == str(trade_id):
            target = trade
            break
    if not target:
        raise RuntimeError(f"Trade not found among active trades: {trade_id}")

    status = str(target.get("status") or "").upper()
    if status not in LIVE_CLOSEABLE_STATUSES:
        raise RuntimeError(f"Trade {trade_id} is not closeable now (status={status})")

    symbol = normalize_symbol(target.get("symbol") or config.get("symbol", "XAU/USD"))
    side = str(target.get("type") or target.get("side") or "").upper()
    entry_price = float(target.get("entry_price") or 0)
    if side not in {"BUY", "SELL"} or entry_price <= 0:
        raise RuntimeError(f"Trade {trade_id} is missing side/entry_price")

    current_price, source = _resolve_live_price(config, symbol)
    pnl_points = float(calculate_pips(entry_price, current_price, side, symbol))
    result = "WIN" if pnl_points > 0 else "LOSS" if pnl_points < 0 else "BREAKEVEN"
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    updates = {
        "status": "MANUAL_CLOSE",
        "result": result,
        "close_price": round(current_price, 2),
        "closed_at": now_iso,
        "close_time": now_iso,
        "final_pnl": round(pnl_points, 1),
        "final_pnl_points": round(pnl_points, 1),
        "current_price": round(current_price, 2),
        "current_pnl": round(pnl_points, 1),
        "current_pnl_points": round(pnl_points, 1),
        "reasons": [str(reason or "Manual operator close")],
        "last_updated": now_iso,
        "market_data_source": source,
    }
    db.update_trade(str(trade_id), updates)

    evaluation = {
        "trade_id": trade_id,
        "old_status": status,
        "new_status": "MANUAL_CLOSE",
        "pnl_points": round(pnl_points, 1),
        "events": ["MANUAL_CLOSE"],
        "updates": updates,
        "progress_to_tp1": None,
        "hours_open": None,
    }
    if send_telegram or os.environ.get("GITHUB_ACTIONS") == "true":
        try:
            telegram.send_trade_events(target, ["MANUAL_CLOSE"], current_price, pnl_points, evaluation)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("Manual close Telegram failed: %s", exc)

    return {
        "trade_id": trade_id,
        "symbol": symbol,
        "side": side,
        "close_price": round(current_price, 2),
        "pnl_points": round(pnl_points, 1),
        "result": result,
        "market_data_source": source,
    }


def main() -> None:
    setup_logging()
    args = parse_args()
    result = close_trade_now(args.trade_id, reason=args.reason, send_telegram=args.send_telegram)
    print(
        f"Manual close completed: {result['trade_id']} | {result['symbol']} | {result['side']} | "
        f"close={result['close_price']:.2f} | pnl={result['pnl_points']:+.1f} pts | {result['result']} | source={result['market_data_source']}"
    )


if __name__ == "__main__":
    main()
