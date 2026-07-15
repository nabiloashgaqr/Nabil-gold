-- =====================================================
-- SmartSignal / Nabil Gold — Supabase Verification Checklist
-- Run these queries in Supabase SQL Editor to verify the production schema.
-- =====================================================

-- 1) Core tables introduced across all upgrade phases
select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in (
    'trades',
    'signals',
    'daily_reports',
    'weekly_reports',
    'agent_weights',
    'macro_context',
    'partial_alert_tracker',
    'post_news_tracker',
    'setup_candidates',
    'setup_state_events',
    'analyst_labels',
    'analyst_comparisons'
  )
order by table_name;

-- 2) Critical trade enrichment / setup columns
select column_name
from information_schema.columns
where table_schema = 'public'
  and table_name = 'trades'
  and column_name in (
    'setup_id',
    'setup_type',
    'setup_state',
    'lead_agent',
    'setup_quality',
    'poi_type',
    'sweep_side',
    'displacement_score',
    'management_profile'
  )
order by column_name;

-- 3) setup_candidates stateful fields
select column_name
from information_schema.columns
where table_schema = 'public'
  and table_name = 'setup_candidates'
  and column_name in (
    'state_key',
    'setup_type',
    'setup_state',
    'lead_agent',
    'setup_quality',
    'quality_score',
    'poi_type',
    'poi_low',
    'poi_high',
    'entry_price',
    'stop_loss',
    'target_price',
    'sweep_side',
    'displacement_score',
    'confidence',
    'first_seen_at',
    'last_seen_at',
    'last_transition_at',
    'transition_count',
    'missing_cycles',
    'last_trade_id'
  )
order by column_name;

-- 4) setup_state_events columns
select column_name
from information_schema.columns
where table_schema = 'public'
  and table_name = 'setup_state_events'
  and column_name in (
    'setup_id',
    'state_key',
    'symbol',
    'from_state',
    'to_state',
    'reason',
    'price',
    'payload',
    'created_at'
  )
order by column_name;

-- 5) analyst distillation tables columns
select table_name, column_name
from information_schema.columns
where table_schema = 'public'
  and table_name in ('analyst_labels', 'analyst_comparisons')
order by table_name, column_name;

-- 6) Verify the active trades view includes PENDING
select definition
from pg_views
where schemaname = 'public'
  and viewname = 'active_trades_view';

-- 7) Check whether any analyst labels exist (overlap cannot score without them)
select count(*) as analyst_labels_count from analyst_labels;

-- 8) Check whether analyst comparisons are being written
select count(*) as analyst_comparisons_count from analyst_comparisons;

-- 9) Check whether setup candidates are being written
select count(*) as setup_candidates_count from setup_candidates;

-- 10) Check latest setup-state events
select *
from setup_state_events
order by created_at desc
limit 10;

-- 11) Check pending trades currently stored
select id, symbol, type, status, order_type, order_kind, entry_price, created_at
from trades
where status = 'PENDING'
order by created_at desc
limit 20;

-- 12) Check whether daily/weekly reports are being archived
select report_date, closed_trades, daily_pnl, win_rate
from daily_reports
order by report_date desc
limit 10;

select week_start, week_end, status
from weekly_reports
order by week_start desc
limit 10;

-- 13) Check row counts of the most important tables in one shot
select 'trades' as table_name, count(*) as rows from trades
union all
select 'setup_candidates', count(*) from setup_candidates
union all
select 'setup_state_events', count(*) from setup_state_events
union all
select 'analyst_labels', count(*) from analyst_labels
union all
select 'analyst_comparisons', count(*) from analyst_comparisons
union all
select 'daily_reports', count(*) from daily_reports
union all
select 'weekly_reports', count(*) from weekly_reports
order by table_name;
