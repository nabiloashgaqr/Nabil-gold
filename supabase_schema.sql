-- =====================================================
-- Gold AI Signals - Supabase Schema v2
-- Compatible with current Python services.
-- Recommended: use SUPABASE_KEY as a Service Role key in GitHub Secrets.
-- Do NOT expose this database for public writes with anon keys.
-- =====================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1) Signals table (optional audit trail)
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    signal_type VARCHAR(10) NOT NULL CHECK (signal_type IN ('BUY', 'SELL', 'WAIT')),
    symbol VARCHAR(20) NOT NULL DEFAULT 'XAU/USD',
    entry_price DECIMAL(18, 4),
    confidence_score DECIMAL(5, 2) DEFAULT 0,
    quality VARCHAR(20),
    session_name VARCHAR(100),
    session_quality VARCHAR(20),
    signal_reason TEXT,
    agent_inputs JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =====================================================
-- SELF-HEAL block for EXISTING signals tables
-- -----------------------------------------------------
-- An older 'signals' table created before these columns existed will be
-- missing them (CREATE TABLE IF NOT EXISTS above is skipped when the table
-- already exists). That is why "column is_active does not exist" was raised
-- by the index below. These ALTERs add any missing column without touching
-- existing data and are safe to re-run. No NOT NULL is forced here so the
-- statements never fail on tables that already contain rows.
-- =====================================================
ALTER TABLE signals ADD COLUMN IF NOT EXISTS signal_type      VARCHAR(10);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS symbol           VARCHAR(20) DEFAULT 'XAU/USD';
ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_price      DECIMAL(18, 4);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS confidence_score DECIMAL(5, 2) DEFAULT 0;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS quality          VARCHAR(20);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS session_name     VARCHAR(100);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS session_quality  VARCHAR(20);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS signal_reason    TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS agent_inputs     JSONB DEFAULT '{}'::jsonb;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS is_active        BOOLEAN DEFAULT TRUE;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS expires_at       TIMESTAMPTZ;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS created_at       TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE signals ADD COLUMN IF NOT EXISTS updated_at       TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_signals_active ON signals(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_type ON signals(signal_type);

-- 2) Trades table: matches services.database.DatabaseService payload.
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    signal_id TEXT REFERENCES signals(id) ON DELETE SET NULL,
    type VARCHAR(10) CHECK (type IN ('BUY', 'SELL')),
    trade_type VARCHAR(10) GENERATED ALWAYS AS (type) STORED,
    symbol VARCHAR(20) NOT NULL DEFAULT 'XAU/USD',

    entry_price DECIMAL(18, 4) NOT NULL,
    entry_time TIMESTAMPTZ,
    stop_loss DECIMAL(18, 4),
    initial_stop_loss DECIMAL(18, 4),
    tp1 DECIMAL(18, 4),
    tp2 DECIMAL(18, 4),

    confidence INTEGER DEFAULT 0,
    trading_mode VARCHAR(20) DEFAULT 'paper' CHECK (trading_mode IN ('paper', 'live', 'demo', 'manual')),
    paper_trading BOOLEAN DEFAULT TRUE,
    paper_balance_start DECIMAL(18, 4),
    paper_lot_size DECIMAL(18, 6),
    status VARCHAR(30) DEFAULT 'OPEN' CHECK (status IN (
        'OPEN', 'PARTIAL', 'TP1_HIT', 'TP2_HIT', 'SL_HIT', 'BE_HIT',
        'MANUAL_CLOSE', 'EXPIRED', 'CLOSED', 'PENDING', 'CANCELLED'
    )),

    current_price DECIMAL(18, 4),
    current_pnl DECIMAL(18, 4) DEFAULT 0,
    current_pnl_points DECIMAL(18, 4) DEFAULT 0,
    final_pnl DECIMAL(18, 4),

    sl_moved_to_entry BOOLEAN DEFAULT FALSE,
    partial_close BOOLEAN DEFAULT FALSE,
    updates_sent JSONB DEFAULT '[]'::jsonb,
    exit_warning BOOLEAN DEFAULT FALSE,
    management_phase VARCHAR(40),
    max_favorable_excursion DECIMAL(18, 4) DEFAULT 0,
    max_adverse_excursion DECIMAL(18, 4) DEFAULT 0,
    result VARCHAR(30),
    reasons JSONB DEFAULT '[]'::jsonb,
    signal_snapshot JSONB DEFAULT '{}'::jsonb,
    ai_reviewed BOOLEAN DEFAULT FALSE,
    ai_review JSONB,
    memory_rule_ids TEXT[] DEFAULT ARRAY[]::TEXT[],

    created_at TIMESTAMPTZ DEFAULT NOW(),
    opened_at TIMESTAMPTZ GENERATED ALWAYS AS (entry_time) STORED,
    closed_at TIMESTAMPTZ,
    close_time TIMESTAMPTZ,
    close_price DECIMAL(18, 4),
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =====================================================
-- SELF-HEAL block for EXISTING trades tables
-- -----------------------------------------------------
-- CREATE TABLE IF NOT EXISTS above is skipped entirely when the table already
-- exists, so an older table created before these columns were added will be
-- MISSING them (this is what causes errors like PGRST204
-- "Could not find the 'confidence' column of 'trades'" and
-- "column symbol does not exist" raised by the indexes below).
-- The ALTER ... ADD COLUMN IF NOT EXISTS statements MUST run BEFORE the
-- CREATE INDEX statements that reference these columns. They add any missing
-- column without touching existing data, and are safe to re-run any time.
-- =====================================================
ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_id           TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS type                VARCHAR(10);
-- 'side' is written by the Python code alongside 'type' and was never in the base schema:
ALTER TABLE trades ADD COLUMN IF NOT EXISTS side                VARCHAR(10);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS symbol              VARCHAR(20) DEFAULT 'XAU/USD';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_price         DECIMAL(18, 4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_time          TIMESTAMPTZ;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_loss           DECIMAL(18, 4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS initial_stop_loss   DECIMAL(18, 4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS tp1                 DECIMAL(18, 4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS tp2                 DECIMAL(18, 4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS confidence          INTEGER DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS trading_mode        VARCHAR(20) DEFAULT 'paper';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS paper_trading       BOOLEAN DEFAULT TRUE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS paper_balance_start DECIMAL(18, 4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS paper_lot_size      DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS status              VARCHAR(30) DEFAULT 'OPEN';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS current_price       DECIMAL(18, 4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS current_pnl         DECIMAL(18, 4) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS current_pnl_points  DECIMAL(18, 4) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS final_pnl           DECIMAL(18, 4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sl_moved_to_entry   BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS partial_close       BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS updates_sent        JSONB DEFAULT '[]'::jsonb;
-- Trade-management telemetry written by OpenTradesManager (added later).
-- Without these, hourly updates hit "Could not find the 'exit_warning' column"
-- (PGRST204) and fall back to a reduced payload.
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_warning            BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS management_phase        VARCHAR(40);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS max_favorable_excursion DECIMAL(18, 4) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS max_adverse_excursion   DECIMAL(18, 4) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS result              VARCHAR(30);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS reasons             JSONB DEFAULT '[]'::jsonb;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_snapshot     JSONB DEFAULT '{}'::jsonb;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS ai_reviewed         BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS ai_review           JSONB;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS memory_rule_ids     TEXT[] DEFAULT ARRAY[]::TEXT[];
ALTER TABLE trades ADD COLUMN IF NOT EXISTS created_at          TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE trades ADD COLUMN IF NOT EXISTS closed_at           TIMESTAMPTZ;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS close_time          TIMESTAMPTZ;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS close_price         DECIMAL(18, 4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS last_updated        TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE trades ADD COLUMN IF NOT EXISTS updated_at          TIMESTAMPTZ DEFAULT NOW();

-- Indexes come AFTER the heal block so they never reference a missing column.
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_open ON trades(status) WHERE status IN ('OPEN', 'PARTIAL', 'TP1_HIT');

-- AI Memory Rules table
CREATE TABLE IF NOT EXISTS ai_memory_rules (
    id TEXT PRIMARY KEY,
    rule_text TEXT NOT NULL,
    category VARCHAR(80) DEFAULT 'AI_REVIEW_LESSON',
    applies_to VARCHAR(20) DEFAULT 'BOTH' CHECK (applies_to IN ('BUY', 'SELL', 'BOTH')),
    confidence INTEGER DEFAULT 70,
    source_trade_id TEXT,
    source VARCHAR(80) DEFAULT 'ai_trade_review',
    active BOOLEAN DEFAULT TRUE,
    times_triggered INTEGER DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- SELF-HEAL: ensure columns exist on an older ai_memory_rules table.
ALTER TABLE ai_memory_rules ADD COLUMN IF NOT EXISTS rule_text       TEXT;
ALTER TABLE ai_memory_rules ADD COLUMN IF NOT EXISTS category        VARCHAR(80) DEFAULT 'AI_REVIEW_LESSON';
ALTER TABLE ai_memory_rules ADD COLUMN IF NOT EXISTS applies_to      VARCHAR(20) DEFAULT 'BOTH';
ALTER TABLE ai_memory_rules ADD COLUMN IF NOT EXISTS confidence      INTEGER DEFAULT 70;
ALTER TABLE ai_memory_rules ADD COLUMN IF NOT EXISTS source_trade_id TEXT;
ALTER TABLE ai_memory_rules ADD COLUMN IF NOT EXISTS source          VARCHAR(80) DEFAULT 'ai_trade_review';
ALTER TABLE ai_memory_rules ADD COLUMN IF NOT EXISTS active          BOOLEAN DEFAULT TRUE;
ALTER TABLE ai_memory_rules ADD COLUMN IF NOT EXISTS times_triggered INTEGER DEFAULT 0;
ALTER TABLE ai_memory_rules ADD COLUMN IF NOT EXISTS metadata        JSONB DEFAULT '{}'::jsonb;
ALTER TABLE ai_memory_rules ADD COLUMN IF NOT EXISTS created_at      TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE ai_memory_rules ADD COLUMN IF NOT EXISTS updated_at      TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_ai_memory_rules_active ON ai_memory_rules(active, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_ai_memory_rules_source_trade ON ai_memory_rules(source_trade_id);

-- AI trade reviews table
CREATE TABLE IF NOT EXISTS ai_trade_reviews (
    id TEXT PRIMARY KEY,
    trade_id TEXT,
    reviewed_at TIMESTAMPTZ DEFAULT NOW(),
    provider VARCHAR(50),
    model VARCHAR(100),
    tokens_used INTEGER DEFAULT 0,
    review JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- SELF-HEAL: ensure columns exist on an older ai_trade_reviews table.
ALTER TABLE ai_trade_reviews ADD COLUMN IF NOT EXISTS trade_id    TEXT;
ALTER TABLE ai_trade_reviews ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE ai_trade_reviews ADD COLUMN IF NOT EXISTS provider    VARCHAR(50);
ALTER TABLE ai_trade_reviews ADD COLUMN IF NOT EXISTS model       VARCHAR(100);
ALTER TABLE ai_trade_reviews ADD COLUMN IF NOT EXISTS tokens_used INTEGER DEFAULT 0;
ALTER TABLE ai_trade_reviews ADD COLUMN IF NOT EXISTS review      JSONB DEFAULT '{}'::jsonb;
ALTER TABLE ai_trade_reviews ADD COLUMN IF NOT EXISTS created_at  TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_ai_trade_reviews_trade ON ai_trade_reviews(trade_id);
CREATE INDEX IF NOT EXISTS idx_ai_trade_reviews_reviewed ON ai_trade_reviews(reviewed_at DESC);

-- 3) Portfolio summary
CREATE TABLE IF NOT EXISTS portfolio (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    balance DECIMAL(18, 4) DEFAULT 10000.00,
    equity DECIMAL(18, 4) DEFAULT 10000.00,
    available_margin DECIMAL(18, 4) DEFAULT 10000.00,
    used_margin DECIMAL(18, 4) DEFAULT 0,
    open_positions INTEGER DEFAULT 0,
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    win_rate DECIMAL(5, 2) DEFAULT 0,
    total_pnl DECIMAL(18, 4) DEFAULT 0,
    max_drawdown DECIMAL(18, 4) DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4) Daily reports
CREATE TABLE IF NOT EXISTS daily_reports (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    report_date DATE NOT NULL,
    total_signals INTEGER DEFAULT 0,
    buy_signals INTEGER DEFAULT 0,
    sell_signals INTEGER DEFAULT 0,
    wait_signals INTEGER DEFAULT 0,
    new_trades INTEGER DEFAULT 0,
    closed_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    daily_pnl DECIMAL(18, 4) DEFAULT 0,
    win_rate DECIMAL(5, 2) DEFAULT 0,
    market_summary TEXT,
    technical_summary TEXT,
    recommendations TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- SELF-HEAL: ensure columns exist on an older daily_reports table.
ALTER TABLE daily_reports ADD COLUMN IF NOT EXISTS report_date  DATE;
ALTER TABLE daily_reports ADD COLUMN IF NOT EXISTS created_at   TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_daily_reports_date ON daily_reports(report_date DESC);

-- 5) News log
CREATE TABLE IF NOT EXISTS news_log (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    headline TEXT NOT NULL,
    source VARCHAR(100),
    impact VARCHAR(20) CHECK (impact IN ('HIGH', 'MEDIUM', 'LOW')),
    affected_pairs TEXT[],
    trading_impact VARCHAR(20) DEFAULT 'NEUTRAL',
    confidence_adjustment DECIMAL(5, 2) DEFAULT 0,
    sentiment VARCHAR(20) DEFAULT 'NEUTRAL',
    logged_at TIMESTAMPTZ DEFAULT NOW()
);
-- SELF-HEAL: ensure columns exist on an older news_log table.
ALTER TABLE news_log ADD COLUMN IF NOT EXISTS impact    VARCHAR(20);
ALTER TABLE news_log ADD COLUMN IF NOT EXISTS logged_at TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_news_impact ON news_log(impact);
CREATE INDEX IF NOT EXISTS idx_news_logged ON news_log(logged_at DESC);

-- 6) Risk settings
CREATE TABLE IF NOT EXISTS risk_settings (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    setting_key VARCHAR(50) UNIQUE NOT NULL,
    setting_value JSONB NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- SELF-HEAL: ensure columns + UNIQUE(setting_key) exist on an older risk_settings table
-- (required by the ON CONFLICT (setting_key) seed below).
ALTER TABLE risk_settings ADD COLUMN IF NOT EXISTS setting_key   VARCHAR(50);
ALTER TABLE risk_settings ADD COLUMN IF NOT EXISTS setting_value JSONB;
ALTER TABLE risk_settings ADD COLUMN IF NOT EXISTS description   TEXT;
ALTER TABLE risk_settings ADD COLUMN IF NOT EXISTS updated_at    TIMESTAMPTZ DEFAULT NOW();
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'risk_settings_setting_key_key'
    ) THEN
        ALTER TABLE risk_settings ADD CONSTRAINT risk_settings_setting_key_key UNIQUE (setting_key);
    END IF;
END $$;

-- Seed default risk settings (OPTIONAL / cosmetic only).
-- NOTE: the Python code does NOT read this table — risk settings come from
-- config.json. An older risk_settings table may use a different column layout
-- (e.g. a NOT NULL 'setting_name' instead of 'setting_key'), which would make
-- this seed fail. Since the seed is non-essential, it is wrapped so that ANY
-- failure here is ignored and the rest of the schema script still completes.
DO $$
BEGIN
    INSERT INTO risk_settings (setting_key, setting_value, description) VALUES
    ('max_risk_per_trade', '{"value": 2, "unit": "percent"}', 'الحد الأقصى للمخاطرة لكل صفقة'),
    ('daily_risk_limit', '{"value": 6, "unit": "percent"}', 'الحد الأقصى للمخاطرة اليومية'),
    ('max_open_positions', '{"value": 3, "unit": "count"}', 'الحد الأقصى للصفقات المفتوحة'),
    ('min_confidence_threshold', '{"value": 60, "unit": "percent"}', 'الحد الأدنى للثقة'),
    ('max_drawdown_stop', '{"value": 10, "unit": "percent"}', 'وقف السحب الأقصى')
    ON CONFLICT (setting_key) DO NOTHING;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Skipped risk_settings seed (table has a different/legacy layout): %', SQLERRM;
END $$;

-- 7) Sessions
CREATE TABLE IF NOT EXISTS session_log (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    session_name VARCHAR(100) NOT NULL,
    quality VARCHAR(20),
    trading_allowed BOOLEAN NOT NULL,
    reason VARCHAR(300),
    signals_generated INTEGER DEFAULT 0,
    trades_opened INTEGER DEFAULT 0,
    total_confidence DECIMAL(5, 2) DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ
);

-- 8) Learning tables
CREATE TABLE IF NOT EXISTS agent_weights (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    agent_name VARCHAR(50) UNIQUE NOT NULL,
    weight DECIMAL(7, 6) NOT NULL DEFAULT 0.15,
    win_rate DECIMAL(5, 2) DEFAULT 0,
    total_predictions INTEGER DEFAULT 0,
    trend VARCHAR(20) DEFAULT 'STABLE' CHECK (trend IN ('IMPROVING', 'STABLE', 'DECLINING')),
    learning_score DECIMAL(7, 6) DEFAULT 0.5,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS learning_history (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    report_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agents_performance JSONB DEFAULT '{}'::jsonb,
    adjusted_weights JSONB DEFAULT '{}'::jsonb,
    previous_weights JSONB DEFAULT '{}'::jsonb,
    total_trades_analyzed INTEGER DEFAULT 0,
    overall_win_rate DECIMAL(5, 2) DEFAULT 0,
    recommendations TEXT[],
    changes_summary TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_evaluations (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    signal_id TEXT REFERENCES signals(id) ON DELETE SET NULL,
    agent_name VARCHAR(50) NOT NULL,
    predicted_signal VARCHAR(10),
    actual_outcome VARCHAR(20) CHECK (actual_outcome IN ('WIN', 'LOSS', 'NEUTRAL')),
    pnl_contribution DECIMAL(10, 4) DEFAULT 0,
    confidence_at_prediction DECIMAL(5, 2) DEFAULT 50,
    learning_adjusted BOOLEAN DEFAULT FALSE,
    trade_closed_at TIMESTAMPTZ,
    evaluated_at TIMESTAMPTZ DEFAULT NOW()
);

-- SELF-HEAL: ensure columns exist on older learning tables.
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS agent_name        VARCHAR(50);
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS weight            DECIMAL(7, 6) DEFAULT 0.15;
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS win_rate          DECIMAL(5, 2) DEFAULT 0;
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS total_predictions INTEGER DEFAULT 0;
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS trend             VARCHAR(20) DEFAULT 'STABLE';
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS learning_score    DECIMAL(7, 6) DEFAULT 0.5;
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS created_at        TIMESTAMPTZ DEFAULT NOW();
-- agent_name must be UNIQUE for the ON CONFLICT seed below; add the constraint
-- only if it is missing (wrapped so re-runs never error).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agent_weights_agent_name_key'
    ) THEN
        ALTER TABLE agent_weights ADD CONSTRAINT agent_weights_agent_name_key UNIQUE (agent_name);
    END IF;
END $$;

ALTER TABLE learning_history ADD COLUMN IF NOT EXISTS report_date           TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE learning_history ADD COLUMN IF NOT EXISTS agents_performance    JSONB DEFAULT '{}'::jsonb;
ALTER TABLE learning_history ADD COLUMN IF NOT EXISTS adjusted_weights      JSONB DEFAULT '{}'::jsonb;
ALTER TABLE learning_history ADD COLUMN IF NOT EXISTS previous_weights      JSONB DEFAULT '{}'::jsonb;
ALTER TABLE learning_history ADD COLUMN IF NOT EXISTS total_trades_analyzed INTEGER DEFAULT 0;
ALTER TABLE learning_history ADD COLUMN IF NOT EXISTS overall_win_rate      DECIMAL(5, 2) DEFAULT 0;
ALTER TABLE learning_history ADD COLUMN IF NOT EXISTS recommendations       TEXT[];
ALTER TABLE learning_history ADD COLUMN IF NOT EXISTS changes_summary       TEXT;
ALTER TABLE learning_history ADD COLUMN IF NOT EXISTS created_at            TIMESTAMPTZ DEFAULT NOW();

ALTER TABLE agent_evaluations ADD COLUMN IF NOT EXISTS agent_name      VARCHAR(50);
ALTER TABLE agent_evaluations ADD COLUMN IF NOT EXISTS trade_closed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_agent_weights_name ON agent_weights(agent_name);
CREATE INDEX IF NOT EXISTS idx_learning_history_date ON learning_history(report_date DESC);
CREATE INDEX IF NOT EXISTS idx_agent_evaluations_agent ON agent_evaluations(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_evaluations_trade ON agent_evaluations(trade_closed_at DESC);

-- Seed default agent weights. Wrapped so a legacy agent_weights layout never
-- aborts the whole script; the learning loop will recreate/refresh weights anyway.
DO $$
BEGIN
    INSERT INTO agent_weights (agent_name, weight) VALUES
    ('technical', 0.20),
    ('classical', 0.20),
    ('smc', 0.25),
    ('price_action', 0.15),
    ('multitimeframe', 0.20)
    ON CONFLICT (agent_name) DO NOTHING;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Skipped agent_weights seed (table has a different/legacy layout): %', SQLERRM;
END $$;

-- 9) Timestamp trigger
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_signals_timestamp ON signals;
CREATE TRIGGER update_signals_timestamp BEFORE UPDATE ON signals FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_trades_timestamp ON trades;
CREATE TRIGGER update_trades_timestamp BEFORE UPDATE ON trades FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_portfolio_timestamp ON portfolio;
CREATE TRIGGER update_portfolio_timestamp BEFORE UPDATE ON portfolio FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_risk_settings_timestamp ON risk_settings;
CREATE TRIGGER update_risk_settings_timestamp BEFORE UPDATE ON risk_settings FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- 10) Views
DROP VIEW IF EXISTS active_trades_view;
CREATE VIEW active_trades_view AS
SELECT * FROM trades WHERE status IN ('OPEN', 'PARTIAL', 'TP1_HIT');

DROP VIEW IF EXISTS daily_pnl_summary;
CREATE VIEW daily_pnl_summary AS
SELECT
    DATE(COALESCE(entry_time, created_at)) AS trade_date,
    COUNT(*) AS total_trades,
    COUNT(*) FILTER (WHERE COALESCE(final_pnl, current_pnl, 0) > 0) AS winning_trades,
    COUNT(*) FILTER (WHERE COALESCE(final_pnl, current_pnl, 0) < 0) AS losing_trades,
    SUM(COALESCE(final_pnl, current_pnl, 0)) AS total_pnl
FROM trades
WHERE status NOT IN ('OPEN', 'PARTIAL', 'TP1_HIT', 'PENDING')
GROUP BY DATE(COALESCE(entry_time, created_at))
ORDER BY trade_date DESC;

-- 11) RLS: enabled and locked by default. Service Role bypasses RLS.
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE news_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE session_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_weights ENABLE ROW LEVEL SECURITY;
ALTER TABLE learning_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_trade_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_memory_rules ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS weekly_reports (
    id BIGSERIAL PRIMARY KEY,
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    stats_json JSONB NOT NULL,
    report_text TEXT NOT NULL,
    recommendations JSONB,
    tokens_used INTEGER DEFAULT 0,
    cost NUMERIC(10, 6) DEFAULT 0.0,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- If you intentionally use anon key for a private bot, create restricted policies manually.
-- Recommended GitHub Secret: SUPABASE_KEY = service_role key, not anon key.

-- =====================================================
-- Reload PostgREST schema cache immediately so newly added columns
-- (e.g. confidence) are recognised without waiting — clears PGRST204.
-- =====================================================
NOTIFY pgrst, 'reload schema';

-- =====================================================
-- ✅ Schema v2 ready.
-- =====================================================
