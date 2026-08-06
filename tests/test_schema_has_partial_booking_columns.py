"""Regression: the trades schema must declare every column the engine writes
for partial TP1 booking.

Live evidence (2026-08-06): running the operator fix for trade d4351dad failed
with 42703 "column closed_fraction of relation trades does not exist". The
engine (open_trades_manager, updates['realized_pnl_points'/'closed_fraction'/
'scale_out_price']) has written these keys since the TP1 partial-booking fix,
but the unified schema never declared them -- so every live full update hit
PGRST204 and the drop-missing-columns retry silently discarded the booking
numbers forever.

If these columns are removed from supabase_schema_unified.sql again, this
test fails.
"""

from __future__ import annotations

import re
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "supabase_schema_unified.sql"

REQUIRED = {
    "closed_fraction": r"closed_fraction\s+DECIMAL",
    "realized_pnl_points": r"realized_pnl_points\s+DECIMAL",
    "scale_out_price": r"scale_out_price\s+DECIMAL",
}


def test_partial_booking_columns_declared_in_create_table():
    text = SCHEMA.read_text(encoding="utf-8")
    create_block = text.split("CREATE TABLE IF NOT EXISTS trades", 1)[1]
    create_block = create_block.split(");", 1)[0]
    for column, pattern in REQUIRED.items():
        assert re.search(pattern, create_block), (
            f"trades CREATE TABLE is missing '{column}' -- the engine writes it "
            f"on every TP1 partial booking and it would be dropped live"
        )


def test_partial_booking_columns_in_alter_migration_section():
    text = SCHEMA.read_text(encoding="utf-8")
    for column in REQUIRED:
        assert re.search(
            rf"ADD COLUMN IF NOT EXISTS\s+{column}\b", text
        ), f"migration section must ADD COLUMN IF NOT EXISTS {column}"
