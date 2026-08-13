# إصلاح المصدر المشترك وصدق الخريطة — 2026-08-12

## ما أُصلح بعد التشغيل

- تُجلب شموع MT5 الأصلية مرة واحدة فقط في دورة التحليل.
- تُخزن اللقطة نفسها ذرياً في `storage/shared_market_data.json`.
- يستلم الوكلاء الخمسة الكائن نفسه و`snapshot_id` نفسه؛ لا وكيل يجلب الشموع منفرداً.
- مسار Auction SQLite أصبح مطلقاً من جذر المشروع، فلا يعتمد على working directory.
- أُغلق خلل Planner الذي اعتبر خمسة وكلاء دون 67% كأن بيانات الوكلاء غائبة،
  وسمح بخريطة READY عند `0/5` مؤهلين.
- بوابة تنفيذ المخطط تستخدم الآن محرك Entry/Thesis المشترك نفسه، وأُلغي override
  الخاص الذي كان يسمح لوكيلين بلا Macro/Gemini.
- Telegram يعرض وزن كل وكيل وحالة `QUALIFIED/NOT QUALIFIED`، ويفصل بوضوح بين:
  Map quality وAuthority وExecution admission.
- مصدر البيانات المشترك يظهر مرة واحدة في البطاقة، ولا تُكرر أسماء الفريمات
  بجانب Unified Trend أو أي وكيل آخر.
- خريطة Telegram تُحجب في التشغيل الفعلي إذا لم ينجح shared execution admission.
- لا تُعاد المعايرة في هذا الإصلاح؛ ملفات المعايرة وقاعدة Auction الحالية تُستخدم كما هي.

## الاختبارات

```text
1799 collected
1796 passed
3 skipped
1 warning قديم عن datetime.utcnow()
```

---

# التحويل الكامل إلى Unified Trend + Auction Flow — 2026-08-12

## الحالة

هذه جولة تنفيذ واحدة تحوّل نظام `Nabil-gold` على `demo/mt5` مباشرة إلى خمسة
وكلاء أساسيين. لا يوجد Shadow ولا تشغيل متوازٍ للوكيلين القديمين.

## كتاب الوكلاء النهائي

| المفتاح | الاسم | الوزن |
|---|---|---:|
| `unified_trend` | Unified Trend / وكيل أدلة الاتجاه الموحد | 20% |
| `classical` | Classical / الوكيل الكلاسيكي | 25% |
| `smc` | SMC / وكيل الأموال الذكية | 20% |
| `price_action` | Price Action / وكيل حركة السعر | 20% |
| `auction_flow` | Auction Flow / وكيل تدفق المزاد | 15% |

المجموع `100%`. أُزيل `technical` و`multitimeframe` من التشغيل والتصويت، لكن
تبقى أسماؤهما قابلة للقراءة فقط عند Replay لسجلات تاريخية قديمة.

## كل الوكلاء يقرأون الفريمات الأربعة

تجلب `MarketDataService` كتاباً واحداً مجمداً من MT5 مرة واحدة في الدورة:

```text
M5  → MT5 TIMEFRAME_M5
M15 → MT5 TIMEFRAME_M15
H1  → MT5 TIMEFRAME_H1
H4  → MT5 TIMEFRAME_H4
```

- كل فريم يُجلب أصلياً بصورة مستقلة.
- لا يُشتق M15/H1/H4 من M5.
- كل واحد من الوكلاء الخمسة يقرأ `5m/15m/1H/4H`.
- كل وكيل يصدر اتجاهاً واحداً وثقة واحدة وصوتاً واحداً.
- لا يقوم كل وكيل باتصال API منفصل؛ الجميع يقرأون اللقطة نفسها، لمنع اختلاف
  الأسعار واستهلاك الحصة.
- إذا نقص فريم أصلي في تشغيل VPS، يفشل الوكيل إلى `WAIT 0` ولا يصنع فريماً.

## Unified Trend

يحسب مباشرة، على كل فريم:

- EMA family.
- Swing structure.
- Price momentum.
- MACD histogram/slope.
- RSI14/RSI7/divergence.

تُطبع المسافات بوحدة ATR، ثم تُوحّد كل Feature إلى `-1..+1` بواسطة Signed
Historical Percentile. تُدمج العائلات بالتساوي، ويُحسب:

```text
UnifiedEdge = BaseEdge × Coherence × Coverage
```

ثم تُحوّل قوة Edge إلى ثقة واحدة بواسطة معايرة Logistic تاريخية monotonic.
لا توجد ثقة Technical ولا ثقة MTF ولا متوسط 57/43.

## Classical وSMC وPrice Action

كل وكيل يشغّل منطقه الأصلي بصورة مستقلة على M5/M15/H1/H4، ثم يدمج اتجاهات
الفريمات إلى صوت واحد بتساوٍ، مع عقوبة تلقائية عند اختلاف الفريمات. التفاصيل
التنفيذية الغنية (المناطق والأنماط والـPOI) تبقى من M15، بينما الاتجاه والثقة
النهائيان يعكسان الكتاب الكامل.

## Auction Flow

Tick Manager المجدول نفسه يجمع Tick من `XAUUSD.s` حتى عندما لا توجد صفقة
مفتوحة؛ لا توجد عملية Tick ثانية.

يستخدم الوكيل:

- Up/Down Tick imbalance لآخر 60 ثانية و5 دقائق.
- Tick arrival/activity.
- السبريد كحارس جودة فقط.
- Session VWAP.
- POC / VAH / VAL.
- Initiative acceptance.
- Responsive rejection.
- موقع إغلاق كل فريم أصلي M5/M15/H1/H4 بالنسبة إلى VWAP/POC.

التخزين:

```text
storage/auction_flow.sqlite3
SQLite/WAL
ثوانٍ: 7 أيام
دقائق: 90 يوماً
```

لا توجد فترة انتظار بعد التثبيت. أمر التثبيت يجلب 30 يوماً من Tick history،
يبني الجلسة الحالية والمعايرة، ثم يفعّل النظام. إذا لم يوفر MT5 البيانات
الكافية يفشل التثبيت ويعيد النسخة السابقة.

## الثقة والمعايرة

يبني المثبت ملفين محليين:

```text
storage/model_calibration/unified_trend_v1.json
storage/model_calibration/auction_flow_v1.json
```

المعايرة تستخدم حواجز متناظرة:

```text
±1 ATR على M15
أفق 16 شمعة M15 = 4 ساعات
```

الحد الأدنى:

```text
Unified Trend: 500 نتيجة محسومة
Auction Flow:  300 نتيجة محسومة
```

ملفات المعايرة لها checksum. عند فقدها أو تلفها يكون الوكيل `WAIT 0`، ولا
يُستخدم رقم ثقة يدوي بديل في التشغيل الفعلي.

## قواعد القبول التي لم تتغير

```text
Path 1: ثلاثة وكلاء مؤهلين + net weighted confidence >=72%
Path 2: وكيلان مؤهلان + Macro بنفس الاتجاه >=55%
Path 3: وكيلان مؤهلان + Gemini بنفس الاتجاه >=70%
agent_min_confidence = 67%
```

- نفس الكتاب والأوزان والعتبات تُستخدم في Entry وThesis Exit.
- Macro وGemini تأكيدان خارجيان، وليسا من الوكلاء الخمسة.
- News وRisk حارسان، وليسا صوتين.
- Opposite Entry يبقى Broker-first: تُغلق الصفقة القديمة على MT5 قبل الجديدة.
- SL/TP/BE/Trailing/TP1 partial وقانون actual-fill stop لا تتغير.
- REAL account مرفوض.

## Telegram

تظهر الأسماء الجديدة فقط في الرسائل الجديدة:

```text
🧭 Unified Trend
Classical
SMC
Price Action
🌊 Auction Flow
```

وتظهر لـUnified Trend قيمة Edge وCoherence، ولـAuction Flow حالته مثل
`Initiative Acceptance` أو `Responsive Rejection`. تبقى:

- البادئة `🧪 DEMO ·`.
- Broker-first delivery.
- actual fill.
- SQLite dedup.
- خرائط READY فقط.

## اللوحة المحلية

تعرض اللوحة الوكلاء الخمسة والأوزان الجديدة، وتشرح Unified Trend وAuction
Flow. تبقى الواجهة GET-only، ولا تعرض Tick خاماً أو `.env` أو أسرار MT5 أو
Telegram.

## التثبيت — ZIP واحد وأمر واحد

1. انسخ ZIP النهائي إلى VPS وفكّه في مجلد مستقل.
2. افتح PowerShell كمسؤول داخل المجلد المفكوك.
3. نفّذ فقط:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\INSTALL.ps1"
```

السكريبت:

- يكتشف تلقائياً محاولة v1 غير مكتملة (ملفات جديدة بلا معايرة)، ويستعيد آخر
  Backup وحالات المهام أولاً قبل إعادة المحاولة.
- يعزل اختبارات VPS عن `.env` ومتغيرات Telegram/MT5 الحية، ثم يعيد بيئة Demo
  قبل بناء المعايرة؛ فلا تدخل الاختبارات مسار Broker-first ولا تكتب MagicMock
  في سجل الصفقات.
- يرفض أي حساب غير Demo.
- يحفظ Backup وTask state.
- يوقف Scheduled writers مؤقتاً.
- ينسخ الدفعة كاملة.
- يشغّل الاختبارات.
- يجلب M5/M15/H1/H4 الأصلية وTick history.
- يبني المعايرتين قبل التفعيل.
- يشغّل Tick Manager من Task Scheduler فقط.
- يشغّل Dashboard API ودورة Analysis مجدولة.
- يتحقق من Entry/Exit وTelegram واللوحة.
- يتراجع تلقائياً عند أي فشل.

لا يحتاج Restart للـVPS في الحالة الطبيعية.

## نتائج الاختبار المحلي قبل الحزمة

```text
1799 collected
1796 passed
3 skipped
1 warning قديم عن datetime.utcnow()
```
