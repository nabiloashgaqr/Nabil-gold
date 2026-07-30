-- ============================================================
--  THESIS_EXIT — a new closing status
-- ============================================================
--
--  WHY
--  ---
--  An automatic close written by the thesis check was being stored as
--  MANUAL_CLOSE. Nobody closed those trades by hand; the system did, because
--  it judged the thesis dead. The label made every automatic exit look like
--  an operator decision in the database, the weekly report and the learning
--  service alike.
--
--  MANUAL_CLOSE is kept, and still means what it says: a human closed the
--  trade (scripts/run_close_trade_now.py). The two are now distinguishable.
--
--  RUN THIS BEFORE UPLOADING THE CODE.
--  The `trades.status` column has a CHECK constraint. Until it accepts
--  'THESIS_EXIT', any attempt to write the new status is rejected by
--  Postgres and the trade update fails.
--
--  Safe to run twice. Wrapped in a transaction: if any statement fails,
--  nothing is applied.
-- ============================================================

BEGIN;

-- 1) Let the column accept the new value ---------------------------------
--    The constraint name is discovered rather than assumed, because it may
--    have been created implicitly (trades_status_check) or by an earlier
--    migration under a different name.
DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT con.conname INTO constraint_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'trades'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) ILIKE '%MANUAL_CLOSE%'
    LIMIT 1;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE trades DROP CONSTRAINT %I', constraint_name);
        RAISE NOTICE 'Dropped existing status constraint: %', constraint_name;
    ELSE
        RAISE NOTICE 'No MANUAL_CLOSE status constraint found; adding a fresh one.';
    END IF;
END $$;

ALTER TABLE trades
    ADD CONSTRAINT trades_status_check CHECK (status IN (
        'OPEN', 'PARTIAL', 'PENDING', 'TP1_HIT', 'TP2_HIT', 'SL_HIT', 'BE_HIT',
        'THESIS_EXIT', 'MANUAL_CLOSE', 'EXPIRED', 'CLOSED', 'CANCELLED'
    ));

-- 2) Relabel the history ---------------------------------------------------
--    Only rows the automatic thesis check actually wrote. The reason string
--    it stores always begins "Automatic thesis exit:", which is what makes
--    these separable from genuine manual closes after the fact.
--
--    `reasons` is a JSON array of strings; the ILIKE on its text form matches
--    the phrase wherever it sits in the array.
UPDATE trades
SET status = 'THESIS_EXIT'
WHERE status = 'MANUAL_CLOSE'
  AND reasons::text ILIKE '%automatic thesis exit%';

-- 3) Report what happened --------------------------------------------------
DO $$
DECLARE
    thesis_rows integer;
    manual_rows integer;
    suspicious  integer;
BEGIN
    SELECT count(*) INTO thesis_rows FROM trades WHERE status = 'THESIS_EXIT';
    SELECT count(*) INTO manual_rows FROM trades WHERE status = 'MANUAL_CLOSE';
    -- Anything still MANUAL_CLOSE that mentions a thesis exit would mean the
    -- match above missed it. Expected to be zero.
    SELECT count(*) INTO suspicious
    FROM trades
    WHERE status = 'MANUAL_CLOSE' AND reasons::text ILIKE '%thesis exit%';

    RAISE NOTICE '--------------------------------------------';
    RAISE NOTICE 'THESIS_EXIT rows after migration : %', thesis_rows;
    RAISE NOTICE 'MANUAL_CLOSE rows remaining      : %', manual_rows;
    RAISE NOTICE 'Unconverted thesis-exit rows     : %  (expected 0)', suspicious;
    RAISE NOTICE '--------------------------------------------';
END $$;

COMMIT;

-- ============================================================
--  VERIFY (run separately, after the COMMIT)
-- ============================================================
--
--  SELECT status, count(*)
--  FROM trades
--  WHERE status IN ('THESIS_EXIT', 'MANUAL_CLOSE')
--  GROUP BY status;
--
--  -- the constraint now lists THESIS_EXIT:
--  SELECT pg_get_constraintdef(oid)
--  FROM pg_constraint
--  WHERE conname = 'trades_status_check';
--
-- ============================================================
--  ROLLBACK, if ever needed
-- ============================================================
--
--  BEGIN;
--  UPDATE trades SET status = 'MANUAL_CLOSE' WHERE status = 'THESIS_EXIT';
--  ALTER TABLE trades DROP CONSTRAINT trades_status_check;
--  ALTER TABLE trades ADD CONSTRAINT trades_status_check CHECK (status IN (
--      'OPEN', 'PARTIAL', 'PENDING', 'TP1_HIT', 'TP2_HIT', 'SL_HIT', 'BE_HIT',
--      'MANUAL_CLOSE', 'EXPIRED', 'CLOSED', 'CANCELLED'
--  ));
--  COMMIT;
