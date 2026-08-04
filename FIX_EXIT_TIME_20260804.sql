-- ============================================================================
-- Fix the exit time on the EXISTING row, same trade, same id
--   TRADE_20260804_161500_221954_4e7c03d7
--
-- The row in the DB carries close_time 16:25 UTC. The operator's exit was
-- 16:25 Asia/Jerusalem (UTC+3) = 13:25 UTC. Only the exit timestamps move;
-- nothing else in the row is touched.
--
-- Guarded: the UPDATE fires only while the wrong value is still there, so a
-- second run is a no-op.
-- ============================================================================

BEGIN;

UPDATE trades
   SET close_time   = '2026-08-04T13:25:00+00:00',   -- the exit itself
       closed_at    = '2026-08-04T13:25:00+00:00',   -- same instant, second column
       last_updated = '2026-08-04T13:25:00+00:00',   -- mirrors close_time, as the
       updated_at   = '2026-08-04T13:25:00+00:00'    -- original INSERT wrote it
 WHERE id = 'TRADE_20260804_161500_221954_4e7c03d7'
   AND close_time = '2026-08-04T16:25:00+00:00';

COMMIT;

-- ============================================================================
-- VERIFY — recomputed from the stored row
-- ============================================================================
SELECT
    id,
    status,
    result,
    entry_time,
    close_time,
    closed_at,
    EXTRACT(EPOCH FROM (close_time - entry_time)) / 60 AS held_minutes,
    final_pnl_points
FROM trades
WHERE id = 'TRADE_20260804_161500_221954_4e7c03d7';

-- Expected:
--   status TP2_HIT · result WIN
--   entry_time 2026-08-04 06:15+00 · close_time 2026-08-04 13:25+00
--   held_minutes 430  (09:15 -> 16:25 Asia/Jerusalem, both UTC+3)
--   final_pnl_points 290.5

-- ============================================================================
-- UNDO (only if you ever need the old value back)
-- ============================================================================
-- UPDATE trades
--    SET close_time   = '2026-08-04T16:25:00+00:00',
--        closed_at    = '2026-08-04T16:25:00+00:00',
--        last_updated = '2026-08-04T16:25:00+00:00',
--        updated_at   = '2026-08-04T16:25:00+00:00'
--  WHERE id = 'TRADE_20260804_161500_221954_4e7c03d7';

-- NOTE (not executed, per your instruction to change ONLY the exit time):
-- the row's `reasons` and `signal_snapshot` texts still describe the exit as
-- 16:25 UTC / "16:15 UTC candle", as written by the earlier insert. Nothing
-- reads those fields for math (analyze_sl_floor reads max_adverse_excursion),
-- so they are cosmetic. Say the word and a follow-up syncs them too.
