# SmartSignal Pro / Nabil Gold

نظام إشارات ومتابعة أداء آلي متعدد الأصول، مخصص حالياً لـ **الذهب XAU/USD** و **النفط WTI/USD**، يعتمد على طبقة تحليل متعددة الوكلاء، مراجعة مستقلة بالذكاء الاصطناعي، إدارة مخاطر، متابعة صفقات، تقارير أداء، وتعامل ذكي مع الأخبار والماكرو.

> **تنبيه مهم:** النظام مخصص للإشارات، التحليل، المتابعة، وقياس الأداء. التداول ينطوي على مخاطر، ولا توجد أي ضمانات للربح. التشغيل الحالي Paper / Signal-following وليس تنفيذ تداول حقيقي مباشر.

---

## الفكرة المختصرة

SmartSignal Pro هو نظام تداول تحليلي يعمل كطبقة قرار ومتابعة، وليس مجرد مؤشر أو Expert Advisor تقليدي.

- يراقب السوق ويبحث عن فرص كل **5 دقائق**.
- يدعم الذهب والنفط مع حساب نقاط مختلف لكل أصل.
- يستخدم عدة وكلاء تحليل بدل الاعتماد على إشارة واحدة.
- يطبق توافق موزون بين الوكلاء قبل إرسال أي إشارة.
- يضيف مراجعة مستقلة من Gemini عند توفرها.
- يراعي الأخبار، الجلسات، الاتجاه اليومي، والمخاطر.
- يحدث الصفقات المفتوحة ويحميها عبر Breakeven وTrailing.
- يخزن الصفقات والتقارير في Supabase.
- يعرض الأداء والجودة عبر Dashboard على Vercel.
- يرسل الإشارات والتحديثات والتقارير عبر Telegram.
- يدعم التعلم وتحليل جودة الأداء بعد إغلاق الصفقات.

---

## كيف يعمل النظام

```text
Twelve Data Market Data
→ Verified Market Snapshot
→ Core Analysis Agents
→ Daily Bias / Session / News / Macro Context
→ Weighted Consensus Decision
→ Risk Management + SL/TP/RR
→ Gemini Independent Review
→ Telegram Signal
→ Supabase Trade Record
→ Open Trade Management
→ Daily / Weekly Reports
→ Learning + Attribution
→ Performance Dashboard
```

---

## أهم التطويرات الحديثة

### Phase 4 — Verification & Observability

- تحسين تتبع حالة Gemini في التحليل والتقارير.
- منع ظهور مخرجات Gemini العامة أو الضعيفة.
- تسجيل واضح لحالة Gemini:
  - added
  - suppressed
  - unavailable
  - skipped
- إظهار مراجعة Gemini داخل إشارات BUY/SELL عند توفرها.

### Phase 5 — Data Enrichment

تمت إضافة بيانات جودة تحفظ مع الصفقة عند الدخول، مثل:

- Planned Risk Points
- Planned TP2 Points
- Planned RR
- Session Label / Quality
- Entry Day / Entry Hour
- News Status / News Risk
- Volatility Regime
- Trend Strength
- Daily Bias at Entry
- Final PnL Points

وتستخدم هذه البيانات في:

- التقرير اليومي.
- التقرير الأسبوعي.
- تقرير التعلم.
- Dashboard.
- Backfill للصفقات القديمة.

### Phase 6 — Final UX / Output Polish

- تحسين رسالة Telegram.
- إضافة Planned RR.
- إضافة Risk / Context.
- تقليل التكرار والتفاصيل الداخلية.
- تحويل التقرير الأسبوعي إلى Executive Report.
- إضافة Edge Snapshot في Dashboard.
- إضافة تحليلات:
  - RR Capture
  - Best Session
  - News Impact
  - Best Regime

### Marketing Injection

تم تحديث صفحة SmartSignal Pro والاشتراكات لعرض المزايا الجديدة بشكل تسويقي مختصر، بدون حذف الأقسام القديمة وبدون كشف أسرار تقنية.

### Agent Upgrade Phase A

- إضافة Verified Snapshot Layer.
- إخراج Structured Evidence لكل وكيل.
- إضافة Reason Codes.
- إضافة Confidence Breakdown.
- حفظ `agent_structured` داخل القرار.

### Agent Upgrade Phase B

- Technical Agent أصبح Regime-aware.
- Classical Agent أصبح يقيس:
  - Pattern Quality
  - Breakout Quality
  - Retest State
- Multi-Timeframe Agent أصبح يخرج:
  - Entry Permission
  - Timing State
  - Failure Mode
- Daily Bias Agent أصبح يدعم:
  - Bias Persistence
  - Strength Band
  - Reason Codes

### Agent Upgrade Phase C

- إضافة Macro / Fundamental Agent.
- فصل الأخبار إلى:
  - `event_risk`
  - `macro_direction`
- إضافة Entry Attribution:
  - Primary Entry Driver
  - Supporting Agents
  - Opposing Agents
  - Timing State
  - Entry Permission
  - Failure Mode
  - Macro Direction
  - Daily Bias
- التعلم أصبح يستفيد من Attribution بدل توزيع عام فقط.

### Hourly Macro Context

- إضافة تحديث ماكرو كل ساعة عبر Workflow مستقل.
- يستخدم Twelve Data باستهلاك منخفض.
- يقرأ رموز Forex/US proxy مثل:
  - `EUR/USD`
  - `GBP/USD`
  - `USD/JPY`
  - `USD/CNY`
  - `SPY`
- يقدر:
  - USD trend
  - DXY proxy trend
  - Risk sentiment
- لا يؤثر على القرار الأساسي.
- يستخدم فقط في:
  - جودة الإشارة.
  - التعلم.
  - Attribution.

### Gemini Visibility in Signals

أصبح قسم Gemini يظهر دائماً في رسالة الإشارة:

- إذا كان متاحاً: يظهر الرأي.
- إذا تم تجاهله لأنه عام: يظهر أنه Skipped.
- إذا لم يكن متاحاً: يظهر Offline / Not available بدون كشف تفاصيل تقنية.

---

## الأصول المدعومة

| الأصل | الرمز الداخلي | الاسم | حجم النقطة | عدد الكسور |
|---|---|---|---:|---:|
| Gold | `XAU/USD` | Gold | `0.10` | 2 |
| Oil | `WTI/USD` | WTI Crude Oil | `0.01` | 2 |

### أسماء النفط المقبولة داخلياً

يتم تحويل الأسماء التالية إلى:

```text
WTI/USD
```

- `WTI`
- `USOIL`
- `OIL`
- `WTICO_USD`

---

## حساب النقاط

النظام يخزن الربح والخسارة والمسافات بوحدة **points**.

### الذهب XAU/USD

- `1 point = 0.10$`
- `10 points = 1.00$`
- `300 points = 30.00$`

### النفط WTI/USD

- `1 point = 0.01$`
- `100 points = 1.00$`
- `120 points = 1.20$`

---

## إعدادات الذهب الحالية

```json
{
  "symbol": "XAU/USD",
  "point_size": 0.10,
  "min_sl_distance_points": 300,
  "early_breakeven_points": 100,
  "trailing_distance": 100,
  "trailing_step": 30,
  "duplicate_zone_points": 50
}
```

| الإعداد | النقاط | حركة السعر |
|---|---:|---:|
| Minimum SL | 300 | 30.00$ |
| Early Breakeven | 100 | 10.00$ |
| Trailing Distance | 100 | 10.00$ |
| Trailing Step | 30 | 3.00$ |
| Duplicate Zone | 50 | 5.00$ |

---

## إعدادات النفط الحالية

```json
{
  "symbol": "WTI/USD",
  "point_size": 0.01,
  "min_sl_distance_points": 120,
  "early_breakeven_points": 70,
  "trailing_distance": 70,
  "trailing_step": 25,
  "duplicate_zone_points": 100
}
```

| الإعداد | النقاط | حركة السعر |
|---|---:|---:|
| Minimum SL | 120 | 1.20$ |
| Early Breakeven | 70 | 0.70$ |
| Trailing Distance | 70 | 0.70$ |
| Trailing Step | 25 | 0.25$ |
| Duplicate Zone | 100 | 1.00$ |

---

## وكلاء التحليل الأساسيون

| الوكيل | الدور |
|---|---|
| Technical Agent | يقرأ المؤشرات، الترند، الزخم، الفوليوم النسبي، وحالة السوق. |
| Classical Agent | يراقب الدعوم والمقاومات، الترندلاين، النماذج، الاختراق، وإعادة الاختبار. |
| SMC Agent | يركز على السيولة، Order Blocks، FVG، Market Structure، ومناطق الاهتمام. |
| Price Action Agent | يقرأ الشموع، الرفض السعري، وسلوك المشترين والبائعين. |
| Multi-Timeframe Agent | يقارن 5m / 15m / 1H / 4H لتأكيد الصورة الأكبر والتوقيت. |

---

## وكلاء وسياقات إضافية

| المكون | الدور |
|---|---|
| News Risk Agent | يحدد خطر الأخبار وهل يوجد حظر أو تحذير. |
| Macro Fundamental Agent | يقرأ سياق الدولار والمخاطر كعامل جودة وتعلم. |
| Daily Bias Agent | يحدد الاتجاه اليومي ويمنع التهور عكس الاتجاه. |
| Trading Session Agent | يحدد جودة الجلسة وسماح الإشارات. |
| Risk Management Agent | يحسب الدخول والوقف والأهداف ونسبة R:R. |
| Dynamic Risk Manager | يخفف أو يمنع المخاطرة عند سوء الأداء أو ظروف معينة. |
| Open Trades Manager | يتابع الصفقات المفتوحة ويحدث SL/TP/Trailing. |
| Learning Service | يتعلم من الصفقات المغلقة ويحدث أوزان الوكلاء. |
| Gemini Review Service | مراجعة مستقلة للإشارة والسوق والأخبار والتقارير. |

---

## Verified Snapshot Layer

قبل تشغيل الوكلاء يتم بناء Snapshot موحد للسوق يحتوي على:

- السعر الحالي.
- آخر شمعة OHLC.
- EMA 8 / 21 / 50 / 100 / 200.
- RSI.
- MACD Histogram.
- ATR.
- Bollinger Bands.
- أقرب دعم ومقاومة.
- جودة البيانات.
- عدد الشموع.
- هل البيانات حديثة أو متأخرة.

الهدف هو أن كل وكيل يحلل من مصدر موحد بدلاً من قراءات متفرقة.

---

## قواعد القرار

- لا يتم إرسال الإشارة بناءً على مؤشر واحد.
- يجب وجود توافق بين عدة وكلاء.
- يتم استخدام **5-Agent Weighted Consensus**.
- الحد الأدنى للثقة العامة: `65%`.
- الصفقات عكس الاتجاه اليومي تحتاج ثقة أعلى: `75%`.
- الوكلاء أقل من `60%` ثقة يتم استبعادهم من التأثير.
- الحد الأدنى لعدد الوكلاء المتفقين: `2`.
- الأخبار الخطرة يمكن أن تمنع الإشارة.
- الماكرو حالياً لا يفتح ولا يمنع الصفقة وحده.

---

## ما دور Gemini؟

Gemini يعمل كمراجعة مستقلة وليس كمحرك القرار الأساسي.

يستخدم في:

- مراجعة سياق السوق.
- مراجعة الإشارة.
- تفسير الأخبار.
- مراجعة التقرير اليومي.
- مراجعة التقرير الأسبوعي.
- مراجعة التعلم.

إذا كانت مخرجات Gemini عامة أو غير مفيدة، يتم تجاهلها وإظهار حالة مختصرة في الرسالة.

في رسالة الإشارة يظهر قسم:

```text
GEMINI INDEPENDENT REVIEW
```

ويعرض إحدى الحالات:

- Opinion متى كان متاحاً.
- Skipped إذا لم يضف معلومة مفيدة.
- Offline / Not available إذا لم يتوفر في تلك الجولة.

---

## الماكرو والفاندامنتال

تمت إضافة طبقة ماكرو خفيفة وآمنة للكوتة.

### مصادر الماكرو المدعومة

1. `MACRO_CONTEXT_JSON`
2. `config["macro_context"]`
3. `storage/macro_context.json`
4. جدول `macro_context` في Supabase
5. تحديث تلقائي كل ساعة عبر Twelve Data proxy

### Workflow الماكرو

```text
.github/workflows/macro_context.yml
```

يعمل كل ساعة تقريباً ويستهلك افتراضياً:

```text
5 credits/hour ≈ 120 credits/day
```

من حد Twelve Data المجاني:

```text
800 requests/day
```

### رموز الماكرو الافتراضية

- `EUR/USD`
- `GBP/USD`
- `USD/JPY`
- `USD/CNY`
- `SPY`

### حدود الماكرو الحالية

- DXY الحقيقي غير مضمون على الخطة المجانية.
- US10Y / Fixed Income غالباً يحتاج خطة أعلى.
- Fed tone و CPI surprises لا يتم اختراعها.
- عند عدم توفر البيانات يرجع النظام إلى `NEUTRAL / UNKNOWN` بأمان.

---

## إدارة المخاطر

- احتساب SL بناءً على ATR / دعم / مقاومة / SMC / منطقة الدخول.
- تطبيق حد أدنى لمسافة الوقف حسب الأصل.
- إعادة حساب الأهداف عند توسيع الوقف للحفاظ على R:R.
- الحد الأدنى لنسبة R:R: `1.5`.
- سقف R:R لتجنب أهداف غير واقعية: `4.0`.
- دعم تحديد حجم الصفقة حسب رأس المال ونسبة المخاطرة.
- حساب قيمة النقطة يختلف حسب الأصل.

---

## إدارة الصفقة بعد الدخول

- متابعة الصفقات المفتوحة كل 5 دقائق.
- نقل SL إلى الدخول عند تحقق ربح مبكر:
  - الذهب: `+100 points`.
  - النفط: `+70 points`.
- تفعيل Trailing Stop بعد حماية الصفقة.
- التريلينغ لا يتحرك إلا عند تحقق step مناسب.
- دعم TP1 و TP2 و TP3.
- دعم الإغلاق الجزئي عند TP1.
- دعم حماية الصفقات الرابحة من الإغلاق الزمني إذا أصبح الوقف مؤمناً.
- إرسال تحديثات Telegram عند الأحداث المهمة.

---

## أنواع أحداث الصفقة

- `PENDING`
- `OPEN`
- `PARTIAL`
- `TP1_HIT`
- `TP2_HIT`
- `SL_HIT`
- `BE_HIT`
- `EXPIRED`
- `MANUAL_CLOSE`
- `CLOSED`

### ملاحظة مهمة حول SL_HIT

`SL_HIT` لا يعني دائماً خسارة.

قد يكون:

- خسارة إذا كان PnL سالباً.
- تعادل إذا كان SL عند الدخول.
- ربح إذا كان SL قد تحرك مع التريلينغ.

لذلك يعتمد النظام على قيمة PnL الفعلية وليس اسم الحالة فقط.

---

## Entry Attribution

كل صفقة تحفظ الآن سياق سبب الدخول، ويشمل:

- Primary Entry Driver.
- Supporting Agents.
- Opposing Agents.
- Entry Permission.
- Timing State.
- MTF Failure Mode.
- Pattern Quality.
- Breakout Quality.
- Technical Regime.
- Event Risk.
- Macro Direction.
- Daily Bias.
- Agent Reason Codes.

يستخدم هذا لاحقاً لمعرفة:

- من كان السبب الأقوى للدخول؟
- هل فشل التوقيت؟
- هل كانت الصفقة ضد الماكرو؟
- هل كان الدايلي بايس داعماً أو معارضاً؟
- أي وكيل أفضل في أي Regime؟

---

## التعلم وتحليل جودة الأداء

Learning Service يستخدم الصفقات المغلقة لتحليل:

- أداء الوكلاء.
- تغير الأوزان.
- أفضل وأسوأ جلسات.
- أداء الأيام.
- RR Capture.
- News Proximity.
- Regime Fit.
- Macro Bias Impact.
- Entry Driver Impact.

ويولد توصيات مثل:

- رفع أو خفض ثقة وكيل.
- الحذر من أخبار معينة.
- تحسين الخروج إذا كان RR Capture ضعيفاً.
- مراقبة الجلسات أو الأنظمة الضعيفة.

---

## الجداول الزمنية

| المهمة | التكرار |
|---|---|
| تحليل فرص جديدة | كل 5 دقائق |
| تحديث الصفقات المفتوحة | كل 5 دقائق |
| تحديث سياق الماكرو | كل ساعة |
| التقرير اليومي | 23:00 |
| التقرير الأسبوعي | السبت 10:00 |
| الاختبارات | Push / PR / Manual |

### نافذة التداول

- المنطقة الزمنية: `Asia/Hebron`.
- توليد الإشارات الجديدة: من 03:00 إلى 22:00.
- إدارة الصفقات المفتوحة يمكن أن تستمر خارج نافذة الإشارات لحماية الصفقة.

---

## مصادر البيانات

- المصدر الأساسي للسوق: **Twelve Data**.
- يتم جلب بيانات 5m ثم إعادة بناء الفريمات الأخرى عند تفعيل resampling.
- الفريمات المستخدمة:
  - `5m`
  - `15m`
  - `1H`
  - `4H`
- الماكرو يستخدم تحديثاً منفصلاً كل ساعة لحماية الكوتة.
- لا يسمح باستخدام بيانات synthetic في الإنتاج إلا إذا تم تفعيله صراحة.

---

## التخزين وقاعدة البيانات

- المزود: **Supabase**.
- يتم تخزين:
  - الإشارات.
  - الصفقات.
  - حالة الصفقة.
  - PnL بالنقاط.
  - بيانات الإثراء.
  - Entry Attribution.
  - Macro Context.
  - التقارير اليومية والأسبوعية.
  - بيانات الأداء والتعلم.

ملف schema الرئيسي:

```text
supabase_schema_unified.sql
```

### جداول مهمة

- `trades`
- `daily_reports`
- `weekly_reports`
- `agent_weights`
- `risk_settings`
- `macro_context`

---

## Telegram

يستخدم Telegram لإرسال:

- إشارات الدخول.
- تفاصيل Entry / SL / TP.
- Planned RR.
- قوة الإشارة.
- الأصوات المؤهلة وغير المؤهلة.
- Risk / Context.
- Daily Bias.
- Macro Context.
- Gemini Independent Review.
- Primary Driver / Timing / Permission.
- تحديثات نقل الوقف.
- تحديثات التريلينغ.
- TP / SL / BE.
- التقارير اليومية والأسبوعية.
- رسائل الاختبار والتنبيه.

### ترتيب رسالة الإشارة

```text
Signal Header
Trade Plan
Agent Votes
Risk / Context
Gemini Independent Review
Gemini News Check
Why This Trade
Footer + Trade ID
```

---

## Dashboard

ملفات اللوحة:

```text
dashboard/index.html
dashboard/style.css
dashboard/app.js
api/dashboard.js
```

### أقسام اللوحة

- Dashboard
- Reports
- Agents
- SmartSignal Pro
- Plans & Payment

### تعرض اللوحة

- عدد الصفقات المغلقة.
- Win Rate.
- Net Points.
- Profit Factor.
- Best / Worst Trade.
- Average Trade.
- Expectancy.
- Daily PnL.
- Cumulative PnL.
- الأداء حسب الجلسة.
- الأداء حسب الأصل.
- الأداء حسب Regime.
- الأداء حسب News Risk.
- RR Capture.
- Best Session.
- News Impact.
- Best Regime.
- جدول الصفقات المغلقة.
- التقارير اليومية والأسبوعية.
- أداء الوكلاء.

---

## صفحة SmartSignal Pro التسويقية

تشرح بشكل مختصر وجذاب:

- المشكلة النفسية في التداول.
- أن النظام آلي لمتابعة الإشارات.
- كيف يعمل النظام من المراقبة حتى التقارير.
- طبقات التحليل الست.
- المراجعة المستقلة.
- Risk Guardrails.
- Live Management.
- Performance Quality.
- Executive Reports.
- الفرق بين النظام و Expert Advisor التقليدي.
- الأسئلة الشائعة.

---

## صفحة Plans & Payment

تحتوي على:

- السعر الشهري: `$100`.
- سعر 3 أشهر: `$200`.
- مزايا الاشتراك.
- الدفع عبر USDT TRC20.
- عنوان المحفظة مع زر نسخ.
- إرسال صورة الدفعة.
- إرسال اسم مستخدم Telegram.
- التفعيل عبر رابط دعوة خاص أو إضافة يدوية.

---

## الفرق عن Expert Advisor EA

- النظام ليس EA واحداً باستراتيجية واحدة.
- هو طبقة ذكاء تداول متعددة الوكلاء والاستراتيجيات.
- يعمل حالياً كنظام إشارات ومتابعة أداء.
- يمكن تطويره لاحقاً نحو تنفيذ آلي اختياري على حساب المستخدم.
- المتداول حالياً يحتفظ بقرار التنفيذ.
- النظام يوفر تحليل، تنبيهات، إدارة صفقات، وتقارير أداء.

---

## التشغيل الآلي

يعمل عبر GitHub Actions و cron-job.org.

### Workflows رئيسية

- `.github/workflows/analyze.yml`
- `.github/workflows/update_trades.yml`
- `.github/workflows/macro_context.yml`
- `.github/workflows/daily_report.yml`
- `.github/workflows/weekly_report.yml`
- `.github/workflows/dashboard.yml`
- `.github/workflows/tests.yml`
- `.github/workflows/telegram_test.yml`
- `.github/workflows/backtest.yml`

---

## ملفات الدخول الرئيسية

```text
main.py
scripts/run_analysis.py
scripts/run_trade_updates.py
scripts/update_macro_context.py
scripts/run_daily_report.py
scripts/run_weekly_report.py
scripts/run_learning.py
scripts/backfill_trade_enrichment.py
scripts/generate_dashboard.py
```

---

## هيكل المشروع

```text
Nabil-gold/
├── agents/                 # وكلاء التحليل والقرار وإدارة الصفقات
├── services/               # البيانات، قاعدة البيانات، Telegram، التقارير
├── scripts/                # نقاط تشغيل GitHub Actions والمهام
├── utils/                  # أدوات مساعدة، المؤشرات، تعريف الأصول
├── tests/                  # اختبارات المشروع
├── dashboard/              # واجهة اللوحة
├── api/                    # API الخاص باللوحة على Vercel
├── .github/workflows/      # تشغيل آلي واختبارات
├── config.json             # الإعدادات الرئيسية
├── supabase_schema_unified.sql
└── README.md
```

---

## متغيرات البيئة المطلوبة

| المتغير | الاستخدام |
|---|---|
| `TWELVEDATA_API_KEY` | بيانات السوق وتحديث الماكرو الخفيف |
| `TELEGRAM_BOT_TOKEN` | إرسال Telegram |
| `TELEGRAM_CHAT_ID` | قناة/محادثة Telegram |
| `SUPABASE_URL` | قاعدة البيانات |
| `SUPABASE_KEY` | مفتاح Supabase |
| `SUPABASE_SERVICE_KEY` | API آمن للوحة على Vercel عند الحاجة |
| `GEMINI_API_KEY` | مراجعات Gemini المستقلة |
| `MACRO_CONTEXT_JSON` | سياق ماكرو يدوي اختياري |

---

## أوامر التشغيل المحلي

```bash
pip install -r requirements.txt
python -m pytest -q
python scripts/run_analysis.py
```

### تحديث الماكرو محلياً

```bash
python scripts/update_macro_context.py
```

### Backfill بيانات الإثراء

```bash
python scripts/backfill_trade_enrichment.py --dry-run
python scripts/backfill_trade_enrichment.py
```

---

## الاختبارات

الأمر الرئيسي:

```bash
python -m pytest -q
```

آخر حالة مستقرة بعد التحديثات الحديثة:

```text
350 passed
```

---

## ملاحظات مهمة

- النظام Paper / Signal-following وليس ضمان ربح.
- النتائج تعتمد على السوق والتنفيذ وإدارة المخاطر.
- يجب اختبار أي إعدادات جديدة على فترة كافية قبل اعتمادها.
- Gemini مراجعة مستقلة وليس محرك القرار الأساسي.
- الماكرو الحالي عامل جودة وتعلم وليس بوابة دخول.
- تحديث الماكرو كل ساعة لتقليل استهلاك Twelve Data.
- عند غياب Gemini أو الماكرو، النظام يستمر بأمان ولا يكسر التحليل.
- الصفقات المفتوحة التي تغلق اليوم تُحسب في يوم الإغلاق لا يوم الفتح.

---

## ملخص سريع

- **SmartSignal Pro** = إشارات + متابعة صفقات + تقارير أداء + تعلم.
- **الأصول** = Gold + WTI Oil.
- **التحليل** = عدة وكلاء + توافق موزون + Snapshot موحد.
- **المراجعة** = Gemini Independent Review.
- **السياق** = أخبار + جلسات + Daily Bias + Macro.
- **التحديث** = كل 5 دقائق للفرص والصفقات، وكل ساعة للماكرو.
- **الإدارة** = SL / TP / Breakeven / Trailing.
- **الجودة** = RR Capture / Session Edge / News Impact / Regime Fit.
- **الواجهة** = Dashboard + Reports + Agents + Plans.
- **التواصل** = Telegram.
- **التخزين** = Supabase.
- **التشغيل** = GitHub Actions + cron-job.org.
