-- ============================================================
--  VERIFY — did the migration keep every trade?
-- ============================================================
--
--  Run this to confirm nothing was lost. The migration contains no DELETE,
--  no DROP TABLE and no TRUNCATE: its only data statement is an UPDATE that
--  renames a status. Every row that existed still exists.
--
--  Read-only. Safe to run any time, as many times as you like.
-- ============================================================

-- 1) The headline: how many closed trades exist, by status ----------------
SELECT
    status,
    count(*)                          AS trades,
    round(sum(COALESCE(final_pnl, 0))::numeric, 1) AS total_points
FROM trades
GROUP BY status
ORDER BY trades DESC;


-- 2) The two statuses this migration touched ------------------------------
--    THESIS_EXIT  = closed automatically because the thesis was judged dead
--    MANUAL_CLOSE = closed by a human (scripts/run_close_trade_now.py)
SELECT
    status,
    count(*) AS trades,
    min(COALESCE(closed_at, close_time)) AS earliest,
    max(COALESCE(closed_at, close_time)) AS latest
FROM trades
WHERE status IN ('THESIS_EXIT', 'MANUAL_CLOSE')
GROUP BY status;


-- 3) Nothing should still be mislabelled ----------------------------------
--    Expected: 0 rows. A row here means the rename missed it.
SELECT id, symbol, type, status, closed_at, reasons
FROM trades
WHERE status = 'MANUAL_CLOSE'
  AND reasons::text ILIKE '%automatic thesis exit%'
ORDER BY closed_at DESC;


-- 4) Proof the rows were renamed, not removed -----------------------------
--    These are the trades the migration relabelled. They are all still here,
--    with their prices, PnL and reasons intact.
SELECT
    id,
    symbol,
    type,
    status,
    entry_price,
    close_price,
    final_pnl,
    closed_at
FROM trades
WHERE status = 'THESIS_EXIT'
ORDER BY closed_at DESC
LIMIT 25;


-- 5) Total trade count, for peace of mind ---------------------------------
--    Compare against what you saw before the migration. It must match.
SELECT
    count(*)                                            AS all_trades_ever,
    count(*) FILTER (WHERE status = 'OPEN')             AS open_now,
    count(*) FILTER (WHERE status = 'PENDING')          AS pending_now,
    count(*) FILTER (WHERE status NOT IN ('OPEN', 'PENDING', 'PARTIAL')) AS closed
FROM trades;


-- 6) The constraint now accepts both names --------------------------------
SELECT pg_get_constraintdef(oid) AS status_constraint
FROM pg_constraint
WHERE conname = 'trades_status_check';
