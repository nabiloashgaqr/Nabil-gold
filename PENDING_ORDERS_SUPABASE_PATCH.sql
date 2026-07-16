-- =====================================================
-- Nabil Gold — Pending Orders / Setup-State Supabase Patch
-- Ready to paste into Supabase SQL Editor
--
-- This patch does 4 things:
--   1) Ensures trades supports PENDING orders cleanly
--   2) Adds pending-order fields used by the latest code
--   3) Creates setup_candidates + setup_state_events
--   4) Refreshes active_trades_view to include PENDING
--
-- Safe to run more than once.
-- =====================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------
-- Common updated_at trigger helper
-- -----------------------------------------------------
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- 1) TRADES TABLE: pending-order support
-- =====================================================

-- Setup / strategy metadata
ALTER TABLE trades ADD COLUMN IF NOT EXISTS setup_id            TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS setup_type          TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS setup_state         TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS lead_agent          TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS setup_quality       TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS poi_type            TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sweep_side          TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS displacement_score  DECIMAL(10, 4);

-- Pending-order lifecycle fields used by the latest execution logic
ALTER TABLE trades ADD COLUMN IF NOT EXISTS order_kind          TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS order_type          TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS pending_cycles      INTEGER DEFAULT 0;

-- Rebuild status check so PENDING is allowed
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

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_trades_setup_type   ON trades(setup_type);
CREATE INDEX IF NOT EXISTS idx_trades_lead_agent   ON trades(lead_agent);
CREATE INDEX IF NOT EXISTS idx_trades_order_type   ON trades(order_type);
CREATE INDEX IF NOT EXISTS idx_trades_order_kind   ON trades(order_kind);
CREATE INDEX IF NOT EXISTS idx_trades_pending_only ON trades(created_at DESC) WHERE status = 'PENDING';

DROP INDEX IF EXISTS idx_trades_open;
CREATE INDEX idx_trades_open ON trades(status)
WHERE status IN ('OPEN', 'PARTIAL', 'PENDING', 'TP1_HIT');

-- =====================================================
-- 2) SETUP CANDIDATES TABLE
-- =====================================================
CREATE TABLE IF NOT EXISTS setup_candidates (
    id TEXT PRIMARY KEY,
    state_key TEXT,
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
    last_transition_at TIMESTAMPTZ DEFAULT NOW(),
    transition_count INTEGER DEFAULT 0,
    missing_cycles INTEGER DEFAULT 0,
    last_trade_id TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS state_key          TEXT;
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
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS last_transition_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS transition_count   INTEGER DEFAULT 0;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS missing_cycles     INTEGER DEFAULT 0;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS last_trade_id      TEXT;
ALTER TABLE setup_candidates ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_setup_candidates_state_key ON setup_candidates(state_key);
CREATE INDEX IF NOT EXISTS idx_setup_candidates_symbol    ON setup_candidates(symbol);
CREATE INDEX IF NOT EXISTS idx_setup_candidates_type      ON setup_candidates(setup_type);
CREATE INDEX IF NOT EXISTS idx_setup_candidates_state     ON setup_candidates(setup_state);
CREATE INDEX IF NOT EXISTS idx_setup_candidates_last_seen ON setup_candidates(last_seen_at DESC);

DROP TRIGGER IF EXISTS update_setup_candidates_timestamp ON setup_candidates;
CREATE TRIGGER update_setup_candidates_timestamp
    BEFORE UPDATE ON setup_candidates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =====================================================
-- 3) SETUP STATE EVENTS TABLE
-- =====================================================
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

CREATE INDEX IF NOT EXISTS idx_setup_state_events_setup_id   ON setup_state_events(setup_id);
CREATE INDEX IF NOT EXISTS idx_setup_state_events_state_key  ON setup_state_events(state_key);
CREATE INDEX IF NOT EXISTS idx_setup_state_events_symbol     ON setup_state_events(symbol);
CREATE INDEX IF NOT EXISTS idx_setup_state_events_created    ON setup_state_events(created_at DESC);

DROP TRIGGER IF EXISTS update_setup_state_events_timestamp ON setup_state_events;
CREATE TRIGGER update_setup_state_events_timestamp
    BEFORE UPDATE ON setup_state_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =====================================================
-- 4) ACTIVE TRADES VIEW MUST INCLUDE PENDING
-- =====================================================
DROP VIEW IF EXISTS active_trades_view;
CREATE VIEW active_trades_view AS
SELECT *
FROM trades
WHERE status IN ('OPEN', 'PARTIAL', 'PENDING', 'TP1_HIT');

-- =====================================================
-- 5) RLS + schema reload
-- =====================================================
ALTER TABLE setup_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE setup_state_events ENABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';

-- =====================================================
-- Optional quick checks after running:
-- select id, symbol, type, status, order_type, order_kind, entry_price, created_at
-- from trades
-- where status = 'PENDING'
-- order by created_at desc
-- limit 20;
-- =====================================================
