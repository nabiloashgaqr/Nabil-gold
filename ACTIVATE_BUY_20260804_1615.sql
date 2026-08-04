-- ============================================================================
-- Manually activate the 12:38 BUY day map as a live trade
-- ============================================================================
--
-- WHY THIS EXISTS
-- ---------------
-- The planner published a BUY map at 12:38 on 2026-08-04:
--
--     Grade A 80.2% · zone 4061.90 -> 4070.88 · ref entry 4066.39
--     invalidation 4047.01 · TP1 4084.01 · TP2 4100.00
--
-- No order was created. Only two agents cleared the 70% bar (Technical 92,
-- Multi-Timeframe 92); Price Action read BUY at 68% -- two points short --
-- so the count came to 2 against a required 3.
--
-- Gold then traded to 4088.85. TP1 at 4084.01 was reached while the system
-- stood aside. The operator is recording the trade the map called for.
--
-- Operator-supplied facts:
--   activation  16:15 UTC at 4066.00
--   lowest print after entry: 4062.00  (40 pts of adverse travel, 21% of risk)
--
-- GEOMETRY
--   risk  = 4066.00 - 4047.01 = 18.99  = 189.9 points
--   TP1   = 4084.01            = 180.1 points = 0.95R
--   TP2   = 4100.00            = 340.0 points = 1.79R   (clears min_rr 1.5)
--
-- SAFETY
--   * One INSERT. No UPDATE or DELETE, so nothing existing can be harmed.
--   * The id is deterministic and unique; re-running is a no-op thanks to
--     ON CONFLICT DO NOTHING.
--   * status OPEN means OpenTradesManager takes over on the next cycle: it
--     will trail, book TP1, and close on TP2 or the stop exactly as it would
--     for a trade it opened itself.
--
-- CHECK BEFORE RUNNING
--   Confirm no live trade already exists on this symbol, or the manager will
--   be managing two positions at once:
--
--     SELECT id, status, type, entry_price
--       FROM trades
--      WHERE symbol = 'XAU/USD'
--        AND status IN ('OPEN','PARTIAL','TP1_HIT','PENDING');
--
-- ============================================================================

INSERT INTO trades (
    id,
    symbol,
    type,
    side,
    status,
    order_kind,
    order_type,
    entry_price,
    entry_time,
    opened_at,
    stop_loss,
    initial_stop_loss,
    tp1,
    tp2,
    confidence,
    current_price,
    current_pnl,
    current_pnl_points,
    max_adverse_excursion,
    max_favorable_excursion,
    sl_moved_to_entry,
    partial_close,
    closed_fraction,
    pending_cycles,
    trading_mode,
    paper_trading,
    result,
    created_at,
    last_updated,
    market_data_source,
    planned_rr,
    reasons,
    signal_snapshot
) VALUES (
    'TRADE_20260804_161500_000000_manual01',
    'XAU/USD',
    'BUY',
    'BUY',
    'OPEN',
    'MARKET',
    'BUY_MARKET',
    4066.00,
    '2026-08-04T16:15:00+00:00',
    '2026-08-04T16:15:00+00:00',
    4047.01,
    4047.01,
    4084.01,
    4100.00,
    80.2,
    4066.00,
    0,
    0,
    -- Worst adverse travel the operator observed: 4062.00, i.e. -40 points.
    -- Recorded as a negative point value, which is the convention
    -- OpenTradesManager and analyze_sl_floor both read.
    -40.0,
    0,
    false,
    false,
    0,
    0,
    'paper',
    true,
    NULL,
    '2026-08-04T16:15:00+00:00',
    '2026-08-04T16:15:00+00:00',
    'manual_operator',
    1.79,
    '["Manual activation of the 12:38 session plan (A 80.2%).",
      "Planner published BUY 4061.90-4070.88; execution gate refused it with 2 qualified agents of 3 required.",
      "Price Action read BUY at 68%, two points under the 70% bar in force at the time.",
      "Entered 4066.00 at 16:15 UTC; lowest print after entry 4062.00."]'::jsonb,
    '{
       "manual_entry": true,
       "entry_mode": "manual_operator",
       "source_plan": "SESSION_PLAN_20260804_123824_548972_66ee7b50",
       "session_bias": "BUY",
       "scenario_type": "FAILED_RECLAIM_CONTINUATION",
       "poi_classification": "HIGH_PROBABILITY_POI",
       "signal": {
         "type": "BUY",
         "entry": {"price": 4066.00, "low": 4061.90, "high": 4070.88,
                   "kind": "MARKET", "order_type": "BUY_MARKET"},
         "stop_loss": 4047.01,
         "tp1": 4084.01,
         "tp2": 4100.00,
         "tp1_rr": 0.95,
         "tp2_rr": 1.79,
         "rr_ratio": 1.79
       },
       "risk_geometry": {
         "shipped_sl_points": 189.9,
         "planned_rr_tp1": 0.95,
         "planned_rr_tp2": 1.79
       },
       "manual_note": "Recorded because the execution gate refused a correct map. TP1 4084.01 was reached at 4088.85."
     }'::jsonb
)
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- VERIFY
-- ============================================================================
SELECT
    id,
    status,
    type,
    entry_price,
    stop_loss,
    tp1,
    tp2,
    ROUND(((entry_price - stop_loss) / 0.1)::numeric, 1)  AS risk_points,
    ROUND(((tp1 - entry_price) / 0.1)::numeric, 1)        AS tp1_points,
    ROUND(((tp2 - entry_price) / 0.1)::numeric, 1)        AS tp2_points,
    ROUND(((tp2 - entry_price) / (entry_price - stop_loss))::numeric, 2) AS tp2_rr,
    max_adverse_excursion,
    entry_time
FROM trades
WHERE id = 'TRADE_20260804_161500_000000_manual01';

-- Expected:
--   status OPEN · risk_points 189.9 · tp1_points 180.1 · tp2_points 340.0
--   tp2_rr 1.79 · max_adverse_excursion -40.0

-- ============================================================================
-- UNDO, if the trade should not have been recorded
-- ============================================================================
-- DELETE FROM trades WHERE id = 'TRADE_20260804_161500_000000_manual01';
