-- ============================================================
-- FIX: trades.exit_warning column type (2026-08-06)
-- ============================================================
-- THE BUG (seen live in analyze run #8966, 2026-08-06 18:33 UTC):
--   PATCH /rest/v1/trades?id=eq.TRADE_..._d4351dad -> HTTP 400
--   {'message': 'invalid input syntax for type boolean: "NEAR_STOP_LOSS"',
--    'code': '22P02'}
--
-- ROOT CAUSE:
--   The column was created as BOOLEAN DEFAULT FALSE, but
--   agents/open_trades_manager.py::_exit_warning() writes rich labels:
--       'NEAR_STOP_LOSS' | 'ADVERSE_MOVE_DEEP' | NULL
--   Writing NULL worked (so the bug stayed hidden on healthy trades);
--   writing a label failed the WHOLE row update atomically, forcing the
--   database layer into its legacy fallback. Critical management fields
--   (stop_loss, sl_moved_to_entry, updates_sent, closed_at) survive the
--   fallback, but exit_warning itself plus management_phase and trailing
--   telemetry columns were silently dropped on every near-stop cycle.
--
-- SAFETY:
--   No code path ever wrote TRUE/FALSE successfully (labels always 400'd),
--   so existing values carry no signal information. The USING clause below
--   still preserves any manual TRUE as 'NEAR_STOP_LOSS', just in case.
-- ============================================================

ALTER TABLE trades
  ALTER COLUMN exit_warning DROP DEFAULT;

ALTER TABLE trades
  ALTER COLUMN exit_warning TYPE text
  USING CASE WHEN exit_warning THEN 'NEAR_STOP_LOSS' ELSE NULL END;

-- VERIFY: must return data_type = 'text'
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'trades' AND column_name = 'exit_warning';
