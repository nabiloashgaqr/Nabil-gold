-- =====================================================
-- Gold AI Signals - Supabase Schema (آمن لإعادة التشغيل)
-- أنشئ هذا الملف في Supabase Dashboard > SQL Editor
-- =====================================================

-- 1. جدول الإشارات (Signals)
CREATE TABLE IF NOT EXISTS signals (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    signal_type VARCHAR(10) NOT NULL CHECK (signal_type IN ('BUY', 'SELL', 'WAIT')),
    symbol VARCHAR(20) NOT NULL DEFAULT 'XAUUSD',
    entry_price DECIMAL(18, 4),
    confidence_score DECIMAL(5, 2) DEFAULT 0,
    quality VARCHAR(10) CHECK (quality IN ('HIGH', 'MEDIUM', 'LOW')),
    session_name VARCHAR(100),
    session_quality VARCHAR(10),
    signal_reason TEXT,
    agent_inputs JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE,
    expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_signals_active ON signals(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_type ON signals(signal_type);

-- 2. جدول الصفقات (Trades)
CREATE TABLE IF NOT EXISTS trades (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    signal_id UUID REFERENCES signals(id) ON DELETE SET NULL,
    trade_type VARCHAR(10) NOT NULL CHECK (trade_type IN ('BUY', 'SELL')),
    symbol VARCHAR(20) NOT NULL DEFAULT 'XAUUSD',
    entry_price DECIMAL(18, 4) NOT NULL,
    stop_loss DECIMAL(18, 4),
    take_profit DECIMAL(18, 4),
    current_price DECIMAL(18, 4),
    quantity DECIMAL(18, 6) DEFAULT 0.01,
    status VARCHAR(20) DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED', 'PENDING', 'CANCELLED')),
    pnl DECIMAL(18, 4) DEFAULT 0,
    pnl_percentage DECIMAL(10, 4) DEFAULT 0,
    risk_amount DECIMAL(18, 4),
    risk_reward_ratio DECIMAL(5, 2),
    opened_at TIMESTAMPTZ DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status) WHERE status = 'OPEN';
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_opened ON trades(opened_at DESC);

-- 3. جدول المحفظة (Portfolio)
CREATE TABLE IF NOT EXISTS portfolio (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
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

-- 4. جدول التقارير اليومية (Daily Reports)
CREATE TABLE IF NOT EXISTS daily_reports (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
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

CREATE INDEX IF NOT EXISTS idx_daily_reports_date ON daily_reports(report_date DESC);

-- 5. جدول سجل الأخبار (News Log)
CREATE TABLE IF NOT EXISTS news_log (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    headline TEXT NOT NULL,
    source VARCHAR(100),
    impact VARCHAR(20) CHECK (impact IN ('HIGH', 'MEDIUM', 'LOW')),
    affected_pairs TEXT[],
    trading_impact VARCHAR(20) DEFAULT 'NEUTRAL' CHECK (trading_impact IN ('POSITIVE', 'NEGATIVE', 'NEUTRAL')),
    confidence_adjustment DECIMAL(5, 2) DEFAULT 0,
    sentiment VARCHAR(20) DEFAULT 'NEUTRAL' CHECK (sentiment IN ('BULLISH', 'BEARISH', 'NEUTRAL')),
    logged_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_news_impact ON news_log(impact);
CREATE INDEX IF NOT EXISTS idx_news_logged ON news_log(logged_at DESC);

-- 6. جدول إعدادات المخاطر (Risk Settings)
CREATE TABLE IF NOT EXISTS risk_settings (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    setting_key VARCHAR(50) UNIQUE NOT NULL,
    setting_value JSONB NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO risk_settings (setting_key, setting_value, description) VALUES
('max_risk_per_trade', '{"value": 2, "unit": "percent"}', 'الحد الأقصى للمخاطرة لكل صفقة'),
('daily_risk_limit', '{"value": 6, "unit": "percent"}', 'الحد الأقصى للمخاطرة اليومية'),
('max_open_positions', '{"value": 5, "unit": "count"}', 'الحد الأقصى للصفقات المفتوحة'),
('min_confidence_threshold', '{"value": 60, "unit": "percent"}', 'الحد الأدنى للثقة'),
('max_drawdown_stop', '{"value": 10, "unit": "percent"}', 'وقف السحب الأقصى')
ON CONFLICT (setting_key) DO NOTHING;

-- 7. جدول سجل الجلسات (Session Log)
CREATE TABLE IF NOT EXISTS session_log (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    session_name VARCHAR(100) NOT NULL,
    quality VARCHAR(10),
    trading_allowed BOOLEAN NOT NULL,
    reason VARCHAR(200),
    signals_generated INTEGER DEFAULT 0,
    trades_opened INTEGER DEFAULT 0,
    total_confidence DECIMAL(5, 2) DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ
);

-- 8. Function للتحديث التلقائي
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 9. إضافة Triggers (مع DROP أولاً لتجنب التكرار)
DROP TRIGGER IF EXISTS update_signals_timestamp ON signals;
CREATE TRIGGER update_signals_timestamp
    BEFORE UPDATE ON signals
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_trades_timestamp ON trades;
CREATE TRIGGER update_trades_timestamp
    BEFORE UPDATE ON trades
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_portfolio_timestamp ON portfolio;
CREATE TRIGGER update_portfolio_timestamp
    BEFORE UPDATE ON portfolio
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_risk_settings_timestamp ON risk_settings;
CREATE TRIGGER update_risk_settings_timestamp
    BEFORE UPDATE ON risk_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- 10. Views للتحليل
DROP VIEW IF EXISTS active_trades_view;
CREATE OR REPLACE VIEW active_trades_view AS
SELECT 
    t.*,
    s.signal_type as signal_type,
    s.confidence_score,
    s.quality as session_quality
FROM trades t
LEFT JOIN signals s ON t.signal_id = s.id
WHERE t.status = 'OPEN';

DROP VIEW IF EXISTS daily_pnl_summary;
CREATE OR REPLACE VIEW daily_pnl_summary AS
SELECT 
    DATE(opened_at) as trade_date,
    COUNT(*) as total_trades,
    COUNT(*) FILTER (WHERE pnl > 0) as winning_trades,
    COUNT(*) FILTER (WHERE pnl < 0) as losing_trades,
    SUM(pnl) as total_pnl,
    AVG(pnl_percentage) as avg_pnl_pct
FROM trades
WHERE closed_at IS NOT NULL
GROUP BY DATE(opened_at)
ORDER BY trade_date DESC;

-- 11. Row Level Security (RLS)
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_reports ENABLE ROW LEVEL SECURITY;

-- Allow public access
CREATE POLICY "Allow all for signals" ON signals FOR ALL TO anon USING (true);
CREATE POLICY "Allow all for trades" ON trades FOR ALL TO anon USING (true);
CREATE POLICY "Allow all for portfolio" ON portfolio FOR ALL TO anon USING (true);
CREATE POLICY "Allow all for daily_reports" ON daily_reports FOR ALL TO anon USING (true);

-- Grant permissions
GRANT ALL ON signals TO anon;
GRANT ALL ON trades TO anon;
GRANT ALL ON portfolio TO anon;
GRANT ALL ON daily_reports TO anon;
GRANT ALL ON news_log TO anon;
GRANT ALL ON risk_settings TO anon;
GRANT ALL ON session_log TO anon;
GRANT ALL ON agent_weights TO anon;
GRANT ALL ON learning_history TO anon;
GRANT ALL ON agent_evaluations TO anon;

-- =====================================================
-- ✅ تم بنجاح!
-- =====================================================

-- =====================================================
-- 🧠 جداول التعلم الذكي (اختياري)
-- =====================================================

-- جدول أوزان الوكلاء المتعلمة
CREATE TABLE IF NOT EXISTS agent_weights (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    agent_name VARCHAR(50) UNIQUE NOT NULL,
    weight DECIMAL(5, 4) NOT NULL DEFAULT 0.15,
    win_rate DECIMAL(5, 2) DEFAULT 0,
    total_predictions INTEGER DEFAULT 0,
    trend VARCHAR(20) DEFAULT 'STABLE' CHECK (trend IN ('IMPROVING', 'STABLE', 'DECLINING')),
    learning_score DECIMAL(5, 4) DEFAULT 0.5,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_weights_name ON agent_weights(agent_name);

-- جدول سجل التعلم
CREATE TABLE IF NOT EXISTS learning_history (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    report_date TIMESTAMPTZ NOT NULL,
    agents_performance JSONB,
    adjusted_weights JSONB,
    previous_weights JSONB,
    total_trades_analyzed INTEGER DEFAULT 0,
    overall_win_rate DECIMAL(5, 2) DEFAULT 0,
    recommendations TEXT[],
    changes_summary TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learning_history_date ON learning_history(report_date DESC);

-- جدول تقييم الوكلاء
CREATE TABLE IF NOT EXISTS agent_evaluations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    signal_id UUID REFERENCES signals(id) ON DELETE SET NULL,
    agent_name VARCHAR(50) NOT NULL,
    predicted_signal VARCHAR(10),
    actual_outcome VARCHAR(20) CHECK (actual_outcome IN ('WIN', 'LOSS', 'NEUTRAL')),
    pnl_contribution DECIMAL(10, 4) DEFAULT 0,
    confidence_at_prediction DECIMAL(5, 2) DEFAULT 50,
    learning_adjusted BOOLEAN DEFAULT FALSE,
    trade_closed_at TIMESTAMPTZ,
    evaluated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_evaluations_agent ON agent_evaluations(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_evaluations_trade ON agent_evaluations(trade_closed_at DESC);

-- Insert الأوزان الافتراضية
INSERT INTO agent_weights (agent_name, weight) VALUES
('technical', 0.20),
('classical', 0.20),
('smc', 0.25),
('price_action', 0.15),
('multitimeframe', 0.15)
ON CONFLICT (agent_name) DO NOTHING;