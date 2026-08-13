-- Phase 2 (demo/mt5): demo isolation tables. Run ONCE in Supabase SQL editor.
CREATE TABLE IF NOT EXISTS trades_demo (LIKE trades INCLUDING ALL);
ALTER TABLE trades_demo ADD COLUMN IF NOT EXISTS mt5_ticket BIGINT;
ALTER TABLE trades_demo ADD COLUMN IF NOT EXISTS mt5_account BIGINT;
ALTER TABLE trades_demo ADD COLUMN IF NOT EXISTS execution_mode TEXT DEFAULT 'mt5_demo';

CREATE TABLE IF NOT EXISTS demo_metrics (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ DEFAULT NOW(),
  trade_id TEXT,
  event TEXT,
  slippage_points NUMERIC(18, 6),
  latency_ms INTEGER,
  mismatch TEXT
);

-- quick check
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'trades_demo' AND column_name IN ('mt5_ticket','mt5_account','execution_mode');
