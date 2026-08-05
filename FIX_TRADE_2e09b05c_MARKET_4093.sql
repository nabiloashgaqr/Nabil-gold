-- ============================================================================
-- Correct TRADE_20260805_015055_001242_2e09b05c to the operator's ruling
-- (2026-08-05): price was within 80 pts of the entry at creation, so the
-- trade is treated as a MARKET fill at 4093.00 at the creation moment, and
-- the exit is TP2 4150.00 at 08:40 Asia/Jerusalem (05:40 UTC).
--
-- Operator facts (all local, Asia/Jerusalem = UTC+3):
--   entry   04:50:55 local = 01:50:55 UTC  (the creation instant)  @ 4093.00
--   exit    08:40:00 local = 05:40:00 UTC  @ 4150.00  (TP2_HIT)
--   chart   (TradingView 15m, local): after 04:50 price never printed below
--           ~4093 (05:15 candle low 4095.300), so the old LIMIT 4090.25 was
--           never legitimately touched; 4150 was traded through by the
--           08:30-08:45 candle. Screenshot high 4167.330 (forming candle at
--           08:53) bounds the MFE approximation.
--
-- GEOMETRY (recomputed from the stored row by the VERIFY block):
--   risk  = 4093.00 - 4078.00 = 15.00 = 150.0 pts (planned risk preserved)
--   TP1   = 4105.94          = 129.4 pts = 0.86R (>= 0.8 floor)
--   TP2   = 4150.00          = 570.0 pts = 3.80R
--   MAE   = 0 (observed: no print below entry after 04:50)
--   MFE   = 4167.33 - 4093.00 = 743.3 pts (approximate, chart-bounded)
-- ============================================================================

BEGIN;

UPDATE trades
   SET order_kind            = 'MARKET',
       order_type            = 'BUY_MARKET',
       entry_price           = 4093.00,
       entry_time            = '2026-08-05T01:50:55+00:00',   -- unchanged; stated for clarity
       stop_loss             = 4078.00,   -- planned 150 pts carried to the market fill
       initial_stop_loss     = 4078.00,
       tp1                   = 4105.94,
       tp2                   = 4150.00,
       status                = 'TP2_HIT',
       result                = 'WIN',
       close_price           = 4150.00,
       close_time            = '2026-08-05T05:40:00+00:00',   -- 08:40 local
       closed_at             = '2026-08-05T05:40:00+00:00',
       current_price         = 4150.00,
       current_pnl           = 0,
       current_pnl_points    = 0,
       final_pnl             = 570.0,
       final_pnl_points      = 570.0,
       max_adverse_excursion = 0,          -- observed: nothing below entry after 04:50
       max_favorable_excursion = 743.3,    -- approximate (chart high 4167.33)
       sl_moved_to_entry     = true,
       partial_close         = true,
       pending_cycles        = 0,
       planned_risk_points   = 150.0,
       planned_tp2_points    = 570.0,
       planned_rr            = 3.8,
       last_updated          = '2026-08-05T06:00:00+00:00',
       updated_at            = '2026-08-05T06:00:00+00:00',
       activation_reason     = 'Corrected 2026-08-05 by operator ruling: within 80 pts at creation -> MARKET fill at 4093.00 at the creation instant (04:50 local); original LIMIT 4090.25 never legitimately touched (chart low after 04:50 stayed above 4093). Exit TP2 4150.00 at 08:40 local (05:40 UTC).',
       reasons               = reasons || '["Manual correction 2026-08-05: entry re-based to MARKET 4093.00 at creation time, stop carried to 4078.00 (150 pts), exit TP2 4150.00 at 08:40 local. See activation_reason."]'::jsonb,
       signal_snapshot       = signal_snapshot || '{
         "manual_correction_20260805": {
           "ruling": "market fill at creation (distance < 80 pts)",
           "entry_local": "2026-08-05T04:50:55+03:00",
           "exit_local": "2026-08-05T08:40:00+03:00",
           "entry_price": 4093.00,
           "exit_price": 4150.00,
           "mae_observed": true,
           "mfe_observed_approx": true,
           "mfe_source": "TradingView 15m screenshot high 4167.330 at 08:53 local"
         }
       }'::jsonb
 WHERE id = 'TRADE_20260805_015055_001242_2e09b05c';

COMMIT;

-- ============================================================================
-- VERIFY — every number recomputed from the stored row, not restated
-- ============================================================================
SELECT
    id,
    status,
    result,
    order_type,
    entry_price,
    stop_loss,
    tp1,
    tp2,
    close_price,
    entry_time,
    close_time,
    ROUND(((entry_price - stop_loss) / 0.1)::numeric, 1)   AS risk_points,
    ROUND(((tp1 - entry_price) / 0.1)::numeric, 1)         AS tp1_points,
    ROUND(((tp2 - entry_price) / 0.1)::numeric, 1)         AS tp2_points,
    ROUND(((tp2 - entry_price) / (entry_price - stop_loss))::numeric, 2) AS tp2_rr,
    ROUND(((close_price - entry_price) / 0.1)::numeric, 1) AS realised_points,
    final_pnl_points,
    max_adverse_excursion,
    max_favorable_excursion,
    EXTRACT(EPOCH FROM (close_time - entry_time)) / 60     AS held_minutes,
    opened_at
FROM trades
WHERE id = 'TRADE_20260805_015055_001242_2e09b05c';

-- Expected:
--   status TP2_HIT · result WIN · order_type BUY_MARKET
--   entry 4093.00 · stop 4078.00 · tp1 4105.94 · tp2 4150.00 · close 4150.00
--   risk_points 150.0 · tp1_points 129.4 · tp2_points 570.0 · tp2_rr 3.80
--   realised_points 570.0 · final_pnl_points 570.0
--   entry_time 01:50:55+00 · close_time 05:40:00+00 · held_minutes ~229.1
--   opened_at = entry_time (generated column)

-- ============================================================================
-- UNDO (restore the system-written values, only if ever needed)
-- ============================================================================
-- UPDATE trades
--    SET order_kind = 'LIMIT', order_type = 'BUY_LIMIT',
--        entry_price = 4090.25, stop_loss = 4075.25, initial_stop_loss = 4075.25,
--        tp2 = 4150.00, status = 'SL_HIT', result = 'WIN',
--        close_price = 4104.16, close_time = NULL, closed_at = NULL,
--        final_pnl = 139.1, final_pnl_points = 139.1
--  WHERE id = 'TRADE_20260805_015055_001242_2e09b05c';
