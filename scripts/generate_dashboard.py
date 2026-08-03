"""Generate Gold AI Signals HTML dashboard."""

from __future__ import annotations

import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.dashboard import format_dashboard_telegram, render_dashboard, save_dashboard, summarize_trades
from services import performance_stats
from services.database import DatabaseService
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)

def main() -> None:
    config = load_config()
    db = DatabaseService(config)
    telegram = TelegramService(config)

    # 150 mirrors the web API's default (dashboard/api/dashboard.js), so both
    # surfaces summarise the same set of trades.
    #
    # This used to pull 80 rows with get_recent_trades and let the summary
    # filter them. Ordering by created_at meant pending, cancelled and open
    # rows consumed the window before the filter ran: the card reported 52
    # closed trades while the website, which queries closed rows directly,
    # reported 85. Identical maths over different samples cannot agree.
    limit = int(os.environ.get("DASHBOARD_TRADE_LIMIT", "150"))
    output = os.environ.get("DASHBOARD_OUTPUT", "storage/dashboard.html")

    trades = db.get_recent_closed_trades(limit=limit)
    # Open positions are shown separately on the card and never folded into
    # the realised figures; see services/performance_stats.py.
    trades += [t for t in (db.get_open_trades() or [])
               if str(t.get("status") or "").upper() in performance_stats.OPEN_STATUSES]
    html_text = render_dashboard(trades)
    output_path = save_dashboard(html_text, output)
    summary = summarize_trades(trades)

    logger.info("Dashboard generated: %s | trades=%s", output_path, len(trades))
    print(f"Dashboard generated: {output_path}")
    print(summary)

    if os.environ.get("SEND_TELEGRAM", "true").lower() in {"1", "true", "yes"}:
        telegram.send_message(format_dashboard_telegram(summary))

if __name__ == "__main__":
    main()
