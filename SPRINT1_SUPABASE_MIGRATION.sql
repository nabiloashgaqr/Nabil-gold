-- =====================================================
-- Nabil Gold — Sprint 1 Incremental Supabase Migration
-- Purpose:
--   1) Add setup metadata columns to trades
--   2) Allow PENDING status in trades
--   3) Create setup_candidates table
--   4) Refresh active_trades_view to include PENDING
-- Safe to run in Supabase SQL Editor as an incremental patch.
-- =====================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------
-- 0) Common timestamp trigger helper (safe re-create)
-- -----------------------------------------------------
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- -----------------------------------------------------
-- 1) Extend trades table with setup metadata
-- -----------------------------------------------------
ALTER TABLE trades ADD COLUMN IF NOT EXISTS setup_id            TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS setup_type          TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS setup_state         TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS lead_agent          TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS setup_quality       TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS poi_type            TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sweep_side          TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS displacement_score  DECIMAL(10, 4);

-- -----------------------------------------------------
-- 2) Update trades.status check constraint to allow PENDING
-- -----------------------------------------------------
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'trades'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%status%'
    LOOP
        EXECUTE format('ALTER TABLE trades DROP CONSTRAINT IF EXISTS %I', r.conname);
    END LOOP;
END $$;

ALTER TABLE trades
    ADD CONSTRAINT trades_status_check CHECK (
        status IN (
            'OPEN', 'PARTIAL', 'PENDING', 'TP1_HIT', 'TP2_HIT', 'SL_HIT', 'BE_HIT',
            'MANUAL_CLOSE', 'EXPIRED', 'CLOSED', 'CANCELLED'
        )
    );

-- -----------------------------------------------------
-- 3) Indexes for new trade metadata
-- -----------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_trades_setup_type ON trades(setup_type);
CREATE INDEX IF NOT EXISTS idx_trades_lead_agent ON trades(lead_agent);

DROP INDEX IF EXISTS idx_trades_open;
CREATE INDEX idx_trades_open ON trades(status)
WHERE status IN ('OPEN', 'PARTIAL', 'PENDING', 'TP1_HIT');

-- -----------------------------------------------------
-- 4) Setup candidates table
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS setup_candidates (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL DEFAULT 'XAU/USD',
    timeframe TEXT,
    direction TEXT,
    setup_type TEXT,
    setup_state TEXT,
    lead_agent TEXT,
    setup_quality TEXT,
    quality_score DECIMAL(10, 4),
    poi_type TEXT,
    poi_low DECIMAL(18, 6),
    poi_high DECIMAL(18, 6),
    entry_price DECIMAL(18, 6),
    stop_loss DECIMAL(18, 6),
    target_price DECIMAL(18, 6),
    sweep_side TEXT,
    displacement_score DECIMAL(10, 4),
    confidence DECIMAL(10, 4),
    details JSONB DEFAULT '{}'::jsonb,
    source TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_trade_id TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS symbol             TEXT DEFAULT 'XAU/USD';
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS timeframe          TEXT;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS direction          TEXT;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS setup_type         TEXT;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS setup_state        TEXT;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS lead_agent         TEXT;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS setup_quality      TEXT;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS quality_score      DECIMAL(10, 4);
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS poi_type           TEXT;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS poi_low            DECIMAL(18, 6);
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS poi_high           DECIMAL(18, 6);
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS entry_price        DECIMAL(18, 6);
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS stop_loss          DECIMAL(18, 6);
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS target_price       DECIMAL(18, 6);
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS sweep_side         TEXT;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS displacement_score DECIMAL(10, 4);
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS confidence         DECIMAL(10, 4);
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS details            JSONB DEFAULT '{}'::jsonb;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS source             TEXT;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS is_active          BOOLEAN DEFAULT TRUE;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS first_seen_at      TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS last_seen_at       TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS last_trade_id      TEXT;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_setup_candidates_symbol ON setup_candidates(symbol);
CREATE INDEX IF NOT EXISTS idx_setup_candidates_type ON setup_candidates(setup_type);
CREATE INDEX IF NOT EXISTS idx_setup_candidates_state ON setup_candidates(setup_state);
CREATE INDEX IF NOT EXISTS idx_setup_candidates_last_seen ON setup_candidates(last_seen_at DESC);

DROP TRIGGER IF EXISTS update_setup_candidates_timestamp ON setup_candidates;
CREATE TRIGGER update_setup_candidates_timestamp
    BEFORE UPDATE ON setup_candidates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- -----------------------------------------------------
-- 5) Refresh active trades view to include PENDING
-- -----------------------------------------------------
DROP VIEW IF EXISTS active_trades_view;
CREATE VIEW active_trades_view AS
SELECT *
FROM trades
WHERE status IN ('OPEN', 'PARTIAL', 'PENDING', 'TP1_HIT');

-- -----------------------------------------------------
-- 6) Enable RLS (service_role bypasses automatically)
-- -----------------------------------------------------
ALTER TABLE setup_candidates ENABLE ROW LEVEL SECURITY;

-- -----------------------------------------------------
-- 7) Reload PostgREST schema cache
-- -----------------------------------------------------
NOTIFY pgrst, 'reload schema';

-- =====================================================
-- End of Sprint 1 migration
-- =====================================================
