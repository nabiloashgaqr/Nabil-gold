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
--  without fill"), which was the correct behaviour for the code as it stood
--  and the wrong outcome for the trade.
--
--  Zone-touch activation (SmartSignal_phaseH_zone_rules) fixes this going
--  forward. This script repairs the one order that was already lost.
--
--  THE FILL
--  --------
--    entry  4062.05   the upper edge of the published zone -- the worst
--                     price inside the area the map itself drew
--    stop   4043.57   moved by exactly the same distance as the entry, so
--                     the planned 185-point risk is preserved. Chasing at
--                     market with the original 4039.79 stop would have meant
--                     265 points of risk at RR 1.02, below the 1.5 floor.
--    RR     1.69 to TP2
--
--  Run once. Wrapped in a transaction and guarded so a second run is a
--  no-op. Verification queries are at the bottom.
-- ============================================================

BEGIN;

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
    -- The stop at FILL was 4043.57 (the mapped 185 pts, carried down with the
    -- entry). It is recorded as initial_stop_loss because that is the risk the
    -- trade actually took on. The live stop below is already at breakeven,
    -- since TP1 was passed on the way to 4081.14.
    initial_stop_loss       = 4043.57,

    -- the map's own targets are unchanged
    tp1                     = 4075.79,
    tp2                     = 4093.31,

    -- TP1 (4075.79) was already exceeded on the way to 4081.14, so the trade
    -- is recorded in its post-TP1 state: partial taken, stop at breakeven.
    -- Leaving it as a plain OPEN would let the manager re-fire TP1 events for
    -- a level price has long passed.
    partial_close           = TRUE,
    sl_moved_to_entry       = TRUE,
    management_phase        = 'POST_TP1_TRAILING',
    updates_sent            = '["ORDER_FILLED", "TP1_HIT", "MOVE_SL_TO_BE"]'::jsonb,

    -- breakeven is the entry, which is where the stop now sits
    stop_loss               = 4062.05,

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

    reasons                 = '["Zone-touch activation: filled at the published zone edge after price traded inside the mapped area without touching the reference entry", "Stop moved 4039.79 -> 4062.05 (breakeven after TP1); planned risk was 185 pts at fill"]'::jsonb,

    last_updated            = NOW(),
    updated_at              = NOW(),

    -- Keep the audit trail: record on the snapshot how this fill was derived.
    signal_snapshot = COALESCE(signal_snapshot, '{}'::jsonb) || jsonb_build_object(
        'pending_runtime', COALESCE(signal_snapshot->'pending_runtime', '{}'::jsonb) || jsonb_build_object(
            'zone_touch_activation',      true,
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
    SELECT status, entry_price, stop_loss, tp1, tp2, current_pnl_points
    INTO r
    FROM trades
    WHERE id = 'TRADE_20260730_090548_435830_9bc87f75';

    RAISE NOTICE '----------------------------------------';
    RAISE NOTICE 'status      : %', r.status;
    RAISE NOTICE 'entry       : %  (zone edge)', r.entry_price;
    RAISE NOTICE 'stop        : %  (breakeven, TP1 already passed)', r.stop_loss;
    RAISE NOTICE 'tp1 / tp2   : % / %', r.tp1, r.tp2;
    RAISE NOTICE 'open PnL    : % pts', r.current_pnl_points;
    RAISE NOTICE '----------------------------------------';
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
--    stop_loss         4062.05   (breakeven -- TP1 was already exceeded)
--    initial_stop_loss 4043.57   (the real risk carried at fill: 185 pts)
--    partial_close     true
--    sl_moved_to_entry true
--
--  The next update cycle will pick it up, trail the stop from breakeven and
--  manage it toward TP2 (4093.31) normally.
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
--      current_pnl = 0, current_pnl_points = 0,
--      max_favorable_excursion = 0,
--      closed_at = NOW(), close_time = NOW(),
--      entry_time = NULL, order_type = 'BUY_LIMIT', order_kind = 'LIMIT',
--      activation_reason = NULL,
--      reasons = '["Planner pending cancelled as stale: market covered 74% of target path without fill"]'::jsonb
--  WHERE id = 'TRADE_20260730_090548_435830_9bc87f75';
--  COMMIT;
