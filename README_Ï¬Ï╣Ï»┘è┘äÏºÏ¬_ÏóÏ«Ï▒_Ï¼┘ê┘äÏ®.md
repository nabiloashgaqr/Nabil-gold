# 📦 تعديلات آخر جولة — إصلاح Supabase 400 + تأكيد تحديث الصفقات

> ⚠️ هذه التعديلات تُطبَّق **فوق** الحزمة الشاملة السابقة (`Nabil-gold-FULL.zip`).
> لا تطبّقها على المشروع الأصلي وحده — تعتمد على ملفات الجولات السابقة.

## 📂 الملفات (5)
- `services/database.py` — معالجة ذكية للعمود الناقص في Supabase.
- `scripts/run_trade_updates.py` — رسالة تأكيد عند التحديث اليدوي بلا أحداث.
- `supabase_schema.sql` — إضافة الأعمدة الناقصة.
- `config.json` — مفتاح `notifications.notify_on_trade_update`.
- `tests/test_db_schema_fallback.py` — اختبارات جديدة (3).

## 🔧 ماذا تغيّر

### 1) إصلاح خطأ `Could not find the 'exit_warning' column ... 400 (PGRST204)`
- **السبب:** جدول `trades` ينقصه أعمدة، والـfallback القديم كان يتقلّص لحزمة legacy
  ضيقة **تُسقط بصمت حقولاً حرجة** (`stop_loss`, `sl_moved_to_entry`, `result`) —
  فكان نقل SL للتعادل/التريلينغ **لا يُحفظ فعلياً**.
- **الحل:** الكود الآن يقرأ اسم العمود الناقص من رسالة الخطأ، **يُسقط فقط العمود
  المفقود ويعيد المحاولة**، محافظاً على باقي الحقول.

### 2) لماذا "لم يصلني شي" عند التحديث؟
سلوك صحيح: الرسالة تُرسل فقط عند **حدث فعلي** (TP/SL/تعادل/تريلينغ/اقتراب). الآن على
**التشغيل اليدوي** (أو `notify_on_trade_update:true`) تصلك **رسالة تأكيد مختصرة**
بحالة كل صفقة و PnL حتى بدون حدث.

## 🛠️ خطوة مهمة في Supabase
شغّل **`supabase_schema.sql`** في Supabase SQL Editor — يضيف الأعمدة الناقصة بأمان
(`ADD COLUMN IF NOT EXISTS`): `exit_warning`, `management_phase`,
`max_favorable_excursion`, `max_adverse_excursion`.

## 🚀 الرفع
استبدل الملفات الـ5 فوق نظيراتها، أو:
```bash
cd Nabil-gold
git apply schema-fix-changes.patch
python -m pytest tests/ -q     # المتوقع: 286 passed
```
