-- ============================================================================
-- Add the missing `trades` columns so the protective-stop guards actually persist.
--
-- ROOT CAUSE (2026-08-05, trade b8ae314a repeated phantom trailing exits):
--   The manager writes `trailing_stop_source_time` (the stamp that lets a
--   trailed/breakeven stop be executed only by bars printed after it was set).
--   Supabase rejected the PATCH with 400 (unknown column) and the service then
--   dropped the column and retried -- so the stamp was NEVER persisted. Every
--   cycle the row came back without the stamp, the guard fell back to the old
--   multi-candle window, and phantom trailing exits continued.
--
--   The log line that proves it:
--     "update succeeded after dropping unknown column(s): exit_warning,
--      trailing_distance_points, trailing_step_points, trailing_stop_source_time"
--
-- Run this ONCE on the live Supabase database (SQL editor). Idempotent.
-- ============================================================================

ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_warning              BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS trailing_distance_points  DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS trailing_step_points      DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS trailing_stop_source_time TIMESTAMPTZ;

-- ============================================================================
-- VERIFY
-- ============================================================================
SELECT column_name, data_type
  FROM information_schema.columns
 WHERE table_name = 'trades'
   AND column_name IN (
       'exit_warning',
       'trailing_distance_points',
       'trailing_step_points',
       'trailing_stop_source_time'
   )
 ORDER BY column_name;
-- Expected: 4 rows. After this, the next analyze cycle's PATCH must return
-- 200 WITHOUT the "dropping unknown column(s)" warning, and the protective-stop
-- guards (per-cycle stamp + sequential replay) become effective in production.
