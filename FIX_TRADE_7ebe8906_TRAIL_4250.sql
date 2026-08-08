-- ============================================================================
-- Correct TRADE_20260805_140300_231604_7ebe8906 to exit at the LOCKED trailing
-- stop 4250.14 (+443.2 pts), not the buggy 4238.80 (+329.8).
--
-- WHAT HAPPENED: the trailing stop had been ratcheted to 4250.14 (locking +443)
-- when price peaked ~4267.14. A defect in the missed-candle replay then rebuilt
-- the trail from the ENTRY and overwrote the stop DOWN to 4238.80; the subsequent
-- drop filled at 4238.80, giving back ~113 pts of locked profit. The code defect
-- is fixed (replay now ratchets UP from the persisted stop only); this script
-- repairs the one record it damaged.
--
-- Operator ruling: settle the remaining leg at the higher locked stop 4250.14.
-- ============================================================================

BEGIN;

UPDATE trades
   SET stop_loss        = 4250.14,
       close_price      = 4250.14,
       current_price    = 4250.14,
       final_pnl        = 443.2,
       final_pnl_points = 443.2,
       current_pnl      = 0,
       current_pnl_points = 0,
       status           = 'SL_HIT',
       result           = 'WIN',
       reasons          = reasons || '["Manual correction 2026-08-05: exit re-settled at the locked trailing stop 4250.14 (+443.2). The buggy missed-candle replay had lowered the stop to 4238.80; defect fixed in code (trail ratchets up only)."]'::jsonb,
       signal_snapshot  = signal_snapshot || '{
         "manual_correction_20260805c": {
           "ruling": "settle at locked trailing stop",
           "wrong_exit": 4238.80,
           "correct_exit": 4250.14,
           "correct_pnl_points": 443.2
         }
       }'::jsonb
 WHERE id = 'TRADE_20260805_140300_231604_7ebe8906';

COMMIT;

-- ============================================================================
-- VERIFY
-- ============================================================================
SELECT id, status, result, entry_price, stop_loss, close_price,
       final_pnl_points,
       ROUND(((close_price - entry_price)/0.1)::numeric,1) AS recomputed_pts
  FROM trades
 WHERE id = 'TRADE_20260805_140300_231604_7ebe8906';
-- Expected: entry 4205.82 · stop/close 4250.14 · final_pnl_points 443.2 ·
--           recomputed_pts 443.2 · status SL_HIT · result WIN

-- ============================================================================
-- UNDO (restore the buggy values, only if ever needed)
-- ============================================================================
-- UPDATE trades
--    SET stop_loss = 4238.80, close_price = 4238.80, current_price = 4234.43,
--        final_pnl = 329.8, final_pnl_points = 329.8
--  WHERE id = 'TRADE_20260805_140300_231604_7ebe8906';
