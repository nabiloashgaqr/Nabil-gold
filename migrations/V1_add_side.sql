-- idempotent migration: add 'side' column to trades and populate from 'type' when empty
-- Safe: does not drop 'type'.

BEGIN;

ALTER TABLE IF EXISTS trades ADD COLUMN IF NOT EXISTS side TEXT;

-- Populate side for existing rows where side is null or empty
UPDATE trades
SET side = UPPER(COALESCE(type, trade_type, ''))
WHERE (side IS NULL OR TRIM(side) = '') AND (type IS NOT NULL OR trade_type IS NOT NULL);

COMMIT;
