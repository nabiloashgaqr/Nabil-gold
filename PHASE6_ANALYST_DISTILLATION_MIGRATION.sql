-- =====================================================
-- Nabil Gold — Phase 6 Analyst Distillation Migration
-- Purpose:
--   1) Create analyst_labels table
--   2) Create analyst_comparisons table
--   3) Reload PostgREST schema cache
-- Safe incremental patch for Supabase SQL Editor.
-- =====================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS analyst_labels (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL DEFAULT 'XAU/USD',
    timeframe TEXT,
    analyst_name TEXT,
    bias TEXT,
    setup_type TEXT,
    sweep_side TEXT,
    poi_type TEXT,
    poi_quality_grade TEXT,
    intended_entry DECIMAL(18, 6),
    invalidation TEXT,
    tp1 DECIMAL(18, 6),
    tp2 DECIMAL(18, 6),
    session_label TEXT,
    trade_decision TEXT,
    notes TEXT,
    source TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS symbol            TEXT DEFAULT 'XAU/USD';
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS timeframe         TEXT;
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS analyst_name      TEXT;
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS bias              TEXT;
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS setup_type        TEXT;
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS sweep_side        TEXT;
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS poi_type          TEXT;
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS poi_quality_grade TEXT;
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS intended_entry    DECIMAL(18, 6);
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS invalidation      TEXT;
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS tp1               DECIMAL(18, 6);
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS tp2               DECIMAL(18, 6);
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS session_label     TEXT;
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS trade_decision    TEXT;
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS notes             TEXT;
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS source            TEXT;
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS created_at        TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE analyst_labels ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_analyst_labels_symbol ON analyst_labels(symbol);
CREATE INDEX IF NOT EXISTS idx_analyst_labels_setup_type ON analyst_labels(setup_type);
CREATE INDEX IF NOT EXISTS idx_analyst_labels_created ON analyst_labels(created_at DESC);

DROP TRIGGER IF EXISTS update_analyst_labels_timestamp ON analyst_labels;
CREATE TRIGGER update_analyst_labels_timestamp
    BEFORE UPDATE ON analyst_labels
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TABLE IF NOT EXISTS analyst_comparisons (
    id TEXT PRIMARY KEY,
    analyst_label_id TEXT,
    setup_candidate_id TEXT,
    symbol TEXT DEFAULT 'XAU/USD',
    match_score DECIMAL(10, 4),
    classification TEXT,
    summary TEXT,
    payload JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE analyst_comparisons ADD COLUMN IF NOT EXISTS analyst_label_id   TEXT;
ALTER TABLE analyst_comparisons ADD COLUMN IF NOT EXISTS setup_candidate_id TEXT;
ALTER TABLE analyst_comparisons ADD COLUMN IF NOT EXISTS symbol             TEXT DEFAULT 'XAU/USD';
ALTER TABLE analyst_comparisons ADD COLUMN IF NOT EXISTS match_score        DECIMAL(10, 4);
ALTER TABLE analyst_comparisons ADD COLUMN IF NOT EXISTS classification     TEXT;
ALTER TABLE analyst_comparisons ADD COLUMN IF NOT EXISTS summary            TEXT;
ALTER TABLE analyst_comparisons ADD COLUMN IF NOT EXISTS payload            JSONB DEFAULT '{}'::jsonb;
ALTER TABLE analyst_comparisons ADD COLUMN IF NOT EXISTS created_at         TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE analyst_comparisons ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_analyst_comparisons_label ON analyst_comparisons(analyst_label_id);
CREATE INDEX IF NOT EXISTS idx_analyst_comparisons_symbol ON analyst_comparisons(symbol);
CREATE INDEX IF NOT EXISTS idx_analyst_comparisons_created ON analyst_comparisons(created_at DESC);

DROP TRIGGER IF EXISTS update_analyst_comparisons_timestamp ON analyst_comparisons;
CREATE TRIGGER update_analyst_comparisons_timestamp
    BEFORE UPDATE ON analyst_comparisons
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

ALTER TABLE analyst_labels ENABLE ROW LEVEL SECURITY;
ALTER TABLE analyst_comparisons ENABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';

-- =====================================================
-- End of Phase 6 migration
-- =====================================================
