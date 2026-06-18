"""Generate Gold AI Signals HTML dashboard."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.dashboard import format_dashboard_telegram, render_dashboard, save_dashboard, summarize_trades
from services.database import DatabaseService
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    config = load_config()
    db = DatabaseService(config)
    telegram = TelegramService(config)

    limit = int(os.environ.get("DASHBOARD_TRADE_LIMIT", "80"))
    output = os.environ.get("DASHBOARD_OUTPUT", "storage/dashboard.html")

    trades = db.get_recent_trades(limit=limit)
    reviews = db.get_recent_trade_reviews(limit=20)
    memory_rules = db.get_active_memory_rules(limit=20)
    html_text = render_dashboard(trades, reviews, memory_rules)
    output_path = save_dashboard(html_text, output)
    summary = summarize_trades(trades)

    logger.info("Dashboard generated: %s | trades=%s | reviews=%s | memory_rules=%s", output_path, len(trades), len(reviews), len(memory_rules))
    print(f"Dashboard generated: {output_path}")
    print(summary)

    if os.environ.get("SEND_TELEGRAM", "true").lower() in {"1", "true", "yes"}:
        telegram.send_message(format_dashboard_telegram(summary))


if __name__ == "__main__":
    main()
