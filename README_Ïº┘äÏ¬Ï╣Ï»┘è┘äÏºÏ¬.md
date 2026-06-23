# 📦 تعديلات Nabil-gold — إصلاح وصول الإشارات + فلتر تكرار احترافي

هذه الحزمة تحتوي **كل الملفات المعدّلة فقط** (بنفس مسارات المشروع الأصلي)، إضافة إلى
ملف `all-changes.patch` لتطبيق كل التغييرات دفعة واحدة.

## ✅ طريقة الرفع (اختر واحدة)

**الطريقة 1 — استبدال الملفات مباشرة (الأبسط):**
انسخ الملفات التالية فوق نظيراتها في مستودعك مع الحفاظ على المسارات:
- `.github/workflows/analyze.yml`
- `.github/workflows/update_trades.yml`
- `config.json`
- `scripts/run_analysis.py`
- `tests/test_duplicate_filter.py`  (ملف جديد)
- `tests/test_signal_delivery.py`  (ملف جديد)

**الطريقة 2 — عبر git patch:**
```bash
cd Nabil-gold
git apply all-changes.patch
```

ثم تحقّق:
```bash
python -m pytest tests/ -q     # المتوقع: 266 passed
```

---

## 🔧 ماذا تغيّر ولماذا

### 1) السبب الجذري: الإشارات تصل يدوياً فقط ولا تصل تلقائياً
- **`scripts/run_analysis.py`** — أصبح الترتيب: **أرسِل الإشارة إلى Telegram أولاً، ثم احفظ الصفقة فقط إذا نجح الإرسال.**
  سابقاً كانت الصفقة تُحفظ أولاً والقيمة المُرجَعة من الإرسال مُهمَلة؛ فأي فشل إرسال عابر على
  التشغيل المجدول كان يترك صفقة محفوظة "تسمّم" فلتر التكرار وتحجب كل ما بعدها بصمت.
- **`analyze.yml` / `update_trades.yml`** — `cancel-in-progress: false` كي لا يُقتل تشغيل جارٍ
  على وشك إرسال إشارة عند بدء تشغيل جديد/متأخر.
- تنبيه (بدل خروج صامت) عند فشل جلب بيانات السوق.

### 2) فلتر التكرار أُعيد تصميمه باحترافية (واعٍ بالنتيجة)
مفهومان منفصلان بدل المنطق القديم المبهم:

**أ) حماية تكديس الصفقات المفتوحة**
- تُحجب صفقة بنفس الاتجاه إذا كانت هناك صفقة مفتوحة في **نفس منطقة السعر** (افتراضي 50 نقطة = 5$).
- خيار `block_same_direction_any_price` لمنع أكثر من صفقة واحدة لكل اتجاه بغضّ النظر عن السعر.

**ب) تبريد واعٍ بالنتيجة للصفقات المغلقة حديثاً** (في نفس المنطقة فقط):
| نتيجة الصفقة السابقة | مدة التبريد الافتراضية |
|---|---|
| خسارة (LOSS) | 90 دقيقة (الأطول — تجنّب التكرار الانتقامي) |
| تعادل (BREAKEVEN) | 45 دقيقة |
| ربح (WIN) | 30 دقيقة (الأقصر — اتجاه ناجح) |
- محدود بـ `lookback_hours` (افتراضي 6 ساعات).
- **دخول في منطقة سعر مختلفة لا يُحجب** (إعداد جديد مشروع وليس تكراراً).
- مفاتيح legacy القديمة `lookback_minutes` / `same_direction_price_zone_points` ما زالت مدعومة كـ fallback.

### 3) تطبيق التوصية: وضوح أسباب الحجب على التشغيل المجدول
- **`config.json`** — `notifications.notify_on_blocked_signal = true`، و`should_send_status`
  أصبحت تحترمه؛ فترى الآن سبب حجب أي إشارة (تكرار/مخاطر/حدود) على التشغيل المجدول بدل التخمين.

---

## 🧪 الاختبارات
- `tests/test_signal_delivery.py` — 3 اختبارات (فشل الإرسال = لا حفظ، استثناء = لا حفظ، نجاح = حفظ مرة واحدة).
- `tests/test_duplicate_filter.py` — 15 اختباراً تغطي كل حالات الفلتر الجديد.
- **الإجمالي: 266 passed** (248 الأصلية + 18 جديدة، صفر كسور).

## ⚙️ إعدادات config.json الجديدة (مرجع سريع)
```jsonc
"duplicate_signal_filter": {
  "enabled": true,
  "price_zone_points": 50,
  "open_trade": {
    "block_same_direction_in_zone": true,
    "block_same_direction_any_price": false
  },
  "cooldown": {
    "lookback_hours": 6,
    "after_loss_minutes": 90,
    "after_breakeven_minutes": 45,
    "after_win_minutes": 30
  }
},
"notifications": {
  "notify_on_blocked_signal": true
}
```
