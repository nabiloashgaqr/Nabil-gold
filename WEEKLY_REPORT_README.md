# 📊 Weekly AI Performance Report — دليل التشغيل

## الملفات المُضافة

| الملف | الموقع | الوظيفة |
|---|---|---|
| `services/weekly_report.py` | `services/` | جمع البيانات + Prompt + Groq + Telegram |
| `scripts/run_weekly_report.py` | `scripts/` | نقطة دخول GitHub Actions |
| `tests/test_weekly_report.py` | `tests/` | 17 اختبار وحدة |
| `weekly_report.yml` | `.github/workflows/` | Workflow جديد |
| `config-snippet.json` | root | قسم `weekly_report` لإضافته إلى config.json |
| `sql-snippet.sql` | root | جدول `weekly_reports` لإضافته إلى supabase_schema.sql |

## خطوات التطبيق

### 1) انسخ الملفات
```bash
# داخل مجلد Nabil-gold/
cp services/weekly_report.py       services/
cp scripts/run_weekly_report.py    scripts/
cp tests/test_weekly_report.py     tests/
cp weekly_report.yml               .github/workflows/
```

### 2) حدّث config.json
افتح `config.json` وأضف القسم التالي (في نهاية الملف):
```json
{
  "weekly_report": {
    "enabled": true,
    "day_of_week": 6,
    "lookback_days": 7,
    "min_trades_for_report": 5,
    "max_chars": 3500,
    "send_telegram": true,
    "storage_path": "storage/weekly_report.json",
    "timezone": "Asia/Hebron"
  }
}
```

> `day_of_week`: 0=Mon, 1=Tue, ..., 6=Sun (افتراضي: الأحد)

### 3) حدّث supabase_schema.sql
أضف جدول `weekly_reports` في نهاية الملف:
```sql
CREATE TABLE IF NOT EXISTS weekly_reports (
    id BIGSERIAL PRIMARY KEY,
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    stats_json JSONB NOT NULL,
    report_text TEXT NOT NULL,
    recommendations JSONB,
    tokens_used INTEGER DEFAULT 0,
    cost NUMERIC(10, 6) DEFAULT 0.0,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 4) اختبر محليًا
```bash
python scripts/run_weekly_report.py
# يجب أن يُنشئ storage/weekly_report.json
```

### 5) شغّل الاختبارات
```bash
python -m pytest tests/test_weekly_report.py -v
# يجب أن تُمرّ 17 اختبار
```

### 6) GitHub Actions
- الـ workflow سيعمل تلقائيًا كل أحد 23:30 Asia/Hebron
- أو يدويًا من Actions tab → "Run workflow"

## كيف يعمل

```
[GitHub Actions - Sunday 23:30]
    ↓
[scripts/run_weekly_report.py]
    ↓
[WeeklyReportService.collect_stats()]
    ↓   يجلب آخر 7 أيام من:
    ↓   • trades (Supabase)
    ↓   • session_log (HALT/CAUTION count)
    ↓   • ai_memory_rules (جديد)
    ↓   • signals (محظورة)
    ↓
[WeeklyReportService.build_prompt()]
    ↓   يبني Prompt مع البيانات الفعلية
    ↓
[ai_service._call_ai(prompt, agent_type='weekly_report')]
    ↓   Groq يولّد تقرير markdown بالعربية
    ↓
[WeeklyReportService.send_to_telegram()]
    ↓   يقسّم لو > 4096 حرف (حد Telegram)
    ↓
[Telegram] + [storage/weekly_report.json]
```

## مثال على التقرير المُولَّد

```
═══════════════════════════════════
📊 التقرير الأسبوعي
الأسبوع: 2026-06-14 → 2026-06-21
═══════════════════════════════════

📈 ملخص الأداء
• إجمالي الصفقات: 42
• نسبة الفوز: 57.1%
• صافي النقاط: +127.4
• أكبر ربح: +18.2
• أكبر خسارة: -12.7

🤖 أداء الوكلاء
• أفضل: SMCAgent — 68% WR، +45.2 نقطة
• أسوأ: ClassicalAgent — 42% WR، -8.5 نقطة

📅 أفضل/أسوأ يوم
• أفضل: الثلاثاء — +28.4 نقطة
• أسوأ: الخميس — -18.2 نقطة

🌍 أداء الجلسات
• NY Open (15:00-17:00): 64% WR
• London (09:00-12:00): 58% WR

⚠️ المخاطر
• HALT فعّل 2 مرة هذا الأسبوع
• CAUTION فعّل 4 مرات

🧠 قواعد ذاكرة جديدة: 5

🎯 التوصيات
1) قلّل وزن classical_agent من 0.20 إلى 0.15
2) زِد نافذة news_risk.tier1_before من 60 إلى 90
3) أضف فلتر "لا إشارة بعد خسارة -10 في نفس اليوم"

═══════════════════════════════════
```

## اختبارات مُغطّاة (17 اختبار)

| الفئة | الاختبار |
|---|---|
| `WeeklyStats` | `to_prompt_dict_has_all_keys`, `round_to_two_decimals` |
| `collect_stats` | `counts_wins_losses_and_open`, `excludes_old_trades`, `per_day_buckets`, `per_agent_buckets`, `handles_empty_recent_trades` |
| `build_prompt` | `includes_data_block_and_constraints`, `respects_max_chars_config` |
| `split_message` | `short_message_returns_single_chunk`, `exact_limit_returns_single_chunk`, `long_message_splits_correctly`, `no_line_break_falls_back_to_hard_cut` |
| `_extract_recommendations` | `extracts_numbered_recs`, `returns_empty_when_no_section` |
| `generate_report` | `returns_few_trades_message`, `uses_fallback_when_no_ai_service`, `calls_groq_and_uses_response`, `handles_groq_failure_gracefully` |
| `send_to_telegram` | `sends_single_message_when_short`, `splits_long_message_into_parts`, `returns_false_when_telegram_disabled`, `returns_false_when_telegram_none` |
| `save` | `saves_to_storage_path` |

## معايير النجاح

| المعيار | الهدف |
|---|---|
| يولّد التقرير بنجاح | ≥ 95% من الأسابيع |
| التوصيات قابلة للتنفيذ | ≥ 80% |
| Groq يذكر أرقامًا صحيحة | 100% |
| Telegram يستقبل الرسالة | 100% |
| يحفظ JSON بنجاح | 100% |

## التكلفة

~$0.0016/أسبوع (prompt ~3000 input + 1500 output tokens)

## Fallback Strategy

| الحالة | السلوك |
|---|---|
| الأسبوع < `min_trades_for_report` | رسالة "الأسبوع هادئ" بدون توصيات |
| `ai_service = None` | ملخص آلي بدون توصيات |
| Groq fails / rate limit | ملخص آلي + تسجيل الخطأ |
| Telegram fails | يحفظ في `storage/weekly_report.json` فقط |
| Supabase fails | يستخدم `_trade_time_text` fallback + تحذير |

## حدود معروفة

- ⚠️ لا يحسب `halt_activations` إلا إذا Supabase مُتاح (`use_supabase=true`)
- ⚠️ لا يحسب `new_memory_rules` إلا إذا Supabase مُتاح
- ⚠️ تصنيف الجلسة (`by_session`) يعتمد على حقل `session` في trade — لو فارغ يُعتبر "unknown"
- ⚠️ Groq يُولّد التوصيات — لا تُطبَّق تلقائيًا (يحتاج مراجعة بشرية)
