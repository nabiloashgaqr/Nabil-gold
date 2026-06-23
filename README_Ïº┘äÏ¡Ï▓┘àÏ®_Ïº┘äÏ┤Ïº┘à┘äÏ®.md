# 📦 Nabil-gold — الحزمة الشاملة النهائية (كل التعديلات معاً)

> ⚠️ **مهم:** هذه الحزمة **تستبدل وتغني عن** كل الحزم السابقة
> (`Nabil-gold-modified.zip` و `Nabil-gold-gold-band.zip`).
> **لا تطبّق الحزم القديمة معها.** هذه وحدها كافية ومتّسقة بذاتها.

## ❗ سبب الخطأ الذي واجهته
رسالة الخطأ:
```
AttributeError: 'DatabaseService' object has no attribute 'new_trade_id'
9 failed
```
حدثت لأن حزمة `gold-band` كانت تحتوي **ملف الاختبارات الجديد فقط** (يستدعي `new_trade_id`
والدخول الذكي)، بينما **الكود الأساسي** (`database.py`, `telegram_bot.py`,
`risk_management_agent.py`, `decision_agent.py`) كان في الحزمة السابقة.
فعند رفع `gold-band` وحدها، الاختبارات تبحث عن كود غير موجود → فشل.

**الحل:** هذه الحزمة الشاملة تحتوي **كل الملفات الـ12 معاً** فلا يحدث أي عدم تطابق.

---

## ✅ طريقة الرفع (اختر واحدة)

**الطريقة 1 — استبدال الملفات مباشرة:**
انسخ كل الملفات التالية فوق نظيراتها في مستودعك مع الحفاظ على المسارات:
```
.github/workflows/analyze.yml
.github/workflows/update_trades.yml
agents/decision_agent.py
agents/risk_management_agent.py
config.json
scripts/run_analysis.py
services/database.py
services/telegram_bot.py
tests/test_duplicate_filter.py        (جديد)
tests/test_signal_delivery.py         (جديد)
tests/test_signal_formatting.py       (جديد)
tests/test_smart_entry_and_id.py      (جديد)
```

**الطريقة 2 — عبر git patch (من نسخة المشروع الأصلية):**
```bash
cd Nabil-gold
git apply all-changes.patch
```

**تحقّق دائماً بعد الرفع:**
```bash
python -m pytest tests/ -q     # المتوقع: 283 passed
```

---

## 🔧 كل التعديلات المُضمّنة

### 1) إصلاح: الإشارات تصل يدوياً فقط ولا تصل تلقائياً
- أرسِل الإشارة أولاً، واحفظ الصفقة فقط عند نجاح الإرسال.
- `cancel-in-progress: false` في الـworkflows.
- تنبيه بدل خروج صامت عند فشل جلب البيانات.

### 2) فلتر تكرار احترافي واعٍ بالنتيجة
- حماية تكديس الصفقات المفتوحة (نفس منطقة السعر).
- تبريد بعد الإغلاق: خسارة 90د · تعادل 45د · ربح 30د.

### 3) وضوح أسباب الحجب على التشغيل المجدول
- `notifications.notify_on_blocked_signal = true`.

### 4) تنسيق تقرير الإشارة — نظيف واحترافي
- إصلاح باغ `\n` الحرفي، أقسام واضحة، جدول أصوات بعلامات اتجاه، footer مرتب.

### 5) إصلاح `PENDING_...` في معرّف الصفقة
- يُولَّد المعرّف الحقيقي قبل الإرسال ويُعاد استخدامه عند الحفظ.

### 6) دخول ذكي تلقائي (Market / Limit / Stop) — مُعاير لحركة الذهب
- Limit عند دعم/مقاومة ضمن نطاق ارتداد منطقي، وإلا Market.
- **النطاق مُعاير للذهب: 60–350 نقطة (6$–35$)** — 1 نقطة = 0.10$.
  - 60 نقطة = ارتداد قريب · 200–350 نقطة = المتوسط الطبيعي.
- يظهر بوضوح: نوع الأمر + سعر الدخول + السعر الحالي + المسافة + السبب.

### 7) عرض الأهداف TP1/TP2 كل واحد بسطر منفصل بدون R:R

---

## 🧪 الاختبارات (الإجمالي: 283 passed)
| الملف | عدد |
|---|---|
| `test_signal_delivery.py` | 3 |
| `test_duplicate_filter.py` | 15 |
| `test_signal_formatting.py` | 6 |
| `test_smart_entry_and_id.py` | 11 |
| الاختبارات الأصلية | 248 |
| **المجموع** | **283** |
