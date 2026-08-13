"""One-way migration: Supabase -> local storage (operator directive 2026-08-10).

Run ON THE VPS while SUPABASE_URL/KEY are still in .env. It copies the FULL
history — paper trades + demo trades + session plans + setup candidates +
state events + decision audit + latest macro context — into the storage/*.json
files the local mode reads, merging BOTH trade books into ONE continuous
book (old first, new continues on top; dedup by id).

Afterwards: comment SUPABASE_URL / SUPABASE_KEY out of .env and restart the
two long-lived processes. The system then runs 100% locally with the complete
history. Re-runnable: existing local files are backed up first.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.dashboard import merge_trade_rows  # noqa: E402

STORAGE = ROOT / "storage"
LIMIT = 20000


def _fetch_all(client, table: str) -> list:
    """Fetch every row of a table with simple keyset-free pagination."""
    rows: list = []
    offset = 0
    while True:
        resp = client.table(table).select("*").range(offset, offset + 999).execute()
        batch = list(resp.data or [])
        rows.extend(batch)
        offset += len(batch)
        if len(batch) < 1000 or offset >= LIMIT:
            break
    return rows


def _backup_existing() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bdir = STORAGE / f"backup_{stamp}"
    bdir.mkdir(parents=True, exist_ok=True)
    for f in STORAGE.glob("*.json"):
        shutil.copy2(f, bdir / f.name)
    return bdir


def _write(name: str, rows) -> int:
    path = STORAGE / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    return len(rows) if isinstance(rows, list) else 1


def main() -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("SUPABASE_URL/KEY not set — nothing to migrate from.")
        sys.exit(1)
    from supabase import create_client
    client = create_client(url, key)

    bdir = _backup_existing()
    print(f"[backup] existing local JSON copied to {bdir}")

    paper = _fetch_all(client, "trades")
    demo = _fetch_all(client, "trades_demo")
    merged = merge_trade_rows(paper, demo)
    merged.sort(key=lambda r: str(r.get("created_at") or ""))
    print(f"[trades] paper={len(paper)} demo={len(demo)} merged={len(merged)}")
    _write("trades.json", merged)

    for table, fname in [
        ("session_plans", "session_plans.json"),
        ("setup_candidates", "setup_candidates.json"),
        ("setup_state_events", "setup_state_events.json"),
        ("decision_audit", "decision_audit.json"),
    ]:
        rows = _fetch_all(client, table)
        print(f"[{table}] rows={len(rows)}")
        _write(fname, rows)

    resp = client.table("macro_context").select("context").eq("id", "latest").limit(1).execute()
    mrows = list(resp.data or [])
    if mrows and isinstance(mrows[0].get("context"), dict):
        _write("macro_context.json", mrows[0]["context"])
        print("[macro_context] latest snapshot saved")

    print("MIGRATION DONE. Next: comment SUPABASE_URL/SUPABASE_KEY in .env,")
    print("restart SS_TickManager + SS_DemoLoop, and verify logs show")
    print("'Supabase credentials missing, using local fallback JSON'.")


if __name__ == "__main__":
    main()
