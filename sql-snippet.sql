-- ============================================================
--  Weekly AI Performance Report — جدول جديد
--  هذا الملف عبارة عن snippet توضيحي. تعريف الجدول الفعلي موجود الآن في supabase_schema.sql
--  تم نقل CREATE TABLE الخاص بـ weekly_reports إلى supabase_schema.sql لمنع التكرار.
-- ============================================================

-- CREATE TABLE IF NOT EXISTS weekly_reports (
--     id BIGSERIAL PRIMARY KEY,
--     week_start DATE NOT NULL,
--     week_end DATE NOT NULL,
--     stats_json JSONB NOT NULL,
--     report_text TEXT NOT NULL,
--     recommendations JSONB,
--     tokens_used INTEGER DEFAULT 0,
--     cost NUMERIC(10, 6) DEFAULT 0.0,
--     status TEXT NOT NULL,
--     created_at TIMESTAMPTZ DEFAULT NOW()
-- );

-- CREATE INDEX IF NOT EXISTS idx_weekly_reports_week_start
--     ON weekly_reports (week_start DESC);

-- ============================================================
--  عدّاد HALT/CAUTION (اختياري — للـ Weekly Report)
--  مفيد إذا كانت session_log تحتوي على event و created_at
-- ============================================================

-- مثال استعلام يستخدمه التقرير الأسبوعي:
-- SELECT COUNT(*) FROM session_log
-- WHERE event = 'HALT_ACTIVATED' AND created_at >= NOW() - INTERVAL '7 days';
