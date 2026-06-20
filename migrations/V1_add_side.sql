-- Migration V1: add `side` column and sync from existing `type` values.
-- Safe, idempotent migration: does not drop `type` to preserve compatibility.

BEGIN;

-- Add side column if missing
ALTER TABLE IF EXISTS trades ADD COLUMN IF NOT EXISTS side VARCHAR(10);

-- Populate side from existing type where side is empty/null
UPDATE trades
SET side = type
WHERE (side IS NULL OR side = '') AND (type IS NOT NULL AND type <> '');

COMMIT;
