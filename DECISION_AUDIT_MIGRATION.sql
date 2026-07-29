-- Decision audit trail — Phase 5
--
-- Records how every analysis cycle ended, including the refusals. Until now a
-- blocked signal existed only as a log line in a workflow run that scrolls
-- away, so questions like "how many signals did the cross-path filter stop
-- this week, and were they right to stop?" could not be answered at all.
--
-- The refusals are the interesting half: a filter that blocks everything and a
-- filter that blocks nothing both look healthy in a trades table.
--
-- Safe to re-run. The service falls back to storage/decision_audit.json when
-- Supabase is unreachable, so applying this is not required for the system to
-- keep working -- only for the trail to be queryable.

CREATE TABLE IF NOT EXISTS decision_audit (
    id              TEXT PRIMARY KEY,
    symbol          TEXT,
    stage           TEXT,           -- which gate ended the cycle
    outcome         TEXT,           -- SENT | BLOCKED
    side            TEXT,           -- BUY | SELL
    reason          TEXT,
    entry_price     NUMERIC,
    stop_loss       NUMERIC,
    tp1             NUMERIC,
    tp2             NUMERIC,
    tp1_rr          NUMERIC,        -- the metric that hid the worst fault
    tp2_rr          NUMERIC,
    confidence      NUMERIC,
    support_count   INTEGER,
    oppose_count    INTEGER,
    entry_mode      TEXT,
    trade_id        TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_decision_audit_created  ON decision_audit (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decision_audit_symbol   ON decision_audit (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decision_audit_stage    ON decision_audit (stage, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decision_audit_outcome  ON decision_audit (outcome, created_at DESC);


-- ── Queries the weekly report is built on ────────────────────────────────

-- Which gate is stopping the most signals?
--   SELECT stage, COUNT(*) AS blocked
--   FROM decision_audit
--   WHERE outcome = 'BLOCKED' AND created_at > NOW() - INTERVAL '7 days'
--   GROUP BY stage ORDER BY blocked DESC;

-- Are first targets still shipping too close to entry?
--   SELECT ROUND(AVG(tp1_rr), 2) AS avg_tp1_rr,
--          COUNT(*) FILTER (WHERE tp1_rr < 0.8) AS weak_targets
--   FROM decision_audit
--   WHERE outcome = 'SENT' AND created_at > NOW() - INTERVAL '7 days';

-- How often do we trade against qualified dissent?
--   SELECT COUNT(*) FILTER (WHERE oppose_count > 0) * 100.0 / NULLIF(COUNT(*), 0)
--   FROM decision_audit
--   WHERE outcome = 'SENT' AND created_at > NOW() - INTERVAL '7 days';

-- Plan-to-order rate: did confirmed maps actually produce orders?
--   SELECT COUNT(*) FILTER (WHERE outcome = 'SENT') * 100.0 / NULLIF(COUNT(*), 0)
--   FROM decision_audit
--   WHERE created_at > NOW() - INTERVAL '7 days';
