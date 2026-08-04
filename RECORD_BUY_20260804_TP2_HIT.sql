-- ============================================================================
-- Record the 2026-08-04 BUY as a CLOSED trade that reached TP2
-- Entry 09:15 local (06:15 UTC) at 4060.95 · exit 16:25 local (13:25 UTC) at 4090.00
-- Replaces the earlier draft (ACTIVATE_BUY_20260804_1615.sql)
-- ============================================================================
--
-- WHAT THIS RECORDS
-- -----------------
-- Plan (PLAN UPDATE · Asia Morning · A+ 97.8% · EXTREME_POI):
--     zone 4057.95 -> 4063.95 · ref entry 4060.95
--     invalidation 4045.95 · TP1 4075.00 · TP2 4090.00
--     "Execution stop normalized to the configured 150-point minimum"
--
-- Chart, 15-minute candle stamped Tue 04 Aug '26 16:15 on the operator's
-- LOCAL-TIME chart (Asia/Jerusalem, UTC+3) = 13:15 UTC, covering
-- 16:15-16:30 local = 13:15-13:30 UTC:
--     O 4063.788 · H 4091.377 · L 4062.100 · C 4078.512
--
-- Operator: entry in that window, exit 16:25 local = 13:25 UTC.
--
-- TIMEZONE CORRECTION (this revision)
-- -----------------------------------
-- Every operator-stated time in this trade is Asia/Jerusalem (UTC+3).
-- An earlier draft of this record read the chart stamps as UTC and stored
-- the exit at 16:25 UTC. Corrected: exit 16:25 local = 13:25 UTC, and the
-- candle stamped 16:15 on the chart is the 13:15 UTC candle. Entry was
-- already correct (09:15 local = 06:15 UTC). Held time is therefore
-- 06:15 -> 13:25 UTC = 430 minutes, not 610.
--
-- ENTRY: 09:15 LOCAL (Asia/Jerusalem, UTC+3) = 06:15 UTC, AT 4060.95
-- --------------------------------------------------------------------
-- Operator-stated: the position was opened at 09:15 in the morning, at the
-- planned zone reference 4060.95. Stored as 06:15 UTC, because every other
-- row in this table is UTC and mixing zones in one column is how a later
-- comparison ends up silently wrong.
--
-- This also fits the plan's own label: "Session: Asia Morning".
--
-- WHAT IS VERIFIED, AND WHAT IS NOT
--   verified   the exit. The 15m candle stamped 16:15 local (= 13:15 UTC,
--              the candle covering the 13:25 UTC exit) printed a high of
--              4091.377, clearing TP2 at 4090.00, and its low of 4062.100
--              never came near the 4045.95 stop.
--   NOT seen   the entry. Both screenshots start at 12:00 LOCAL (= 09:00
--              UTC), so neither shows the 06:15 UTC entry. The 4060.95 fill
--              is taken on the operator's word, not read off a chart.
--              `max_adverse_excursion` is therefore recorded as 0 and
--              flagged as unobserved in signal_snapshot -- the low between
--              06:15 UTC and 09:00 UTC is simply unknown, and inventing a
--              number there would corrupt analyze_sl_floor, which reads
--              exactly that field.
--
-- GEOMETRY
--     risk  = 4060.95 - 4045.95 = 15.00  = 150.0 points
--             (matches the plan's own note: "stop normalized to the
--              configured 150-point minimum" — an independent cross-check
--              that this entry price is the one the plan was built on)
--     TP1   = 4075.00                    = 140.5 points = 0.94R
--     TP2   = 4090.00                    = 290.5 points = 1.94R  (>= 1.5)
--     realised = +290.5 points
--     max favourable = 4091.377 - 4060.95 = 304.3 points
--     max adverse    = unobserved, stored as 0
--
-- WHY TP2 IS CONFIRMED
--     candle (13:15 UTC) high 4091.377 >= TP2 4090.00  -> target reached
--     candle (13:15 UTC) low  4062.100 >  stop 4045.95 -> stop never threatened
--     exit 13:25 UTC falls INSIDE that candle          -> consistent
--
-- SCHEMA NOTES (verified against supabase_schema_unified.sql, not assumed)
--   * `closed_fraction` does not exist on this table.
--   * `opened_at` and `trade_type` are GENERATED columns; writing to them
--     raises, so they are omitted and Postgres derives them.
--   * trading_mode is 'paper' -- the value every system-written trade uses
--     (services/database.py:139). Nothing filters on this column, so a
--     different value would only make this row an outlier in your own stats.
--   * The id follows the generator's format exactly
--     (TRADE_%Y%m%d_%H%M%S_%f_<8 hex>, services/database.py:97).
--     How the row was created is recorded in signal_snapshot, not in the id.
--     The 161500 segment mirrors the exit candle's 16:15 label on the
--     operator's local-time chart; the id is a unique key, not a UTC claim.
--
-- ============================================================================

BEGIN;

-- 1) Remove the superseded draft, if it was ever run.
--    Scoped to that exact id, so nothing else can be touched.
DELETE FROM trades
 WHERE id = 'TRADE_20260804_161500_445238_a056d0d8';

-- 2) Insert the trade as it actually happened: opened and closed on TP2.
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
    stop_loss,
    initial_stop_loss,
    tp1,
    tp2,
    confidence,
    current_price,
    current_pnl,
    current_pnl_points,
    final_pnl,
    final_pnl_points,
    close_price,
    close_time,
    closed_at,
    result,
    max_adverse_excursion,
    max_favorable_excursion,
    sl_moved_to_entry,
    partial_close,
    pending_cycles,
    trading_mode,
    paper_trading,
    created_at,
    last_updated,
    updated_at,
    market_data_source,
    planned_risk_points,
    planned_tp2_points,
    planned_rr,
    session_label,
    setup_type,
    poi_type,
    sweep_side,
    daily_bias_at_entry,
    macro_bias_at_entry,
    activation_reason,
    reasons,
    signal_snapshot
) VALUES (
    'TRADE_20260804_161500_221954_4e7c03d7',
    'XAU/USD',
    'BUY',
    'BUY',
    'TP2_HIT',
    'MARKET',
    'BUY_MARKET',
    4060.95,
    '2026-08-04T06:15:00+00:00',
    4045.95,
    4045.95,
    4075.00,
    4090.00,
    98,
    4090.00,
    0,
    0,
    290.5,
    290.5,
    4090.00,
    '2026-08-04T13:25:00+00:00',
    '2026-08-04T13:25:00+00:00',
    'WIN',
    0,
    304.3,
    true,
    true,
    0,
    'paper',
    true,
    '2026-08-04T06:15:00+00:00',
    '2026-08-04T13:25:00+00:00',
    '2026-08-04T13:25:00+00:00',
    'manual_operator',
    150.0,
    290.5,
    1.94,
    'Asia Morning',
    'FAILED_RECLAIM_CONTINUATION',
    'order_block',
    'sell_side',
    'BULLISH',
    'BEARISH_GOLD',
    'Recorded from the A+ 97.8% day map; the execution gate had refused it before the agent bar moved to 67.',
    '["Manual record of the PLAN UPDATE day map (A+ 97.8%, EXTREME_POI).",
      "Planner published BUY zone 4057.95-4063.95, invalidation 4045.95, TP1 4075.00, TP2 4090.00.",
      "Entry 09:15 local (Asia/Jerusalem, UTC+3) = 06:15 UTC at the planned zone reference 4060.95.",
      "Risk 150.0 points, matching the plan note: stop normalized to the configured 150-point minimum.",
      "15m candle 13:15 UTC (stamped 16:15 on the local-time chart): O 4063.788 H 4091.377 L 4062.100 C 4078.512.",
      "High 4091.377 cleared TP2 4090.00; the candle low 4062.100 never approached the 4045.95 stop.",
      "Exit 13:25 UTC (16:25 Asia/Jerusalem) at TP2 for +290.5 points (1.94R); held 430 minutes.",
      "Timezone correction: the exit was 16:25 Asia/Jerusalem = 13:25 UTC. An earlier draft of this record mislabeled the local chart stamps as UTC.",
      "Entry price is operator-stated: neither screenshot covers 06:15 UTC (they start at 12:00 local = 09:00 UTC), so max_adverse_excursion is unobserved and stored as 0."]'::jsonb,
    '{
       "manual_entry": true,
       "entry_mode": "manual_operator",
       "supersedes": "TRADE_20260804_161500_445238_a056d0d8",
       "session_bias": "BUY",
       "scenario_type": "FAILED_RECLAIM_CONTINUATION",
       "poi_classification": "EXTREME_POI",
       "planner_grade": "A+",
       "planner_score": 97.8,
       "signal": {
         "type": "BUY",
         "entry": {"price": 4060.95, "low": 4057.95, "high": 4063.95,
                   "kind": "MARKET", "order_type": "BUY_MARKET",
                   "entry_time_local": "2026-08-04T09:15:00+03:00",
                   "exit_time_local": "2026-08-04T16:25:00+03:00"},
         "stop_loss": 4045.95,
         "tp1": 4075.00,
         "tp2": 4090.00,
         "tp1_rr": 0.94,
         "tp2_rr": 1.94,
         "rr_ratio": 1.94
       },
       "risk_geometry": {
         "shipped_sl_points": 150.0,
         "planned_rr_tp1": 0.94,
         "planned_rr_tp2": 1.94
       },
       "source_candle_15m": {
         "time": "2026-08-04T13:15:00+00:00",
         "chart_label": "16:15 Asia/Jerusalem on the local-time chart (UTC+3)",
         "open": 4063.788, "high": 4091.377,
         "low": 4062.100, "close": 4078.512
       },
       "max_adverse_observed": false,
       "max_adverse_note": "Entry at 06:15 UTC is outside both screenshots (they start at 12:00 local = 09:00 UTC). The low between entry (06:15 UTC) and 09:00 UTC was never seen, so max_adverse_excursion is 0 by absence of data, not by measurement. analyze_sl_floor reads this field -- treat this row as unjudgeable for the floor question.",
       "manual_note": "Entry time and price are operator-stated. The exit is chart-confirmed: the 13:15 UTC candle (stamped 16:15 on the local-time chart) high of 4091.377 cleared TP2 at 4090.00, and the 13:25 UTC exit falls inside that candle."
     }'::jsonb
);

COMMIT;

-- ============================================================================
-- VERIFY — every number recomputed from the stored row, not restated
-- ============================================================================
SELECT
    id,
    status,
    result,
    entry_price,
    close_price,
    stop_loss,
    tp2,
    ROUND(((entry_price - stop_loss) / 0.1)::numeric, 1)   AS risk_points,
    ROUND(((tp2 - entry_price) / 0.1)::numeric, 1)         AS tp2_points,
    ROUND(((close_price - entry_price) / 0.1)::numeric, 1) AS realised_points,
    ROUND(((tp2 - entry_price) / (entry_price - stop_loss))::numeric, 2) AS tp2_rr,
    final_pnl_points,
    max_favorable_excursion,
    max_adverse_excursion,
    entry_time,
    close_time,
    EXTRACT(EPOCH FROM (close_time - entry_time)) / 60     AS held_minutes,
    opened_at
FROM trades
WHERE id = 'TRADE_20260804_161500_221954_4e7c03d7';

-- Expected:
--   status TP2_HIT · result WIN
--   risk_points 150.0 · tp2_points 290.5 · realised_points 290.5
--   tp2_rr 1.94 · final_pnl_points 290.5
--   max_favorable 304.3 · max_adverse 0 (unobserved) · opened_at = entry_time
--   entry_time 2026-08-04 06:15+00 · close_time 2026-08-04 13:25+00
--   held_minutes 430  (09:15 -> 16:25 Asia/Jerusalem, both UTC+3)

-- Confirm the superseded draft is gone and only one row exists today:
SELECT id, status, entry_price, close_price, final_pnl_points
  FROM trades
 WHERE symbol = 'XAU/USD'
   AND created_at >= '2026-08-04T00:00:00+00:00'
 ORDER BY created_at DESC;

-- ============================================================================
-- UNDO
-- ============================================================================
-- DELETE FROM trades WHERE id = 'TRADE_20260804_161500_221954_4e7c03d7';
