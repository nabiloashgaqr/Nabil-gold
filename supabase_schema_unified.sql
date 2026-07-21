-- ============================================================
-- Gold AI Signals - UNIFIED Supabase Schema (Single Source of Truth)
-- ============================================================
-- Version: 2026-06-25 (consolidated)
--
-- This is the ONE AND ONLY SQL file for the project.
-- All other .sql files have been consolidated here.
--
-- HOW TO USE:
--   1. For fresh install or full reset:
--      - Run the entire file (it includes optional RESET section)
--   2. For safe incremental update (recommended in production):
--      - Run only from "=== SCHEMA SECTION ===" onwards
--
-- Recommended: Use SUPABASE_KEY (service_role) in GitHub Secrets.
-- ============================================================

-- ============================================================
-- OPTIONAL RESET SECTION (Run this first only if you need a clean slate)
-- WARNING: This will DROP all data in the listed tables!
-- ============================================================

-- Uncomment the block below if you want a full reset:
--
-- DROP VIEW  IF EXISTS active_trades_view CASCADE;
-- DROP VIEW  IF EXISTS daily_pnl_summary  CASCADE;
--
-- DROP TABLE IF EXISTS agent_evaluations CASCADE;
-- DROP TABLE IF EXISTS learning_history  CASCADE;
-- DROP TABLE IF EXISTS agent_weights     CASCADE;
-- DROP TABLE IF EXISTS session_log       CASCADE;
-- DROP TABLE IF EXISTS risk_settings     CASCADE;
-- DROP TABLE IF EXISTS news_log          CASCADE;
-- DROP TABLE IF EXISTS daily_reports     CASCADE;
-- DROP TABLE IF EXISTS portfolio         CASCADE;
-- DROP TABLE IF EXISTS weekly_reports    CASCADE;
-- DROP TABLE IF EXISTS trades            CASCADE;
-- DROP TABLE IF EXISTS signals           CASCADE;

-- ============================================================
-- SCHEMA SECTION (Always run this)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =====================================================
-- 1) Signals table (optional audit trail)
-- =====================================================
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    signal_type VARCHAR(10) NOT NULL CHECK (signal_type IN ('BUY', 'SELL', 'WAIT')),
    symbol VARCHAR(20) NOT NULL DEFAULT 'XAU/USD',
    entry_price DECIMAL(18, 6),
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

-- Self-heal for existing signals tables
ALTER TABLE signals ADD COLUMN IF NOT EXISTS signal_type      VARCHAR(10);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS symbol           VARCHAR(20) DEFAULT 'XAU/USD';
ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_price      DECIMAL(18, 6);
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

-- =====================================================
-- 2) Trades table (core table - matches Python DatabaseService)
-- =====================================================
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    signal_id TEXT REFERENCES signals(id) ON DELETE SET NULL,
    type VARCHAR(10) CHECK (type IN ('BUY', 'SELL')),
    trade_type VARCHAR(10) GENERATED ALWAYS AS (type) STORED,
    symbol VARCHAR(20) NOT NULL DEFAULT 'XAU/USD',

    entry_price DECIMAL(18, 6) NOT NULL,
    entry_time TIMESTAMPTZ,
    stop_loss DECIMAL(18, 6),
    initial_stop_loss DECIMAL(18, 6),
    tp1 DECIMAL(18, 6),
    tp2 DECIMAL(18, 6),

    confidence INTEGER DEFAULT 0,
    trading_mode VARCHAR(20) DEFAULT 'paper' CHECK (trading_mode IN ('paper', 'live', 'demo', 'manual')),
    paper_trading BOOLEAN DEFAULT TRUE,
    paper_balance_start DECIMAL(18, 6),
    paper_lot_size DECIMAL(18, 6),
    status VARCHAR(30) DEFAULT 'OPEN' CHECK (status IN (
        'OPEN', 'PARTIAL', 'PENDING', 'TP1_HIT', 'TP2_HIT', 'SL_HIT', 'BE_HIT',
        'MANUAL_CLOSE', 'EXPIRED', 'CLOSED', 'CANCELLED'
    )),

    current_price DECIMAL(18, 6),
    current_pnl DECIMAL(18, 6) DEFAULT 0,
    current_pnl_points DECIMAL(18, 6) DEFAULT 0,
    final_pnl DECIMAL(18, 6),
    final_pnl_points DECIMAL(18, 6),

    -- Phase 5 trade-enrichment metadata for learning/report quality
    planned_risk_points DECIMAL(18, 6),
    planned_tp2_points DECIMAL(18, 6),
    planned_rr DECIMAL(10, 4),
    session_label TEXT,
    session_quality TEXT,
    entry_day_of_week TEXT,
    entry_hour_local INTEGER,
    news_status_at_entry TEXT,
    news_risk_at_entry TEXT,
    volatility_regime TEXT,
    trend_strength TEXT,
    daily_bias_at_entry TEXT,
    primary_entry_driver TEXT,
    entry_failure_mode TEXT,
    macro_bias_at_entry TEXT,
    setup_id TEXT,
    setup_type TEXT,
    setup_state TEXT,
    lead_agent TEXT,
    setup_quality TEXT,
    poi_type TEXT,
    sweep_side TEXT,
    displacement_score DECIMAL(10, 4),

    sl_moved_to_entry BOOLEAN DEFAULT FALSE,
    partial_close BOOLEAN DEFAULT FALSE,
    updates_sent JSONB DEFAULT '[]'::jsonb,
    exit_warning BOOLEAN DEFAULT FALSE,
    management_phase VARCHAR(40),
    recent_30m_high DECIMAL(18, 6),
    recent_30m_low DECIMAL(18, 6),
    max_favorable_excursion DECIMAL(18, 6) DEFAULT 0,
    max_adverse_excursion DECIMAL(18, 6) DEFAULT 0,
    result VARCHAR(30),
    reasons JSONB DEFAULT '[]'::jsonb,
    signal_snapshot JSONB DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    opened_at TIMESTAMPTZ GENERATED ALWAYS AS (entry_time) STORED,
    closed_at TIMESTAMPTZ,
    close_time TIMESTAMPTZ,
    close_price DECIMAL(18, 6),
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Self-heal for existing trades tables (very important for production)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_id           TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS type                VARCHAR(10);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS side                VARCHAR(10);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS symbol              VARCHAR(20) DEFAULT 'XAU/USD';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_price         DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_time          TIMESTAMPTZ;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_loss           DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS initial_stop_loss   DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS tp1                 DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS tp2                 DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS confidence          INTEGER DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS trading_mode        VARCHAR(20) DEFAULT 'paper';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS paper_trading       BOOLEAN DEFAULT TRUE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS paper_balance_start DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS paper_lot_size      DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS status              VARCHAR(30) DEFAULT 'OPEN';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS current_price       DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS current_pnl         DECIMAL(18, 6) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS current_pnl_points  DECIMAL(18, 6) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS final_pnl           DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS final_pnl_points    DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS planned_risk_points DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS planned_tp2_points  DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS planned_rr          DECIMAL(10, 4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS session_label       TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS session_quality     TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_day_of_week   TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_hour_local    INTEGER;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS news_status_at_entry TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS news_risk_at_entry  TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS volatility_regime   TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS trend_strength      TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS daily_bias_at_entry TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS primary_entry_driver TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_failure_mode TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS macro_bias_at_entry TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS setup_id            TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS setup_type          TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS setup_state         TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS lead_agent          TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS setup_quality       TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS poi_type            TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sweep_side          TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS displacement_score  DECIMAL(10, 4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sl_moved_to_entry   BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS partial_close       BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS updates_sent        JSONB DEFAULT '[]'::jsonb;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_warning        BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS management_phase    VARCHAR(40);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS recent_30m_high     DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS recent_30m_low      DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS max_favorable_excursion DECIMAL(18, 6) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS max_adverse_excursion   DECIMAL(18, 6) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS result              VARCHAR(30);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS reasons             JSONB DEFAULT '[]'::jsonb;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_snapshot     JSONB DEFAULT '{}'::jsonb;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS created_at          TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE trades ADD COLUMN IF NOT EXISTS closed_at           TIMESTAMPTZ;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS close_time          TIMESTAMPTZ;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS close_price         DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS last_updated        TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE trades ADD COLUMN IF NOT EXISTS updated_at          TIMESTAMPTZ DEFAULT NOW();

-- Indexes (after self-heal)
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_closed ON trades(closed_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_enrichment_dow ON trades(entry_day_of_week);
CREATE INDEX IF NOT EXISTS idx_trades_enrichment_session ON trades(session_label);
CREATE INDEX IF NOT EXISTS idx_trades_entry_driver ON trades(primary_entry_driver);
CREATE INDEX IF NOT EXISTS idx_trades_macro_bias ON trades(macro_bias_at_entry);
CREATE INDEX IF NOT EXISTS idx_trades_setup_type ON trades(setup_type);
CREATE INDEX IF NOT EXISTS idx_trades_lead_agent ON trades(lead_agent);
CREATE INDEX IF NOT EXISTS idx_trades_open ON trades(status) WHERE status IN ('OPEN', 'PARTIAL', 'PENDING', 'TP1_HIT');

-- =====================================================
-- 3) Portfolio
-- =====================================================
CREATE TABLE IF NOT EXISTS portfolio (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    balance DECIMAL(18, 6) DEFAULT 10000.00,
    equity DECIMAL(18, 6) DEFAULT 10000.00,
    available_margin DECIMAL(18, 6) DEFAULT 10000.00,
    used_margin DECIMAL(18, 6) DEFAULT 0,
    open_positions INTEGER DEFAULT 0,
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    win_rate DECIMAL(5, 2) DEFAULT 0,
    total_pnl DECIMAL(18, 6) DEFAULT 0,
    max_drawdown DECIMAL(18, 6) DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =====================================================
-- 4) Daily Reports
-- =====================================================
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
    daily_pnl DECIMAL(18, 6) DEFAULT 0,
    win_rate DECIMAL(5, 2) DEFAULT 0,
    market_summary TEXT,
    technical_summary TEXT,
    recommendations TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Dashboard archive fields for persisted daily report text/stats
ALTER TABLE daily_reports ADD COLUMN IF NOT EXISTS report_text TEXT;
ALTER TABLE daily_reports ADD COLUMN IF NOT EXISTS stats_json JSONB DEFAULT '{}'::jsonb;
ALTER TABLE daily_reports ADD COLUMN IF NOT EXISTS recommendations_json JSONB DEFAULT '[]'::jsonb;
ALTER TABLE daily_reports ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'ok';

CREATE INDEX IF NOT EXISTS idx_daily_reports_date ON daily_reports(report_date DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_reports_report_date ON daily_reports(report_date);

-- =====================================================
-- 5) News Log
-- =====================================================
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

CREATE INDEX IF NOT EXISTS idx_news_impact ON news_log(impact);
CREATE INDEX IF NOT EXISTS idx_news_logged ON news_log(logged_at DESC);

-- =====================================================
-- 6) Risk Settings
-- =====================================================
CREATE TABLE IF NOT EXISTS risk_settings (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    setting_key VARCHAR(50) UNIQUE NOT NULL,
    setting_value JSONB NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Self-heal + seed
ALTER TABLE risk_settings ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE risk_settings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'risk_settings_setting_key_key'
    ) THEN
        ALTER TABLE risk_settings ADD CONSTRAINT risk_settings_setting_key_key UNIQUE (setting_key);
    END IF;
END $$;

DO $$
BEGIN
    INSERT INTO risk_settings (setting_key, setting_value, description) VALUES
    ('max_risk_per_trade', '{"value": 2, "unit": "percent"}', 'الحد الأقصى للمخاطرة لكل صفقة'),
    ('daily_risk_limit', '{"value": 6, "unit": "percent"}', 'الحد الأقصى للمخاطرة اليومية'),
    ('max_open_positions', '{"value": 3, "unit": "count"}', 'الحد الأقصى للصفقات المفتوحة'),
    ('min_confidence_threshold', '{"value": 60, "unit": "percent"}', 'الحد الأدنى للثقة'),
    ('max_drawdown_stop', '{"value": 10, "unit": "percent"}', 'وقف السحب الأقصى')
    ON CONFLICT (setting_key) DO NOTHING;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Skipped risk_settings seed: %', SQLERRM;
END $$;

-- =====================================================
-- 7) Session Log
-- =====================================================
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

-- =====================================================
-- 8) Learning & Agent Performance
-- =====================================================
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

-- Self-heal learning tables
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS agent_name        VARCHAR(50);
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS weight            DECIMAL(7, 6) DEFAULT 0.15;
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS win_rate          DECIMAL(5, 2) DEFAULT 0;
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS total_predictions INTEGER DEFAULT 0;
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS trend             VARCHAR(20) DEFAULT 'STABLE';
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS learning_score    DECIMAL(7, 6) DEFAULT 0.5;
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE agent_weights ADD COLUMN IF NOT EXISTS created_at        TIMESTAMPTZ DEFAULT NOW();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_weights_agent_name_key') THEN
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

-- Seed default weights
DO $$
BEGIN
    INSERT INTO agent_weights (agent_name, weight) VALUES
    ('technical', 0.20), ('classical', 0.20), ('smc', 0.25),
    ('price_action', 0.15), ('multitimeframe', 0.20)
    ON CONFLICT (agent_name) DO NOTHING;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Skipped agent_weights seed: %', SQLERRM;
END $$;

-- =====================================================
-- 9) Timestamp triggers
-- =====================================================
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

-- =====================================================
-- 10) Views
-- =====================================================
DROP VIEW IF EXISTS active_trades_view;
CREATE VIEW active_trades_view AS
SELECT * FROM trades WHERE status IN ('OPEN', 'PARTIAL', 'PENDING', 'TP1_HIT');

DROP VIEW IF EXISTS daily_pnl_summary;
CREATE VIEW daily_pnl_summary AS
SELECT
    DATE(COALESCE(entry_time, created_at)) AS trade_date,
    COUNT(*) AS total_trades,
    COUNT(*) FILTER (WHERE COALESCE(final_pnl, current_pnl, 0) > 0) AS winning_trades,
    COUNT(*) FILTER (WHERE COALESCE(final_pnl, current_pnl, 0) < 0) AS losing_trades,
    SUM(COALESCE(final_pnl, current_pnl, 0)) AS total_pnl
FROM trades
WHERE status NOT IN ('OPEN', 'PARTIAL', 'TP1_HIT')
GROUP BY DATE(COALESCE(entry_time, created_at))
ORDER BY trade_date DESC;

-- =====================================================
-- 11) Weekly Reports
-- =====================================================
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

CREATE INDEX IF NOT EXISTS idx_weekly_reports_week_start ON weekly_reports (week_start DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_weekly_reports_period ON weekly_reports (week_start, week_end);

-- =====================================================
-- 12) Partial Alert Tracker (July 2026 — BUG-4 fix)
-- =====================================================
-- Tracks last partial-consensus alert per symbol+direction.
-- Required because GitHub Actions is stateless: local JSON
-- files don't persist between runs, so the price-diff gate
-- was always treating every cycle as "first alert".
-- =====================================================

CREATE TABLE IF NOT EXISTS partial_alert_tracker (
    key TEXT PRIMARY KEY,                    -- e.g. "XAU/USD_BUY" or "XAU/USD_SELL"
    price DECIMAL(18, 6),                    -- price at which last alert was sent
    timestamp TIMESTAMPTZ,                   -- when the alert was sent (UTC)
    session TEXT,                            -- session name at alert time (e.g. "London")
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_partial_alert_key ON partial_alert_tracker(key);

ALTER TABLE partial_alert_tracker ADD COLUMN IF NOT EXISTS price      DECIMAL(18, 6);
ALTER TABLE partial_alert_tracker ADD COLUMN IF NOT EXISTS timestamp  TIMESTAMPTZ;
ALTER TABLE partial_alert_tracker ADD COLUMN IF NOT EXISTS session    TEXT;
ALTER TABLE partial_alert_tracker ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

DROP TRIGGER IF EXISTS update_partial_alert_timestamp ON partial_alert_tracker;
CREATE TRIGGER update_partial_alert_timestamp
    BEFORE UPDATE ON partial_alert_tracker
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =====================================================
-- 13) Post-News Tracker (July 2026)
-- =====================================================
-- Prevents duplicate post-news analysis alerts.
-- Each event fires only once per release.
-- =====================================================

CREATE TABLE IF NOT EXISTS post_news_tracker (
    event_key TEXT PRIMARY KEY,              -- unique key: "event_name_time"
    sent_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE post_news_tracker ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE post_news_tracker ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ DEFAULT NOW();

-- =====================================================
-- 14) Macro Context (hourly snapshot)
-- =====================================================
-- Stores the latest hourly macro context (DXY, risk sentiment, etc.)
-- Updated by macro_context.yml workflow every hour.
-- =====================================================

CREATE TABLE IF NOT EXISTS macro_context (
    id TEXT PRIMARY KEY DEFAULT 'latest',
    context JSONB DEFAULT '{}'::jsonb,
    source TEXT,
    generated_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE macro_context ADD COLUMN IF NOT EXISTS context      JSONB DEFAULT '{}'::jsonb;
ALTER TABLE macro_context ADD COLUMN IF NOT EXISTS source       TEXT;
ALTER TABLE macro_context ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ;
ALTER TABLE macro_context ADD COLUMN IF NOT EXISTS updated_at   TIMESTAMPTZ DEFAULT NOW();

DROP TRIGGER IF EXISTS update_macro_context_timestamp ON macro_context;
CREATE TRIGGER update_macro_context_timestamp
    BEFORE UPDATE ON macro_context
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =====================================================
-- 14.5) Session Plans / Day-Map snapshots
-- =====================================================
-- Stores the planner output even when no trade is opened, so production can
-- audit whether the system actually saw the day map like a manual analyst.
-- =====================================================
CREATE TABLE IF NOT EXISTS session_plans (
    id TEXT PRIMARY KEY,
    plan_id TEXT,
    scenario_id TEXT,
    symbol TEXT DEFAULT 'XAU/USD',
    session_label TEXT,
    session_quality TEXT,
    session_bias TEXT,
    scenario_type TEXT,
    planner_source TEXT,
    authority_state TEXT,
    authority_direction TEXT,
    plan_ready BOOLEAN DEFAULT FALSE,
    plan_status TEXT,
    plan_reason TEXT,
    planner_confidence DECIMAL(10, 4),
    planner_grade TEXT,
    poi_classification TEXT,
    extreme_poi BOOLEAN DEFAULT FALSE,
    primary_entry_price DECIMAL(18, 6),
    standby_entry_price DECIMAL(18, 6),
    invalidation_level DECIMAL(18, 6),
    target_liquidity DECIMAL(18, 6),
    market_zone_context TEXT,
    structure_trend TEXT,
    structure_quality TEXT,
    execution_preference TEXT,
    expected_path TEXT,
    current_price DECIMAL(18, 6),
    market_data_source TEXT,
    analysis_run_at TIMESTAMPTZ DEFAULT NOW(),
    plan_created_at TIMESTAMPTZ DEFAULT NOW(),
    plan_expires_at TIMESTAMPTZ,
    payload JSONB DEFAULT '{}'::jsonb,
    telegram_sent_at TIMESTAMPTZ,
    telegram_delivery_note TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS plan_id TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS scenario_id TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS symbol TEXT DEFAULT 'XAU/USD';
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS session_label TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS session_quality TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS session_bias TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS scenario_type TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS planner_source TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS authority_state TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS authority_direction TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS plan_ready BOOLEAN DEFAULT FALSE;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS plan_status TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS plan_reason TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS planner_confidence DECIMAL(10, 4);
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS planner_grade TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS poi_classification TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS extreme_poi BOOLEAN DEFAULT FALSE;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS primary_entry_price DECIMAL(18, 6);
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS standby_entry_price DECIMAL(18, 6);
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS invalidation_level DECIMAL(18, 6);
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS target_liquidity DECIMAL(18, 6);
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS market_zone_context TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS structure_trend TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS structure_quality TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS execution_preference TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS expected_path TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS current_price DECIMAL(18, 6);
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS market_data_source TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS analysis_run_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS plan_created_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS plan_expires_at TIMESTAMPTZ;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS payload JSONB DEFAULT '{}'::jsonb;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS telegram_sent_at TIMESTAMPTZ;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS telegram_delivery_note TEXT;
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE session_plans ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_session_plans_symbol ON session_plans(symbol);
CREATE INDEX IF NOT EXISTS idx_session_plans_ready ON session_plans(plan_ready);
CREATE INDEX IF NOT EXISTS idx_session_plans_run_at ON session_plans(analysis_run_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_plans_plan_id ON session_plans(plan_id);
CREATE INDEX IF NOT EXISTS idx_session_plans_telegram_sent_at ON session_plans(telegram_sent_at DESC);

DROP TRIGGER IF EXISTS update_session_plans_timestamp ON session_plans;
CREATE TRIGGER update_session_plans_timestamp
    BEFORE UPDATE ON session_plans
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =====================================================
-- 15) Setup Candidates (Sprint 1 foundation)
-- =====================================================
-- Stores structured pre-trade setup context (setup type, POI, sweep side,
-- state label, quality). This is the persistence layer for the future setup
-- state machine and analyst-vs-bot comparison.
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
CREATE INDEX IF NOT EXISTS idx_setup_candidates_symbol ON setup_candidates(symbol);
CREATE INDEX IF NOT EXISTS idx_setup_candidates_type ON setup_candidates(setup_type);
CREATE INDEX IF NOT EXISTS idx_setup_candidates_state ON setup_candidates(setup_state);
CREATE INDEX IF NOT EXISTS idx_setup_candidates_last_seen ON setup_candidates(last_seen_at DESC);

DROP TRIGGER IF EXISTS update_setup_candidates_timestamp ON setup_candidates;
CREATE TRIGGER update_setup_candidates_timestamp
    BEFORE UPDATE ON setup_candidates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =====================================================
-- 16) Setup State Events (Sprint 2 foundation)
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

CREATE INDEX IF NOT EXISTS idx_setup_state_events_setup_id ON setup_state_events(setup_id);
CREATE INDEX IF NOT EXISTS idx_setup_state_events_state_key ON setup_state_events(state_key);
CREATE INDEX IF NOT EXISTS idx_setup_state_events_symbol ON setup_state_events(symbol);
CREATE INDEX IF NOT EXISTS idx_setup_state_events_created ON setup_state_events(created_at DESC);

DROP TRIGGER IF EXISTS update_setup_state_events_timestamp ON setup_state_events;
CREATE TRIGGER update_setup_state_events_timestamp
    BEFORE UPDATE ON setup_state_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =====================================================
-- 17) Analyst Labels + Comparisons (Phase 6 foundation)
-- =====================================================
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

-- =====================================================
-- 12) Row Level Security (RLS)
-- Service Role (SUPABASE_KEY) bypasses RLS automatically.
-- ============================================================
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
ALTER TABLE partial_alert_tracker ENABLE ROW LEVEL SECURITY;
ALTER TABLE post_news_tracker ENABLE ROW LEVEL SECURITY;
ALTER TABLE macro_context ENABLE ROW LEVEL SECURITY;
ALTER TABLE session_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE setup_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE setup_state_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE analyst_labels ENABLE ROW LEVEL SECURITY;
ALTER TABLE analyst_comparisons ENABLE ROW LEVEL SECURITY;

-- =====================================================
-- Final: Reload PostgREST schema cache
-- =====================================================
NOTIFY pgrst, 'reload schema';

-- =====================================================
-- ✅ UNIFIED SCHEMA READY (Single File)
-- All previous duplicate SQL files have been consolidated.
-- ============================================================
