-- Delete the specific bad/legacy paper trade from Supabase.
-- Trade ID: TRADE_20260625_095618_558835_ea62220c
-- Run this in Supabase SQL Editor, then run the Daily Learning job again if you want reports/learning to recalculate without it.

BEGIN;

-- Remove AI review/memory items linked to the trade, if they exist.
DELETE FROM ai_trade_reviews
WHERE trade_id = 'TRADE_20260625_095618_558835_ea62220c';

DELETE FROM ai_memory_rules
WHERE source_trade_id = 'TRADE_20260625_095618_558835_ea62220c';

-- Remove the trade itself.
DELETE FROM trades
WHERE id = 'TRADE_20260625_095618_558835_ea62220c';

COMMIT;

-- Optional verification:
-- SELECT * FROM trades WHERE id = 'TRADE_20260625_095618_558835_ea62220c';
