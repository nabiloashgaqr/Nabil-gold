-- ============================================================
-- 🔧 إصلاحات التنبيه الجزئي — جداول Supabase الجديدة
-- ============================================================
-- التاريخ: 2026-07-06
-- الغرض: إضافة جداول التخزين الدائم لـ partial_alert_tracker
--         و post_news_tracker حتى تعمل في بيئة GitHub Actions (stateless)
--
-- طريقة التشغيل:
--   1. افتح Supabase Dashboard → SQL Editor
--   2. انسخ والصق هذا الملف بالكامل
--   3. اضغط Run
--
-- ⚠️ ملاحظة: هذا ملف تراكمي — يمكن تشغيله بأمان فوق القاعدة الموجودة
--    بدون حذف أي بيانات (IF NOT EXISTS + ADD COLUMN IF NOT EXISTS)
-- ============================================================


-- =====================================================
-- 1) جدول partial_alert_tracker
-- =====================================================
-- يخزّن آخر تنبيه جزئي لكل اتجاه لكل رمز
-- مثلاً: XAU/USD_BUY → آخر سعر أُرسل عنده تنبيه شراء جزئي
-- هذا يمنع الإزعاج المتكرر ويضمن فرق ≥100pts بين التنبيهات
--
-- في بيئة GitHub Actions (stateless) الملف المحلي لا يستمر بين التشغيلات
-- فكان النظام يعامل كل تشغيل كأنه "أول تنبيه" — هذا الجدول يحل المشكلة
-- =====================================================

CREATE TABLE IF NOT EXISTS partial_alert_tracker (
    key TEXT PRIMARY KEY,                    -- مثال: "XAU/USD_BUY" أو "XAU/USD_SELL"
    price DECIMAL(18, 6),                    -- آخر سعر أُرسل عنده التنبيه
    timestamp TIMESTAMPTZ,                   -- وقت إرسال التنبيه (UTC ISO)
    session TEXT,                            -- اسم الجلسة وقت التنبيه (مثلاً "London")
    updated_at TIMESTAMPTZ DEFAULT NOW()     -- وقت آخر تحديث
);

-- فهرس للبحث السريع
CREATE INDEX IF NOT EXISTS idx_partial_alert_key ON partial_alert_tracker(key);

-- Self-heal: إضافة أعمدة إذا الجدول موجود بدونها
ALTER TABLE partial_alert_tracker ADD COLUMN IF NOT EXISTS price      DECIMAL(18, 6);
ALTER TABLE partial_alert_tracker ADD COLUMN IF NOT EXISTS timestamp  TIMESTAMPTZ;
ALTER TABLE partial_alert_tracker ADD COLUMN IF NOT EXISTS session    TEXT;
ALTER TABLE partial_alert_tracker ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- تفعيل trigger للتحديث التلقائي
DROP TRIGGER IF EXISTS update_partial_alert_timestamp ON partial_alert_tracker;
CREATE TRIGGER update_partial_alert_timestamp
    BEFORE UPDATE ON partial_alert_tracker
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- RLS
ALTER TABLE partial_alert_tracker ENABLE ROW LEVEL SECURITY;


-- =====================================================
-- 2) جدول post_news_tracker
-- =====================================================
-- يخزّن أحداث الأخبار التي أُرسل عنها تحليل ما بعد الخبر
-- لمنع التكرار: كل حدث يُرسل مرة واحدة فقط
-- مثلاً: "Non-Farm Payrolls_2026-07-06 12:30" → أُرسل بالفعل
-- =====================================================

CREATE TABLE IF NOT EXISTS post_news_tracker (
    event_key TEXT PRIMARY KEY,              -- مفتاح فريد: "اسم_الحدث_الوقت"
    sent_at TIMESTAMPTZ DEFAULT NOW()        -- وقت إرسال التنبيه
);

-- Self-heal
ALTER TABLE post_news_tracker ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE post_news_tracker ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ DEFAULT NOW();

-- RLS
ALTER TABLE post_news_tracker ENABLE ROW LEVEL SECURITY;


-- =====================================================
-- 3) جدول macro_context (إذا لم يكن موجوداً)
-- =====================================================
-- يخزّن آخر سياق كلي ساعي (DXY, risk sentiment, etc.)
-- يُحدّث كل ساعة من workflow macro_context.yml
-- =====================================================

CREATE TABLE IF NOT EXISTS macro_context (
    id TEXT PRIMARY KEY DEFAULT 'latest',    -- صف واحد دائماً: "latest"
    context JSONB DEFAULT '{}'::jsonb,        -- البيانات الكلية الكاملة
    source TEXT,                              -- مصدر البيانات (مثلاً "twelvedata_hourly")
    generated_at TIMESTAMPTZ,                 -- وقت توليد البيانات
    updated_at TIMESTAMPTZ DEFAULT NOW()      -- وقت آخر تحديث
);

-- Self-heal
ALTER TABLE macro_context ADD COLUMN IF NOT EXISTS context      JSONB DEFAULT '{}'::jsonb;
ALTER TABLE macro_context ADD COLUMN IF NOT EXISTS source       TEXT;
ALTER TABLE macro_context ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ;
ALTER TABLE macro_context ADD COLUMN IF NOT EXISTS updated_at   TIMESTAMPTZ DEFAULT NOW();

-- trigger
DROP TRIGGER IF EXISTS update_macro_context_timestamp ON macro_context;
CREATE TRIGGER update_macro_context_timestamp
    BEFORE UPDATE ON macro_context
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- RLS
ALTER TABLE macro_context ENABLE ROW LEVEL SECURITY;


-- =====================================================
-- 4) عمود market_phase في جدول trades (إذا لم يكن موجوداً)
-- =====================================================
-- يُستخدم من _entry_enrichment في database.py
-- بعض السكيمات القديمة لا تحتويه
-- =====================================================

ALTER TABLE trades ADD COLUMN IF NOT EXISTS market_phase TEXT;


-- =====================================================
-- 5) إعادة تحميل schema cache
-- =====================================================
NOTIFY pgrst, 'reload schema';


-- =====================================================
-- ✅ تم! التحقق
-- =====================================================
-- بعد التشغيل، تحقق من وجود الجداول:
--
-- SELECT table_name FROM information_schema.tables
-- WHERE table_name IN ('partial_alert_tracker', 'post_news_tracker', 'macro_context')
-- ORDER BY table_name;
--
-- النتيجة المتوقعة:
--   macro_context
--   partial_alert_tracker
--   post_news_tracker
--
-- تحقق من هيكل partial_alert_tracker:
--   SELECT * FROM partial_alert_tracker LIMIT 5;
--
-- (سيكون فارغاً في البداية — يُملأ تلقائياً عند أول تنبيه جزئي)
-- ============================================================
