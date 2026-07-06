"""Update hourly macro context for quality scoring and learning.

This job fetches all macro data from Yahoo Finance (yfinance) — completely free,
no API key, no quota limits. The latest context is saved to Supabase when available
and to storage/macro_context.json for local/manual runs.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService  # noqa: E402
from services.macro_data_provider import MacroDataProvider  # noqa: E402
from utils.helpers import load_config, setup_logging  # noqa: E402

setup_logging()
logger = logging.getLogger(__name__)


def main() -> int:
    config = load_config()
    context = MacroDataProvider(config).build_context()
    storage_path = ROOT / "storage" / "macro_context.json"
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    saved_supabase = False
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"):
        db = DatabaseService(config)
        saved_supabase = db.save_macro_context(context)

    qp = context.get("quota_policy", {}) or {}
    print("✅ Macro context updated")
    print(f"source={context.get('source')} usd_trend={context.get('usd_trend')} risk={context.get('risk_sentiment')}")
    print(f"credits_used_estimate={qp.get('credits_used_estimate')} hourly_daily_estimate={qp.get('daily_estimate_at_hourly')}/800")
    print(f"saved_local={storage_path} saved_supabase={saved_supabase}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
