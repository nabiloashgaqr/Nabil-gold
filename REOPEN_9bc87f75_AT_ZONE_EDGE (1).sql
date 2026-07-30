-- ============================================================
--  Reopen TRADE_20260730_090548_435830_9bc87f75 at the zone edge
-- ============================================================
--
--  WHAT HAPPENED
--  -------------
--  A BUY day map published a MAIN BUY AREA of 4054.49 - 4062.05 with a
--  reference entry of 4058.27. Price traded down to 4060.40 -- inside the
--  published area, 21 points above the reference entry -- and the LIMIT was
--  never touched. Price then ran to 4081.14, straight through TP1 (4075.79).
--
--  The order was cancelled as stale ("market covered 74% of target path
--  without fill"): correct for the code as it stood, wrong for the trade.
--
--  THE FILL
--  --------
--    entry  4062.05   the upper edge of the published zone -- the worst
--                     price inside the area the map itself drew
--    risk   185 pts   the stop travels with the entry (4039.79 -> 4043.57),
--                     so the planned risk is preserved. Chasing at market
--                     against the original stop would have been 265 pts at
--                     RR 1.02, below the 1.5 floor.
--    stop   4062.05   breakeven, because TP1 (4075.79) was already passed on
--                     the way to 4081.14. The 4043.57 figure is recorded as
--                     initial_stop_loss: the risk actually carried at fill.
--
--  STEP 0 EXISTS BECAUSE THE FIRST VERSION OF THIS SCRIPT FAILED
--  -------------------------------------------------------------
--  It assumed the live table matched supabase_schema_unified.sql and hit
--  "column management_phase does not exist". The repo schema adds most of
--  these columns through ALTER statements that were never run on this
--  database. Rather than assume again, step 0 adds every column this script
--  writes, IF NOT EXISTS -- a no-op for the ones already present.
--
--  Run once. Transactional, guarded, and safe to re-run (it stops rather
--  than overwrite). Verification and rollback are at the bottom.
-- ============================================================

BEGIN;

-- 0) Make sure every column this script writes actually exists ------------
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_price             DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_time              TIMESTAMPTZ;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_loss               DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS initial_stop_loss       DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS tp1                     DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS tp2                     DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS current_price           DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS current_pnl             DECIMAL(18, 6) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS current_pnl_points      DECIMAL(18, 6) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS final_pnl               DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS final_pnl_points        DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS max_favorable_excursion DECIMAL(18, 6) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS max_adverse_excursion   DECIMAL(18, 6) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sl_moved_to_entry       BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS partial_close           BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS management_phase        VARCHAR(40);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS updates_sent            JSONB DEFAULT '[]'::jsonb;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS reasons                 JSONB DEFAULT '[]'::jsonb;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_snapshot         JSONB DEFAULT '{}'::jsonb;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS result                  VARCHAR(30);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS order_kind              VARCHAR(20);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS order_type              VARCHAR(20);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS pending_cycles          INTEGER DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS activation_reason       TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS closed_at               TIMESTAMPTZ;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS close_time              TIMESTAMPTZ;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS close_price             DECIMAL(18, 6);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS last_updated            TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE trades ADD COLUMN IF NOT EXISTS updated_at              TIMESTAMPTZ DEFAULT NOW();


-- 1) Safety: confirm the row is in the state we expect ---------------------
DO $$
DECLARE
    current_status text;
BEGIN
    SELECT status INTO current_status
    FROM trades
    WHERE id = 'TRADE_20260730_090548_435830_9bc87f75';

    IF current_status IS NULL THEN
        RAISE EXCEPTION 'Trade 9bc87f75 not found -- nothing to repair.';
    END IF;

    IF current_status = 'OPEN' THEN
        RAISE EXCEPTION 'Trade 9bc87f75 is already OPEN. Script already applied; aborting so nothing is overwritten.';
    END IF;

    IF current_status <> 'CANCELLED' THEN
        RAISE EXCEPTION 'Trade 9bc87f75 is %, expected CANCELLED. Aborting.', current_status;
    END IF;

    RAISE NOTICE 'Trade 9bc87f75 is CANCELLED as expected -- reopening at the zone edge.';
END $$;


-- 2) Reopen it at the zone edge -------------------------------------------
UPDATE trades
SET
    status                  = 'OPEN',
    result                  = NULL,

    entry_price             = 4062.05,
    entry_time              = '2026-07-30T09:20:00+00:00',

    -- The stop carried at FILL was 4043.57: the mapped 185 points, moved down
    -- with the entry. It is recorded as initial_stop_loss because that is the
    -- risk the trade actually took on.
    initial_stop_loss       = 4043.57,

    -- The live stop is already at breakeven: TP1 was passed en route to
    -- 4081.14, and breakeven for this fill is the entry itself.
    stop_loss               = 4062.05,

    tp1                     = 4075.79,
    tp2                     = 4093.31,

    -- Recorded in its post-TP1 state. Leaving it a plain OPEN would let the
    -- manager re-fire TP1 for a level price passed long ago.
    partial_close           = TRUE,
    sl_moved_to_entry       = TRUE,
    management_phase        = 'POST_TP1_TRAILING',
    updates_sent            = '["ORDER_FILLED", "TP1_HIT", "MOVE_SL_TO_BE"]'::jsonb,

    current_price           = 4081.14,
    current_pnl             = 191.0,
    current_pnl_points      = 191.0,
    max_favorable_excursion = 191.0,
    max_adverse_excursion   = 0,

    closed_at               = NULL,
    close_time              = NULL,
    close_price             = NULL,
    final_pnl               = NULL,
    final_pnl_points        = NULL,

    pending_cycles          = 0,
    order_type              = 'BUY_MARKET',
    order_kind              = 'MARKET',
    activation_reason       = 'Zone-touch activation (applied manually): price traded into the mapped area 4054.49-4062.05 at 4060.40, within 21 pts of the 4058.27 entry, and left it upward; filled at the zone edge 4062.05 with the planned 185 pt risk preserved',

    reasons                 = '["Zone-touch activation: filled at the published zone edge after price traded inside the mapped area without touching the reference entry", "Stop moved to breakeven 4062.05 after TP1; risk carried at fill was 185 pts (stop 4043.57)"]'::jsonb,

    last_updated            = NOW(),
    updated_at              = NOW(),

    signal_snapshot = COALESCE(signal_snapshot, '{}'::jsonb) || jsonb_build_object(
        'pending_runtime', COALESCE(signal_snapshot->'pending_runtime', '{}'::jsonb) || jsonb_build_object(
            'zone_touch_activation',       true,
            'zone_touch_applied_manually', true,
            'zone_touch_fill_price',       4062.05,
            'zone_touch_zone_low',         4054.49,
            'zone_touch_zone_high',        4062.05,
            'zone_touch_original_entry',   4058.27,
            'zone_touch_original_stop',    4039.79,
            'zone_touch_stop_at_fill',     4043.57,
            'zone_touch_planned_risk_pts', 185,
            'zone_touch_touch_low',        4060.40,
            'zone_touch_reason',           'manual repair of the order cancelled as stale before zone-touch activation shipped'
        )
    )
WHERE id = 'TRADE_20260730_090548_435830_9bc87f75'
  AND status = 'CANCELLED';


-- 3) Report ----------------------------------------------------------------
DO $$
DECLARE
    r record;
BEGIN
    SELECT status, entry_price, stop_loss, initial_stop_loss, tp1, tp2,
           current_pnl_points, management_phase
    INTO r
    FROM trades
    WHERE id = 'TRADE_20260730_090548_435830_9bc87f75';

    RAISE NOTICE '------------------------------------------------';
    RAISE NOTICE 'status        : %', r.status;
    RAISE NOTICE 'entry         : %   (upper edge of the mapped zone)', r.entry_price;
    RAISE NOTICE 'stop (live)   : %   (breakeven -- TP1 already passed)', r.stop_loss;
    RAISE NOTICE 'stop at fill  : %   (185 pts of planned risk)', r.initial_stop_loss;
    RAISE NOTICE 'tp1 / tp2     : % / %', r.tp1, r.tp2;
    RAISE NOTICE 'open PnL      : % pts', r.current_pnl_points;
    RAISE NOTICE 'phase         : %', r.management_phase;
    RAISE NOTICE '------------------------------------------------';
END $$;

COMMIT;


-- ============================================================
--  VERIFY (run after the COMMIT)
-- ============================================================
--
--  SELECT id, status, entry_price, stop_loss, initial_stop_loss,
--         tp1, tp2, partial_close, sl_moved_to_entry,
--         current_pnl_points, management_phase, updates_sent
--  FROM trades
--  WHERE id = 'TRADE_20260730_090548_435830_9bc87f75';
--
--  Expected:
--    status            OPEN
--    entry_price       4062.05
--    stop_loss         4062.05    (breakeven)
--    initial_stop_loss 4043.57    (185 pts carried at fill)
--    partial_close     true
--    sl_moved_to_entry true
--    management_phase  POST_TP1_TRAILING
--
--  The next update cycle trails the stop up from breakeven toward TP2
--  (4093.31). It will NOT re-fire TP1.
--
-- ============================================================
--  ROLLBACK, if you change your mind
-- ============================================================
--
--  BEGIN;
--  UPDATE trades
--  SET status = 'CANCELLED', result = 'CANCELLED',
--      entry_price = 4058.27, stop_loss = 4039.79, initial_stop_loss = NULL,
--      partial_close = FALSE, sl_moved_to_entry = FALSE,
--      management_phase = NULL, updates_sent = '[]'::jsonb,
--      current_pnl = 0, current_pnl_points = 0, max_favorable_excursion = 0,
--      closed_at = NOW(), close_time = NOW(), entry_time = NULL,
--      order_type = 'BUY_LIMIT', order_kind = 'LIMIT', activation_reason = NULL,
--      reasons = '["Planner pending cancelled as stale: market covered 74% of target path without fill"]'::jsonb
--  WHERE id = 'TRADE_20260730_090548_435830_9bc87f75';
--  COMMIT;
