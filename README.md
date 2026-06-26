# 🏆 Gold AI Signals — نظام إشارات الذهب الذكي

<div align="center">

![Gold](https://img.shields.io/badge/XAU/USD-Gold-FFD700)
![Python](https://img.shields.io/badge/Python-3.11%2B-green)
![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-Automated-orange)
![Telegram](https://img.shields.io/badge/Telegram-Signals%20%2B%20Reports-blue)
![Tests](https://img.shields.io/badge/Tests-334%20Passed-brightgreen)
![Mode](https://img.shields.io/badge/Mode-Paper%20Trading-yellow)
![Status](https://img.shields.io/badge/Status-Learning%20Phase-orange)

</div>

---

## ⚠️ تنبيه مهم

هذا المشروع **تعليمي / تجريبي فقط** لتحليل وتتبّع تداول الذهب **XAU/USD**.

- لا يُعد توصية مالية أو استثمارية.
- الوضع الحالي: **Paper Trading** فقط.
- لا يتم تنفيذ أوامر حقيقية على أي حساب تداول.
- أي نتائج أو تقارير هي لأغراض الاختبار والتعلّم فقط.

---

## 📌 ملخص سريع

**Gold AI Signals** نظام آلي يعمل عبر GitHub Actions، وتتم جدولة التحليل وتحديث الصفقات خارجياً عبر **cron-job.org** لضمان تشغيل منتظم أكثر، ثم تُرسل الإشارات والتحديثات والتقارير إلى Telegram.

الفكرة الأساسية:

```text
Market Data → 5 Analysis Agents → Weighted Consensus → Risk Filters → Telegram + Supabase
```

أهم ما يفعله النظام:

- يجلب بيانات XAU/USD من Twelve Data.
- يشغّل عدة وكلاء تحليل فني وسلوكي ومخاطر.
- يستخدم 5 وكلاء تحليل مع weighted consensus بدون أي اعتماد على Groq أو API ذكاء خارجي.
- يرسل إشارات منظمة إلى Telegram.
- يتابع الصفقات المفتوحة كل 5 دقائق.
- يحرّك الستوب تلقائياً حسب قواعد محددة.
- يحفظ الصفقات في Supabase.
- يرسل تقريراً يومياً وأسبوعياً.
- يراجع الخسائر بالذكاء الاصطناعي ويستخرج Memory Rules.

---

## ✅ الحالة الحالية

| البند | الحالة |
|---|---|
| التشغيل | GitHub Actions عبر cron-job.org للتحليل والتحديث |
| وضع التداول | Paper Trading |
| مصدر البيانات | Twelve Data |
| القرار النهائي | 5-Agent Weighted Consensus |
| قاعدة البيانات | Supabase |
| رسائل Telegram | إشارات + تحديثات + تقارير + أخطاء |
| تحديث الصفقات | كل 5 دقائق |
| حالة السوق | رسالة كل ساعة |
| التقرير اليومي | 23:00 بتوقيتك المحلي |
| التقرير الأسبوعي | السبت 10:00 صباحاً بتوقيتك المحلي |
| الاختبارات | 334 passed |
| المرحلة | Learning Phase |

---

## 🆕 آخر الإصلاحات والتعديلات المهمة

### 1. إدارة الصفقة والتريلنج

تم اعتماد القاعدة التالية:

```text
عند ربح +100 نقطة → نقل SL إلى الدخول
بعدها يبدأ Trailing مباشرة:
Trailing gap = 100 نقطة
Trailing step = 30 نقطة
تحديث الصفقات = كل 5 دقائق
```

مثال BUY من 4000:

| السعر | الحالة | الستوب |
|---:|---|---:|
| 4010 | +100 نقطة | 4000 دخول |
| 4013 | +130 نقطة | 4003 |
| 4016 | +160 نقطة | 4006 |
| 4019 | +190 نقطة | 4009 |

### 2. رسائل تحديث الصفقات

تحديث الصفقات يعمل كل 5 دقائق، لكن Telegram لا يرسل إلا عند حدوث تغيير فعلي:

- Order Filled
- نقل SL إلى الدخول
- تحريك Trailing Stop
- TP1
- TP2
- SL Hit
- Trailing SL Hit
- Break-even
- Expired / Manual Close

ولا يرسل رسائل مزعجة للأحداث المعلوماتية فقط مثل:

- NEAR_TP1
- LONG_RUNNING
- EXIT_WARNING

### 3. رسالة الإشارة

رسالة الإشارة أصبحت تحتوي سطر إدارة الصفقة داخل خطة الصفقة:

```text
Management: SL → entry after +100 pts · Trail gap 100 pts / step 30 pts · check 5m
```

### 4. تقارير Telegram

- التقرير اليومي مدمج في رسالة واحدة.
- التقرير الأسبوعي يعمل السبت 10:00 صباحاً بتوقيتك.
- Profit Factor يعرض `∞` عندما لا توجد خسائر بدلاً من 0.
- تم تعقيم النصوص الخارجية حتى لا تكسر رموز مثل `< > &` رسائل Telegram HTML.
- رسائل الأخطاء الآن تعرض Workflow / Job / Event / Run ID لتحديد مصدر المشكلة بسرعة.

### 5. إصلاح Scale-in

تم إصلاح مسار التعزيز Scale-in:

- يرسل رسالة Telegram أولاً.
- إذا وصلت الرسالة، يحفظ صفقة التعزيز في قاعدة البيانات.
- إذا فشل الإرسال، لا ينشئ صفقة مخفية.

---

## 🧠 كيف يعمل النظام

### وضع القرار الحالي: 5-Agent Weighted Consensus

- لا يوجد اعتماد على Groq أو أي قرار AI خارجي.
- الوكلاء تحت ثقة 60% يتم تجاهلهم.
- دخول عادي: وكيل واحد قوي بثقة ≥70% أو وكيلان على نفس الاتجاه بمتوسط/ثقة موزونة ≥65%.
- الوكلاء الموافقون يضيفون وزنهم إلى الاتجاه، والوكلاء المعارضون يخصمون وزنهم من قوة الإشارة.
- عكس Daily Bias يحتاج وكيلين مؤهلين على نفس الاتجاه وثقة إشارة ≥75%.

### خط الإشارة

```text
1. cron-job.org يشغّل GitHub Action كل 5 دقائق طوال 24 ساعة في أيام العمل
2. جلب بيانات XAU/USD من Twelve Data
3. تشغيل وكلاء التحليل
4. تشغيل فلاتر الجلسة والأخبار والاتجاه اليومي والمخاطر
5. DecisionAgent يحسب weighted consensus بين الوكلاء الخمسة
6. إذا كانت شروط الإجماع والمخاطر متحققة يقرر BUY / SELL، وإلا WAIT
7. إذا القرار BUY/SELL ومؤهل → إرسال Telegram
8. بعد نجاح الإرسال → حفظ الصفقة في Supabase
9. تحديث الصفقة لاحقاً كل 5 دقائق
```

---

## 🤖 الوكلاء والخدمات

### وكلاء التحليل

| الوكيل | الوظيفة |
|---|---|
| `TechnicalAgent` | مؤشرات فنية: EMA / RSI / MACD / ATR / مستويات |
| `ClassicalAgent` | نماذج كلاسيكية وشموع ودعم/مقاومة |
| `SMCAgent` | Smart Money Concepts / Order Blocks / Liquidity / FVG |
| `PriceActionAgent` | حركة السعر والشموع والزخم |
| `MultiTimeframeAgent` | مقارنة 5m / 15m / 1H / 4H |

### وكلاء الفلترة والسياق

| الوكيل | الوظيفة |
|---|---|
| `TradingSessionAgent` | التحقق من وقت التداول والجلسة |
| `NewsRiskAgent` | فلترة مخاطر الأخبار |
| `DailyBiasAgent` | اتجاه أعلى 4H / Daily Bias |
| `RiskManagementAgent` | حساب SL/TP/R:R/حجم الصفقة |

### وكلاء القرار والإدارة

| الوكيل | الوظيفة |
|---|---|
| `DecisionAgent` | القرار النهائي عبر 5-Agent Weighted Consensus |
| `OpenTradesManager` | متابعة الصفقات المفتوحة وتحريك الستوب والتريلنج |
| `DailyReportAgent` | إحصائيات التقرير اليومي |
| `BaseAgent` | وظائف مشتركة للوكلاء |

---

## 🛡️ الفلاتر والحماية

| الفلتر | الحالة | الوصف |
|---|---|---|
| Weighted Consensus | ✅ فعال | لا إشارة بدون إجماع موزون من الوكلاء |
| Trading Hours | ✅ فعال | يمنع إشارات خارج نافذة التداول |
| News Risk | ✅ فعال | يمنع التداول حول الأخبار عالية الخطورة |
| Daily Bias | ✅ فعال | يسمح بعكس الاتجاه إذا الثقة ≥70% مع وكيل مؤهل واحد، أو ≥65% مع وكيلين مؤهلين بنفس الاتجاه |
| Duplicate Filter | ✅ فعال | يمنع تكرار الإشارات في نفس المنطقة |
| Risk Management | ✅ فعال | يرفض الصفقة إذا فشل R:R أو ATR أو SL |
| Dynamic Risk | 🟡 معطّل مؤقتاً | جاهز للتفعيل بعد مرحلة التعلم |
| Synthetic Data Protection | ✅ فعال | يمنع الإنتاج من استخدام بيانات تجريبية إذا لم يسمح config |

---

## 💰 إدارة المخاطر والصفقة

### قبل الدخول

`RiskManagementAgent` يحسب:

- Entry
- Stop Loss
- TP1 / TP2 / TP3
- R:R
- Position Size تقديري
- Trade Grade

الإعدادات الحالية المهمة:

| الإعداد | القيمة |
|---|---:|
| `min_confidence` | 60% |
| `min_rr_ratio` | 1.5 |
| `min_sl_distance_points` | 300 نقطة |
| `max_rr_ratio` | 4.0 |
| `default_risk_percent` | 1% |
| `max_open_trades` | 50 مؤقتاً |
| `max_daily_signals` | 50 مؤقتاً |

> ملاحظة: رفع `max_open_trades` و `max_daily_signals` مؤقت في مرحلة التعلم لجمع بيانات أكثر.

### أثناء الصفقة

| الحدث | التصرف |
|---|---|
| +100 نقطة ربح | نقل SL إلى الدخول |
| بعد نقل SL | يبدأ Trailing مباشرة |
| كل +30 نقطة إضافية | تحريك SL بمقدار 30 نقطة مع الحفاظ على gap 100 نقطة |
| TP1 | Partial / حماية حسب الخطة |
| TP2 | إغلاق كربح |
| SL | إغلاق كخسارة أو ربح مقفول إذا كان Trailing SL |
| 24 ساعة | Expire إلا إذا الصفقة رابحة ومحمية |

---

## 💬 رسائل Telegram

### 1. رسالة الإشارة

تحتوي على:

- نوع الإشارة BUY/SELL
- السعر والثقة والجودة
- خطة الدخول
- SL / TP1 / TP2
- سطر إدارة الصفقة
- أصوات الوكلاء
- 5-Agent weighted consensus
- أسباب الدخول
- ملاحظات المخاطر
- رقم الصفقة

### 2. رسائل تحديث الصفقة

تصل فقط عند تغيير فعلي:

- نقل SL للدخول
- تحريك Trailing
- TP1 / TP2
- SL / Trailing SL
- BE
- Fill

مثال رسالة تريلنج محسنة:

```text
Trailing Stop Moved - XAU/USD
Stop Loss: 4003.00
Current PnL: +130 pts
TP1 Progress: completed ✅
Trailing stop moved to 4003.00, locking about +30 pts.
Rule: 100-point gap / 30-point step.
```

### 3. Market Status

حالة السوق لا تُرسل من إعدادات المستودع الداخلية، بل من Cron Job خارجي مخصص:

```text
يرسل حالة السوق كل ساعة عبر cron-job.org فقط
```

يفيد في معرفة سبب WAIT أو عدم وصول إشارة، بدون أن تتحول تشغيلات التحليل كل 5 دقائق إلى رسائل مزعجة.

### 4. التقرير اليومي

رسالة واحدة مدمجة تشمل:

- أداء اليوم
- الصفقات المغلقة
- الصفقات المفتوحة
- الأداء حسب الاتجاه
- Learning Update
- AI Trade Review

### 5. التقرير الأسبوعي

تقرير أسبوعي رقمي/تلقائي يوم السبت 10:00 صباحاً بتوقيتك بدون Groq.

### 6. رسائل الأخطاء

أصبحت تحتوي على:

- Workflow
- Job
- Event
- Run ID
- Repo / Ref
- نص الخطأ

---

## ⏰ جدول التشغيل

> التوقيت المحلي المستخدم في الإعدادات: `Asia/Hebron` وهو عملياً مناسب لتوقيتك المحلي الحالي.

| المهمة | الجدولة |
|---|---|
| التحليل والإشارات | كل 5 دقائق 24 ساعة خلال أيام العمل فقط، عبر cron-job.org (`workflow_dispatch`) |
| تحديث الصفقات المفتوحة | كل 5 دقائق أيام العمل عبر cron-job.org (`workflow_dispatch`) — يعمل فعلياً فقط عند وجود صفقة نشطة/معلقة |
| Market Status | كل ساعة عبر cron-job.org فقط (`send_status=true`) |
| التقرير اليومي + Learning | 23:00 بتوقيتك المحلي، الإثنين إلى الجمعة |
| Dashboard | 23:15 بتوقيتك المحلي، الإثنين إلى الجمعة |
| التقرير الأسبوعي | السبت 10:00 صباحاً بتوقيتك المحلي |

### جدولة cron-job.org الحالية

تم إيقاف `schedule` الداخلي في GitHub لملفي التحليل والتحديث، وأصبح التشغيل الأساسي عبر cron-job.org فقط:

#### Analysis — كل 5 دقائق 24 ساعة خلال أيام العمل فقط

توليد الإشارات مسموح 24 ساعة في أيام العمل، مع منع السبت والأحد.

إذا كان cron-job.org مضبوطاً على توقيتك المحلي (`Asia/Hebron` أو `Asia/Jerusalem`) استخدم:

```cron
*/5 * * * 1-5
```

يرسل POST إلى:

```text
https://api.github.com/repos/nabiloashgaqr/Nabil-gold/actions/workflows/analyze.yml/dispatches
```

Body:

```json
{
  "ref": "main",
  "inputs": {
    "send_status": "false"
  }
}
```

> مهم: `send_status=false` يعني يحلل فقط، ولا يرسل Telegram إلا إذا وُجدت صفقة فعلية أو حدث خطأ. الكود نفسه يمنع توليد الإشارات يومي السبت والأحد حتى لو تم تشغيل Workflow بالخطأ.

#### Update Trades — كل 5 دقائق أيام العمل

```cron
*/5 * * * 1-5
```

يرسل POST إلى:

```text
https://api.github.com/repos/nabiloashgaqr/Nabil-gold/actions/workflows/update_trades.yml/dispatches
```

Body:

```json
{
  "ref": "main"
}
```

> تحديث الصفقات يبدأ بفحص خفيف لـ Supabase. إذا لم توجد صفقة نشطة أو أمر معلق (`OPEN/PARTIAL/TP1_HIT/PENDING`) يتوقف قبل checkout/pip/جلب السعر، ولا يرسل Telegram. وإذا وُجدت صفقة، لا يرسل Telegram إلا عند حدوث تغيير فعلي مثل تحريك SL / Trailing / TP / SL / BE / Fill.

#### Market Status — كل ساعة أيام العمل

تم إيقاف حالة السوق الداخلية من `config.json`. حالة السوق تُرسل فقط من Cron Job خارجي مخصص.

```cron
2 * * * 1-5
```

يرسل POST إلى نفس Workflow التحليل:

```text
https://api.github.com/repos/nabiloashgaqr/Nabil-gold/actions/workflows/analyze.yml/dispatches
```

Body:

```json
{
  "ref": "main",
  "inputs": {
    "send_status": "true"
  }
}
```

> هذا الـ Cron Job هو الوحيد المسؤول عن رسالة حالة السوق كل ساعة. تشغيلات التحليل العادية كل 5 دقائق تبقى صامتة عند عدم وجود صفقة.

### Workflows الرئيسية

| Workflow | الملف | الوظيفة |
|---|---|---|
| Gold Analysis Bot | `.github/workflows/analyze.yml` | تحليل وإرسال إشارات — بدون schedule داخلي، يُشغّل عبر cron-job.org |
| Update Open Trades | `.github/workflows/update_trades.yml` | متابعة الصفقات — بدون schedule داخلي، يُشغّل عبر cron-job.org |
| Daily Report & Learning | `.github/workflows/daily_report.yml` | تقرير يومي + تعلم + مراجعة خسائر |
| Weekly AI Performance Report | `.github/workflows/weekly_report.yml` | تقرير أسبوعي |
| Dashboard | `.github/workflows/dashboard.yml` | توليد Dashboard HTML |
| Tests | `.github/workflows/tests.yml` | تشغيل الاختبارات |

---

## 🗄️ قاعدة البيانات Supabase

الملف الأساسي:

```text
supabase_schema_unified.sql
```

الجداول المهمة:

| الجدول | الوظيفة |
|---|---|
| `trades` | الصفقات المفتوحة والمغلقة |
| `signals` | أرشفة الإشارات |
| `agent_weights` | أوزان الوكلاء المتعلمة |
| `learning_history` | تاريخ التعلم |
| `ai_trade_reviews` | معطّل حالياً بعد حذف Groq |
| `ai_memory_rules` | قواعد الذاكرة المستخرجة |
| `daily_reports` | تقارير يومية |
| `weekly_reports` | تقارير أسبوعية |
| `portfolio` | ملخص المحفظة الورقية |

---

## 🔐 Secrets المطلوبة في GitHub

أضفها من:

```text
Repository → Settings → Secrets and variables → Actions
```

| السر | مطلوب | الوظيفة |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | توكن بوت Telegram |
| `TELEGRAM_CHAT_ID` | ✅ | القناة أو المجموعة |
| `SUPABASE_URL` | ✅ | رابط Supabase |
| `SUPABASE_KEY` | ✅ | Service Role Key أو Key مناسب |
| `TWELVE_DATA_API_KEY` | ✅ | بيانات الذهب |

اختياري:

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
GEMINI_API_KEY
```

---

## 🚀 طريقة النشر

1. انسخ المستودع أو ارفعه إلى GitHub.
2. أضف Secrets المطلوبة.
3. افتح Supabase وشغّل:

```sql
supabase_schema_unified.sql
```

4. شغّل Workflow الاختبارات أولاً.
5. شغّل Smoke Tests إن وجدت.
6. شغّل Analysis يدوياً مرة.
7. شغّل Update Trades يدوياً مرة.
8. راقب رسائل Telegram.
9. اترك النظام يعمل Paper Trading لعدة أسابيع قبل أي تشديد.

---

## 🧪 التشغيل المحلي

### تثبيت المتطلبات

```bash
python -m pip install -r requirements.txt
```

### تشغيل الاختبارات

```bash
python -m pytest -q
```

آخر نتيجة مؤكدة:

```text
334 passed, 1 warning
```

### تشغيل تحليل واحد محلياً

```bash
python main.py
```

أو:

```bash
python scripts/run_analysis.py
```

### تشغيل تحديث الصفقات

```bash
python scripts/run_trade_updates.py
```

### تشغيل التقرير اليومي

```bash
python scripts/run_daily_report.py
```

### تشغيل التقرير الأسبوعي

```bash
python scripts/run_weekly_report.py
```

---

## 📁 هيكل المشروع

```text
Nabil-gold/
├── .github/workflows/              # GitHub Actions
├── agents/                         # وكلاء التحليل والقرار وإدارة الصفقات
│   ├── technical_agent.py
│   ├── classical_agent.py
│   ├── smc_agent.py
│   ├── price_action_agent.py
│   ├── multitimeframe_agent.py
│   ├── decision_agent.py
│   ├── risk_management_agent.py
│   └── open_trades_manager.py
├── services/                       # خدمات البيانات والذكاء والتقارير
│   ├── ai_service.py
│   ├── database.py
│   ├── market_data.py
│   ├── telegram_bot.py
│   ├── learning_service.py
│   ├── trade_review.py
│   └── weekly_report.py
├── scripts/                        # سكريبتات التشغيل
│   ├── run_analysis.py
│   ├── run_trade_updates.py
│   ├── run_daily_report.py
│   ├── run_weekly_report.py
│   ├── run_learning.py
│   └── run_trade_review.py
├── tests/                          # اختبارات النظام
├── config.json                     # الإعدادات الرئيسية
├── .env.example                    # مثال المتغيرات
├── requirements.txt
├── supabase_schema_unified.sql
└── README.md                       # هذا الملف الشامل
```

---

## ⚙️ أهم إعدادات config.json

### وضع التشغيل

```json
"trading_mode": "paper",
"operation_mode": "observation"
```

### القرار بدون AI خارجي

```json
"ai_service": {
  "enabled": false,
  "provider": "none",
  "model": "classic-consensus"
}
```

### إدارة الصفقة

```json
"trailing_stop": {
  "enabled": true,
  "early_breakeven_points": 100.0,
  "trailing_distance": 100.0,
  "trailing_step": 30.0
}
```

### إشعارات الصفقات

```json
"notify_on_trade_update": false,
"heartbeat_on_trade_update": false
```

معنى ذلك:

```text
لا رسائل متابعة للصفقات بدون تغيير فعلي.
```

### حالة السوق

```json
"hourly_status": false,
"send_no_signal_updates": false,
"notify_on_blocked_signal": false
```

معنى ذلك:

```text
المستودع لا يرسل حالة السوق تلقائياً. حالة السوق تأتي فقط من cron-job.org كل ساعة عبر send_status=true.
```

---

## 📊 Profit Factor

تم إصلاح مشكلة ظهور Profit Factor = 0 عندما تكون كل الصفقات رابحة ولا توجد خسائر.

القاعدة الحالية:

```text
إذا gross_loss = 0 و gross_profit > 0:
داخلياً = 99.9
عرضاً في Telegram/Dashboard = ∞
```

هذا مطبق في:

- Dashboard
- Daily Report
- Weekly Report
- Telegram Reports

---

## 🧠 Learning + Memory Rules

النظام يتعلم من الصفقات السابقة عبر:

1. حساب أداء الوكلاء.
2. تحديث الأوزان في Supabase.
3. مراجعة الخسائر بالمنطق الرقمي/القواعد عند الحاجة (AI review معطّل حالياً).
4. استخراج Memory Rules.
5. إدخال القواعد النشطة في Prompt القرارات القادمة.

أثناء مرحلة التعلم تم تخفيف بعض القيود لجمع بيانات كافية:

| الإعداد | القيمة الحالية | القيمة الافتراضية |
|---|---:|---:|
| `max_open_trades` | 50 | 3 |
| `max_daily_signals` | 50 | 8 |
| `max_consecutive_losses` | 999 | 3 |
| `dynamic_risk_management.enabled` | false | true |
| `ai_trade_review.max_reviews_per_run` | 20 | 3 |

---

## 🧯 التعامل مع الأخطاء

أي خطأ مهم يرسل Telegram Alert يحتوي:

```text
Workflow
Job
Event
Run ID
Repo/Ref
Error Message
```

كما أن رسائل Telegram تستخدم حماية:

- HTML escaping للنصوص غير الموثوقة.
- Retry عند فشل الإرسال.
- fallback إلى plain text إذا فشل HTML parsing.

---

## 🧭 خارطة الطريق

| الأولوية | التطوير المقترح |
|---|---|
| ⭐⭐ | تشديد مرحلة التعلم تدريجياً بعد جمع عينة كافية |
| ⭐⭐ | تفعيل Dynamic Risk تدريجياً |
| ⭐⭐ | أوامر Telegram تفاعلية مثل `/status` و `/open` |
| ⭐ | GitHub Pages للداشبورد |
| ⭐ | تحسين Backtesting للـ 5-Agent Consensus |
| ⭐ | ضبط تلقائي للصيفي/الشتوي داخل السكريبتات بدلاً من Cron ثابت |

---

## ✅ ملاحظات نهائية

- هذا الملف هو **README الشامل الوحيد** للمشروع.
- أي شرح مهم يجب إضافته هنا بدلاً من إنشاء ملفات Markdown متعددة.
- النظام مخصص حالياً للمراقبة والتعلم وليس للتداول الحقيقي.
- أفضل طريقة لتقييمه: تشغيل Paper Trading لعدة أسابيع ثم مراجعة التقارير الأسبوعية.

---

<div align="center">

**Gold AI Signals — Trade Smart, Test First 🏆**  
آخر تحديث: 2026-06-25

</div>
