-- =====================================================
-- Nabil Gold — Sprint 2 Setup-State Migration
-- Purpose:
--   1) Extend setup_candidates with stateful fields
--   2) Create setup_state_events table
--   3) Reload schema cache
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

-- -----------------------------------------------------
-- 1) Extend setup_candidates with state-memory columns
-- -----------------------------------------------------
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS state_key          TEXT;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS last_transition_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS transition_count   INTEGER DEFAULT 0;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS missing_cycles     INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_setup_candidates_state_key ON setup_candidates(state_key);

-- -----------------------------------------------------
-- 2) Setup state events audit table
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS setup_state_events (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    setup_id TEXT,
    state_key TEXT,
    symbol TEXT DEFAULT 'XAU/USD',
    from_state TEXT,
    to_state TEXT,
    reason TEXT,
    price DECIMAL(18, 6),
    payload JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE setup_state_events ADD COLUMN IF NOT EXISTS setup_id    TEXT;
ALTER TABLE setup_state_events ADD COLUMN IF NOT EXISTS state_key   TEXT;
ALTER TABLE setup_state_events ADD COLUMN IF NOT EXISTS symbol      TEXT DEFAULT 'XAU/USD';
ALTER TABLE setup_state_events ADD COLUMN IF NOT EXISTS from_state  TEXT;
ALTER TABLE setup_state_events ADD COLUMN IF NOT EXISTS to_state    TEXT;
ALTER TABLE setup_state_events ADD COLUMN IF NOT EXISTS reason      TEXT;
ALTER TABLE setup_state_events ADD COLUMN IF NOT EXISTS price       DECIMAL(18, 6);
ALTER TABLE setup_state_events ADD COLUMN IF NOT EXISTS payload     JSONB DEFAULT '{}'::jsonb;
ALTER TABLE setup_state_events ADD COLUMN IF NOT EXISTS created_at  TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE setup_state_events ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_setup_state_events_setup_id ON setup_state_events(setup_id);
CREATE INDEX IF NOT EXISTS idx_setup_state_events_state_key ON setup_state_events(state_key);
CREATE INDEX IF NOT EXISTS idx_setup_state_events_symbol ON setup_state_events(symbol);
CREATE INDEX IF NOT EXISTS idx_setup_state_events_created ON setup_state_events(created_at DESC);

DROP TRIGGER IF EXISTS update_setup_state_events_timestamp ON setup_state_events;
CREATE TRIGGER update_setup_state_events_timestamp
    BEFORE UPDATE ON setup_state_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

ALTER TABLE setup_state_events ENABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';

-- =====================================================
-- End of Sprint 2 migration
-- =====================================================
