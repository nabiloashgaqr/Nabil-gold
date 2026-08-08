-- ============================================================================
-- Backfill closed_at / close_time for closed trades that lack them.
--
-- WHY: a closed trade (status in the outcome set) with closed_at NULL is pushed
-- to the END by the dashboard's `closed_at DESC NULLS LAST` ordering and drops
-- out of "Latest Closed Trades (latest 50)". Today's manually-corrected trade
-- (7ebe8906) was set to SL_HIT without a closed_at, so it vanished from the list.
--
-- This sets the close time to the best available proof of when it closed
-- (close_time -> last_updated -> updated_at -> now). Idempotent.
-- Run once on the live Supabase database.
-- ============================================================================

UPDATE trades
   SET closed_at  = COALESCE(closed_at, close_time, last_updated, updated_at, now()),
       close_time = COALESCE(close_time, closed_at, last_updated, updated_at, now())
 WHERE status IN ('TP2_HIT','SL_HIT','BE_HIT','EXPIRED','THESIS_EXIT','MANUAL_CLOSE','CLOSED')
   AND (closed_at IS NULL OR close_time IS NULL);

-- ============================================================================
-- VERIFY: no closed trade should still lack a close time.
-- ============================================================================
SELECT id, status, entry_time, closed_at, close_time
  FROM trades
 WHERE status IN ('TP2_HIT','SL_HIT','BE_HIT','EXPIRED','THESIS_EXIT','MANUAL_CLOSE','CLOSED')
   AND (closed_at IS NULL OR close_time IS NULL);
-- Expected: 0 rows.
